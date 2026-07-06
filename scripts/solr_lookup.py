"""
solr_lookup.py — verify citations against the OpenAlex Solr index on galaxy.

Uses the openalexWorks Solr collection (492M works) as an alternative to
the Crossref MongoDB lookup. Works immediately — no text index build required.

Endpoint: http://galaxy:8983/solr/openalexWorks/select

Usage as a module:
    from solr_lookup import SolrLookup
    with SolrLookup() as lookup:
        result = lookup.by_citation(obj)   # obj needs .doi, .title, .year
        # or, for bulk (much faster):
        results = lookup.by_title_batch(objs)

Usage standalone (test):
    python3 solr_lookup.py
"""
import re
import types
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from typing import Optional
from urllib.parse import urlencode, quote

import requests

SOLR_URL = "http://galaxy:8983/solr/openalexWorks/select"
SIMILARITY_THRESHOLD = 0.85


class MatchMethod(str, Enum):
    DOI             = "doi"
    TITLE_YEAR      = "title_year"
    TITLE_ONLY      = "title_only"
    NOT_FOUND       = "not_found"
    # Crossref fallback methods (used when OpenAlex Solr does not find a citation)
    XREF_DOI        = "xref_doi"
    XREF_TITLE_YEAR = "xref_title_year"
    XREF_TITLE_ONLY = "xref_title_only"
    # Vector re-ranking fallback (Phase 4: semantic similarity via sentence-transformers)
    VECTOR          = "vector"


@dataclass
class SolrResult:
    found: bool
    method: MatchMethod
    record: Optional[dict] = None
    confidence: float = 0.0


def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _get_title(doc: dict) -> Optional[str]:
    t = doc.get("title")
    if isinstance(t, list) and t:
        return t[0]
    if isinstance(t, str):
        return t
    return None


