"""
crossref_lookup.py — fallback citation lookup via the Crossref REST API.

Used when OpenAlex Solr does not find a citation. Crossref covers ~160M works
(journal articles, books, conference proceedings, preprints) and has broader
coverage than OpenAlex for older papers and grey literature.

API: https://api.crossref.org/works  (free, no key required)
Rate limit: polite pool (add mailto: to User-Agent) allows generous throughput.

Usage:
    from crossref_lookup import CrossrefLookup
    with CrossrefLookup(email="you@example.com") as cr:
        result = cr.by_citation(obj)   # obj needs .doi, .title, .year

Returns SolrResult objects (same type as SolrLookup) with method values
MatchMethod.XREF_DOI / XREF_TITLE_YEAR / XREF_TITLE_ONLY.
"""
import re
import time
from difflib import SequenceMatcher
from typing import Optional

import requests

# Import shared result types from solr_lookup
from solr_lookup import SolrResult, MatchMethod

CROSSREF_API = "https://api.crossref.org/works"
SIMILARITY_THRESHOLD = 0.85


def _normalize_title(t) -> str:
    """Flatten a list title, strip HTML tags Crossref sometimes includes."""
    if isinstance(t, list):
        t = " ".join(t)
    t = re.sub(r'<[^>]+>', '', t)          # strip <i>, <sub>, etc.
    return re.sub(r'\s+', ' ', t).strip()


def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _extract_year(msg: dict) -> Optional[int]:
    """Pull the earliest available publication year from a Crossref work."""
    for key in ("published-print", "published-online", "published"):
        parts = msg.get(key, {}).get("date-parts", [])
        if parts and parts[0] and parts[0][0]:
            return int(parts[0][0])
    return None


def _to_record(msg: dict) -> dict:
    """Normalise a Crossref message dict to the same shape as a Solr doc."""
    titles = msg.get("title") or []
    title = _normalize_title(titles) if titles else ""
    year = _extract_year(msg)
    containers = msg.get("container-title") or []
    journal = containers[0] if containers else ""
    return {
        "title":            title,
        "doi":              msg.get("DOI", ""),
        "publication_year": year,
        "container-title":  [journal],
        "source":           "crossref",
    }


class CrossrefLookup:
    """
    Thin wrapper around the Crossref REST API.

    Implements the same by_doi / by_title / by_citation interface as
    SolrLookup so it can be used as a drop-in fallback.
    """

    def __init__(
        self,
        email: Optional[str] = None,
        similarity_threshold: float = SIMILARITY_THRESHOLD,
        min_delay: float = 0.05,   # seconds between requests (~20 req/s max)
    ):
        ua = "FakeCitationDetector/1.0"
        if email:
            ua += f" (mailto:{email})"
        self.headers = {"User-Agent": ua}
        self.threshold = similarity_threshold
        self.min_delay = min_delay
        self._last_req = 0.0
        self._net_error = False   # set True on timeout/5xx; by_citation checks this

    # ── internal HTTP helper ─────────────────────────────────────────────────

    def _get(self, url: str, params: Optional[dict] = None) -> Optional[dict]:
        """GET url with rate-limiting and error handling.
        Sets _net_error on 429/timeout/5xx so by_citation circuit-breaks immediately.
        """
        elapsed = time.monotonic() - self._last_req
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        try:
            resp = requests.get(url, params=params or {},
                                headers=self.headers, timeout=8)
            self._last_req = time.monotonic()
            resp.raise_for_status()
            self._net_error = False
            return resp.json()
        except Exception as e:
            msg = str(e)
            if ("429" in msg or "timed out" in msg.lower()
                    or "timeout" in msg.lower() or "500" in msg):
                self._net_error = True
            print(f"    [crossref] request failed: {e}")
            return None

    # ── public lookup methods ────────────────────────────────────────────────

    def by_doi(self, doi: str) -> SolrResult:
        """Exact DOI lookup via GET /works/{doi}."""
        doi = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', doi.strip(), flags=re.I)
        data = self._get(f"{CROSSREF_API}/{doi}")
        if not data or data.get("status") != "ok":
            return SolrResult(found=False, method=MatchMethod.NOT_FOUND)
        return SolrResult(
            found=True, method=MatchMethod.XREF_DOI,
            record=_to_record(data["message"]), confidence=1.0,
        )

    def by_title(self, title: str, year: Optional[int] = None,
                 candidates: int = 5) -> SolrResult:
        """Keyword title search with optional year filter (±1 year window)."""
        params: dict = {
            "query.title": title,
            "rows": candidates,
            "select": "DOI,title,published,published-print,published-online,container-title",
        }
        if year:
            # Crossref filter uses YYYY-MM format
            params["filter"] = (
                f"from-pub-date:{year - 1}-01,until-pub-date:{year + 1}-12"
            )

        data = self._get(CROSSREF_API, params)
        if not data or data.get("status") != "ok":
            return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

        items = data["message"].get("items", [])
        best_item, best_sim = None, 0.0
        for item in items:
            raw = item.get("title") or item.get("short-title") or []
            if not raw:
                continue
            candidate = _normalize_title(raw)
            sim = _title_similarity(title, candidate)
            if sim > best_sim:
                best_sim = sim
                best_item = item

        if best_item and best_sim >= self.threshold:
            method = MatchMethod.XREF_TITLE_YEAR if year else MatchMethod.XREF_TITLE_ONLY
            return SolrResult(
                found=True, method=method,
                record=_to_record(best_item), confidence=round(best_sim, 4),
            )

        return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

    @staticmethod
    def _title_variants(title: str) -> list:
        """
        Same variant logic as SolrLookup._title_variants — handles GROBID's
        two failure modes (author prefix, appended subtitle) and PDF encoding
        artifacts (fi-ligature, em-dash encoding, ± as en-dash).
        """
        variants = []
        normalized = (title
                      .replace('®', 'fi').replace('ð', '-')
                      .replace('Ð', '-').replace('±', '-')
                      .replace('“', '"').replace('”', '"')
                      .replace('‘', "'").replace('’', "'"))
        if normalized != title:
            variants.append(normalized)
        if '. ' in title:
            before, after = title.split('. ', 1)
            if len(after.strip()) > 20:
                variants.append(after.strip())   # drop author-team prefix
            if len(before.strip()) > 20:
                variants.append(before.strip())  # drop appended subtitle
        return variants

    def by_citation(self, parsed) -> SolrResult:
        """
        Full waterfall: DOI → title+year → title only → title variants.
        Circuit-breaks on timeout/5xx so one stuck ref doesn't double the wait.
        """
        doi   = getattr(parsed, 'doi',   None)
        title = getattr(parsed, 'title', None)
        year  = getattr(parsed, 'year',  None)
        self._net_error = False   # reset per-citation

        if doi:
            r = self.by_doi(doi)
            if r.found:
                return r
            if self._net_error:
                return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

        if title and year:
            r = self.by_title(title, year=year)
            if r.found:
                return r
            if self._net_error:
                return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

        if title:
            r = self.by_title(title)
            if r.found:
                return r
            if self._net_error:
                return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

        if title:
            for variant in self._title_variants(title):
                if year:
                    r = self.by_title(variant, year=year)
                    if r.found:
                        return r
                    if self._net_error:
                        return SolrResult(found=False, method=MatchMethod.NOT_FOUND)
                r = self.by_title(variant)
                if r.found:
                    return r
                if self._net_error:
                    return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

        return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

    # ── context manager ──────────────────────────────────────────────────────

    def close(self):
        pass   # stateless HTTP; nothing to close

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ── standalone test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testing CrossrefLookup...")
    with CrossrefLookup() as cr:
        # DOI test — a well-known paper
        r = cr.by_doi("10.1056/NEJMoa2001017")
        print(f"\nDOI lookup:  found={r.found}  method={r.method}")
        if r.record:
            print(f"  title: {r.record.get('title')}")
            print(f"  year:  {r.record.get('publication_year')}")

        # Title + year test
        r2 = cr.by_title("A novel coronavirus from patients with pneumonia in China", year=2020)
        print(f"\nTitle lookup: found={r2.found}  method={r2.method}  conf={r2.confidence}")
        if r2.record:
            print(f"  title: {r2.record.get('title')}")
            print(f"  year:  {r2.record.get('publication_year')}")

        # A book — should not be in Crossref normally
        r3 = cr.by_title("Manual for the Beck Depression Inventory-II", year=1996)
        print(f"\nBook lookup:  found={r3.found}  method={r3.method}")


# ── concurrent batch lookup ──────────────────────────────────────────────────

import threading
from concurrent.futures import ThreadPoolExecutor

_thread_local = threading.local()


def _thread_crossref() -> "CrossrefLookup":
    """Get (or lazily create) a per-thread CrossrefLookup instance (polite pool)."""
    if not hasattr(_thread_local, "lookup"):
        _thread_local.lookup = CrossrefLookup(email="rwang@insilicom.com")
    return _thread_local.lookup


def batch_crossref(parsed_list: list, max_workers: int = 4) -> list:
    """
    Concurrent Crossref waterfall for a list of parsed citations.
    Returns SolrResults in the same order as input.
    max_workers=4 keeps total load sane when multiple jobs run concurrently.
    Each thread owns its own CrossrefLookup; 429s circuit-break the waterfall.
    """
    if not parsed_list:
        return []

    def _lookup(parsed):
        return _thread_crossref().by_citation(parsed)

    n = min(max_workers, len(parsed_list))
    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(_lookup, parsed_list))