def _solr_get(params: dict, timeout: int = 8) -> dict:
    """Fire a GET request to Solr and return the parsed JSON response."""
    # Always disable facets — the collection has expensive default facets
    params.setdefault("facet", "false")
    resp = requests.get(SOLR_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _solr_post(params: dict, timeout: int = 60) -> dict:
    """Fire a POST request to Solr (avoids URL-length limits for long OR queries)."""
    params.setdefault("facet", "false")
    resp = requests.post(
        SOLR_URL,
        data=params,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


# Admin endpoint — used only for the fast connection check
_SOLR_ADMIN_URL = SOLR_URL.replace("/select", "").rsplit("/", 1)[0] +     "/admin/info/system"


class SolrLookup:

    def __init__(
        self,
        solr_url: str = SOLR_URL,
        similarity_threshold: float = SIMILARITY_THRESHOLD,
    ):
        self.url = solr_url
        self.threshold = similarity_threshold
        self._check_connection()

    def _check_connection(self):
        """Ping the Solr admin endpoint — much faster than scanning 492M docs."""
        admin_url = self.url.replace("/select", "").rsplit("/", 1)[0] +             "/admin/info/system"
        try:
            r = requests.get(admin_url, params={"wt": "json"}, timeout=5)
            r.raise_for_status()
        except Exception as e:
            raise RuntimeError(
                f"\nCannot connect to Solr at {self.url}\n"
                f"Check that galaxy:8983 is reachable.\nError: {e}"
            ) from None

    def by_doi(self, doi: str) -> SolrResult:
        """Exact DOI lookup."""
        # strip protocol prefix if present
        doi = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', doi.strip(), flags=re.I)
        doi = doi.lower()
        params = {
            "q": f'doi:"{doi}"',
            "defType": "lucene",
            "fl": "id,title,doi,publication_year,cited_by_count,type",
            "rows": "1",
            "wt": "json",
        }
        try:
            data = _solr_get(params)
            docs = data["response"]["docs"]
            if docs:
                return SolrResult(found=True, method=MatchMethod.DOI,
                                  record=docs[0], confidence=1.0)
        except Exception as e:
            print(f"Solr DOI lookup failed: {e}")
        return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

    def by_title(self, title: str, year: Optional[int] = None,
                 candidates: int = 5) -> SolrResult:
        """Keyword title search with optional year filter."""
        params = {
            "q": title,
            "qf": "title^3 abstract",
            "defType": "edismax",
            "fl": "id,title,doi,publication_year,cited_by_count,type",
            "rows": str(candidates),
            "wt": "json",
            "boost": "",           # disable recency boost
        }
        if year:
            params["fq"] = f"publication_year:[{year - 1} TO {year + 1}]"
        else:
            params["fq"] = ""      # include all years

        try:
            data = _solr_get(params)
            docs = data["response"]["docs"]
        except Exception as e:
            print(f"Solr title lookup failed: {e}")
            return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

        best_doc, best_score = None, 0.0
        for doc in docs:
            candidate = _get_title(doc)
            if not candidate:
                continue
            score = _title_similarity(title, candidate)
            if score > best_score:
                best_score = score
                best_doc = doc

        if best_doc and best_score >= self.threshold:
            method = MatchMethod.TITLE_YEAR if year else MatchMethod.TITLE_ONLY
            return SolrResult(found=True, method=method,
                              record=best_doc, confidence=round(best_score, 4))
        return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

    @staticmethod
    def _escape_phrase(text: str) -> str:
        """Escape backslash and double-quote for use inside a Lucene phrase query."""
        return text.replace('\\', '\\\\').replace('"', '\\"')

    @staticmethod
    def _title_variants(title: str) -> list:
        """
        Generate alternative title strings to retry when the raw GROBID title fails.

        GROBID has two common failure modes:
        1. Author-team prefix:  "Novel Coronavirus Outbreak Research Team. Detection of..."
           → the real title is the part AFTER the first ". "
        2. Appended subtitle:   "...QUOROM Statement. Quality of Reporting of Meta-analyses"
           → the real title is the part BEFORE the first ". "

        We also normalize common PDF ligature encoding artifacts (® → fi, Ð → -).
        """
        variants = []

        # Normalize PDF ligature / encoding artifacts
        normalized = (title
                      .replace('®', 'fi')   # ® is the fi ligature in some PDFs
                      .replace('ð', '-')    # ð mis-encoded as em-dash
                      .replace('Ð', '-')    # Ð
                      .replace('±', '-')    # ± used as en-dash
                      .replace('“', '"').replace('”', '"')
                      .replace('‘', "'").replace('’', "'"))
        if normalized != title:
            variants.append(normalized)

        # Split on first ". " — try both halves if the title has one
        if '. ' in title:
            before, after = title.split('. ', 1)
            # Only use a fragment if it's substantial (>20 chars) and looks like a title
            if len(after.strip()) > 20:
                variants.append(after.strip())   # drop author prefix
            if len(before.strip()) > 20:
                variants.append(before.strip())  # drop appended subtitle

        return variants

    def by_title_batch(self, citations: list, batch_size: int = 15) -> list:
        """
        Look up multiple citations in batched Solr OR queries (~1 HTTP request per
        batch_size citations instead of 2-3 requests per citation).

        citations : list of namespace objects with .title and .year
        Returns   : list[SolrResult] in the same order as .

        Strategy: build an OR of Lucene phrase queries for each batch, sent as a
        single POST request; fetch up to batch_size * 8 candidate docs (max 500);
        then fuzzy-match each citation client-side against the returned doc pool.
        Citations without a title are left as NOT_FOUND (handled by caller).
        """
        n = len(citations)
        results = [SolrResult(found=False, method=MatchMethod.NOT_FOUND, confidence=0.0)
                   for _ in range(n)]

        for start in range(0, n, batch_size):
            batch = citations[start : start + batch_size]

            # Only citations that have a title can be phrase-matched
            titled = [(j, c) for j, c in enumerate(batch) if c.title]
            if not titled:
                continue

            # Build OR of Lucene phrase queries: title:"escaped title" OR ...
            clauses = [f'title:"{self._escape_phrase(c.title)}"' for _, c in titled]
            q = " OR ".join(clauses)

            rows = min(len(titled) * 8, 500)

            params = {
                "q":       q,
                "defType": "lucene",
                "fl":      "id,title,doi,publication_year,primary_location",
                "rows":    str(rows),
                "wt":      "json",
                "facet":   "false",
            }

            try:
                data = _solr_post(params)
                docs = data["response"]["docs"]
            except Exception as e:
                print(f"Solr batch lookup failed (batch {start}–{start+len(batch)-1}): {e}")
                continue

            # For each citation in the batch, find the best matching doc in the pool
            for j, c in titled:
                best_doc, best_score = None, 0.0
                for doc in docs:
                    db_title = _get_title(doc)
                    if not db_title:
                        continue
                    score = _title_similarity(c.title, db_title)
                    if score > best_score:
                        best_score = score
                        best_doc = doc

                if best_doc and best_score >= self.threshold:
                    db_year = best_doc.get("publication_year")
                    # Year guard: when both years are known and differ by >1, reject
                    # this batch match.  The batch pool is shared across all citations
                    # in the OR query, so a doc returned for citation A can silently
                    # become the best fuzzy match for citation B (cross-contamination).
                    # Rejecting year-mismatched results pushes them to the individual
                    # fallback (by_title with a year filter) where they are handled
                    # correctly.
                    try:
                        year_diff = (abs(int(c.year) - int(db_year))
                                     if c.year and db_year else 0)
                    except (ValueError, TypeError):
                        year_diff = 0
                    if c.year and db_year and year_diff > 1:
                        continue   # send to individual fallback — wrong-paper match
                    method = MatchMethod.TITLE_YEAR if year_diff == 0 else MatchMethod.TITLE_ONLY
                    results[start + j] = SolrResult(
                        found=True, method=method,
                        record=best_doc, confidence=round(best_score, 4),
                    )

        return results

    def by_citation(self, parsed) -> SolrResult:
        """Try DOI first, then title+year, then title only, then title variants."""
        if parsed.doi:
            result = self.by_doi(parsed.doi)
            if result.found:
                return result

        title = parsed.title
        year  = parsed.year

        if title and year:
            result = self.by_title(title, year=year)
            if result.found:
                return result

        if title:
            result = self.by_title(title)
            if result.found:
                return result

        # Retry with cleaned-up title variants (subtitle stripping, author prefix, encoding)
        if title:
            for variant in self._title_variants(title):
                if year:
                    result = self.by_title(variant, year=year)
                    if result.found:
                        return result
                result = self.by_title(variant)
                if result.found:
                    return result

        return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

    def close(self):
        pass  # no persistent connection to close

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


if __name__ == "__main__":
    import time
    print("Testing SolrLookup...")
    with SolrLookup() as s:
        # DOI test
        r = s.by_doi("10.1056/NEJMoa2001017")
        print(f"\nDOI lookup:  found={r.found}  method={r.method}")
        if r.record:
            print(f"  title: {_get_title(r.record)}")
            print(f"  year:  {r.record.get('publication_year')}")

        # title+year test (single)
        r2 = s.by_title("A novel coronavirus from patients with pneumonia in China", year=2020)
        print(f"\nTitle lookup: found={r2.found}  method={r2.method}  conf={r2.confidence}")
        if r2.record:
            print(f"  title: {_get_title(r2.record)}")
            print(f"  year:  {r2.record.get('publication_year')}")

        # batch test — 3 citations in one request
        import types as _types
        def _obj(title, year):
            o = _types.SimpleNamespace(); o.doi=None; o.title=title; o.year=year; return o

        batch_input = [
            _obj("A novel coronavirus from patients with pneumonia in China", 2020),
            _obj("Remdesivir for the Treatment of Covid-19", 2020),
            _obj("this title does not exist in any database whatsoever xyz123", 2020),
        ]
        t0 = time.time()
        batch_out = s.by_title_batch(batch_input)
        elapsed = time.time() - t0
        print(f"\nBatch lookup (3 citations, 1 request): {elapsed:.2f}s")
        for inp, res in zip(batch_input, batch_out):
            print(f"  [{res.method}] conf={res.confidence:.2f}  '{inp.title[:60]}'")
