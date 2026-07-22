"""Federated Crossref + OpenAlex lookup, joined on DOI at query time.

Neither store is duplicated: OpenAlex (Solr, corpus/graph + existence) and Crossref
(`crs.crossref` Mongo, authoritative bibliographic metadata + raw reference[] + title_norm)
are joined on the normalized DOI on demand. `by_doi()` returns one unified record with
per-field provenance and in_crossref / in_openalex flags; `references()` returns the raw
Crossref reference list (the fake-detection input OpenAlex does not store); `verify_dois()`
is the pipeline's single DOI accessor across BOTH corpora.
"""
import re
import requests
from typing import Optional

from mongo_lookup import (MongoLookup, extract_title_text, extract_year,
                          extract_journal, norm_title_exact)

_DOI_PREFIX = re.compile(r'^(?:https?://(?:dx\.)?doi\.org/|doi:)', re.I)


_OA_SOLR = "http://galaxy:8983/solr/openalexWorks/select"


def _solr_esc(v):
    return str(v).replace("\\", " ").replace('"', " ").strip()


def norm_doi(d: str) -> str:
    return _DOI_PREFIX.sub('', (d or '').strip()).lower()


_XREF_FIELDS = {"_id": 0, "DOI": 1, "title": 1, "title_norm": 1, "container-title": 1,
                "short-container-title": 1, "issued": 1, "volume": 1, "issue": 1,
                "page": 1, "article-number": 1, "author": 1, "type": 1, "ISSN": 1,
                "reference": 1, "publisher": 1, "reference-count": 1}


class IntegratedLookup:
    def __init__(self, solr=None, use_solr: bool = True):
        self.xr = MongoLookup()
        # reuse an existing SolrLookup when the caller has one (avoids a 2nd connection)
        self.solr = solr
        if self.solr is None and use_solr:
            try:
                from solr_lookup import SolrLookup
                self.solr = SolrLookup()
            except Exception:
                self.solr = None

    # --- source fetchers ---
    def _crossref(self, doi: str) -> Optional[dict]:
        return self.xr.collection.find_one({"DOI": norm_doi(doi)}, _XREF_FIELDS)

    def _openalex(self, doi: str) -> Optional[dict]:
        if not self.solr:
            return None
        try:
            r = self.solr.by_doi(doi)
            return r.record if getattr(r, "found", False) else None
        except Exception:
            return None

    # --- DOI verification across BOTH corpora (pipeline's single DOI accessor) ---
    def verify_dois(self, refs: list) -> list:
        """Verify each ref's DOI against both corpora, aligned to `refs`.

        Phase 1: OpenAlex Solr (per-ref by_doi). Phase 2: Crossref (batched) for the
        remainder. DOI is exact/authoritative so both indexes are exhausted here before
        any weaker metadata/title phase runs. Returns a list of SolrResult-or-None — the
        same contract the pipeline's original inline DOI phases produced.
        """
        n = len(refs)
        out = [None] * n
        if self.solr is not None:
            for i, ref in enumerate(refs):
                doi = getattr(ref, "doi", None)
                if doi:
                    try:
                        r = self.solr.by_doi(doi)
                        if r.found:
                            out[i] = r
                    except Exception:
                        pass
        # arXiv fallback: no DOI but an arXiv id in the raw text -> 10.48550/arXiv.<id>
        if self.solr is not None:
            from text_repair import arxiv_doi_from_text, preprint_doi_from_text, pmid_from_text
            for i in range(n):
                if out[i] is not None or getattr(refs[i], "doi", None):
                    continue
                _raw = getattr(refs[i], "raw", None) or getattr(refs[i], "title", None)
                _pm = pmid_from_text(_raw)
                if _pm and out[i] is None:
                    try:
                        _pd = requests.get(_OA_SOLR, params={"q": "pmid:" + _pm, "fq": "",
                              "rows": 1, "wt": "json", "fl": "doi"}, timeout=12).json()["response"]["docs"]
                        if _pd and _pd[0].get("doi"):
                            _r = self.solr.by_doi(_pd[0]["doi"])
                            if _r.found:
                                out[i] = _r
                    except Exception:
                        pass
                for _idf in (arxiv_doi_from_text(_raw), preprint_doi_from_text(_raw)):
                    if _idf and out[i] is None:
                        try:
                            r = self.solr.by_doi(_idf)
                            if r.found:
                                out[i] = r
                        except Exception:
                            pass
                continue
                adoi = arxiv_doi_from_text(getattr(refs[i], "raw", None)
                                           or getattr(refs[i], "title", None))
                if adoi:
                    try:
                        r = self.solr.by_doi(adoi)
                        if r.found:
                            out[i] = r
                    except Exception:
                        pass
        rem = [i for i in range(n) if out[i] is None and getattr(refs[i], "doi", None)]
        if rem:
            try:
                from crossref_lookup import batch_crossref
                for i, r in zip(rem, batch_crossref([refs[i] for i in rem])):
                    if r.found:
                        out[i] = r
            except Exception as exc:
                print(f"    [crossref-doi] failed: {exc}")
        return out

    # --- unified record ---
    def by_doi(self, doi: str) -> Optional[dict]:
        doi = norm_doi(doi)
        xr, oa = self._crossref(doi), self._openalex(doi)
        if not xr and not oa:
            return None
        rec = {"doi": doi, "in_crossref": bool(xr), "in_openalex": bool(oa), "sources": {}}

        def take(key, val, src):
            if val not in (None, "", [], {}):
                rec[key] = val
                rec["sources"][key] = src

        if xr:  # Crossref = authoritative bibliographic + references
            take("title", extract_title_text(xr), "crossref")
            take("title_norm", xr.get("title_norm"), "crossref")
            take("year", extract_year(xr), "crossref")
            take("journal", extract_journal(xr), "crossref")
            take("volume", xr.get("volume"), "crossref")
            take("issue", xr.get("issue"), "crossref")
            take("page", xr.get("page"), "crossref")
            take("article_num", xr.get("article-number"), "crossref")
            take("type", xr.get("type"), "crossref")
            take("issn", xr.get("ISSN"), "crossref")
            take("publisher", xr.get("publisher"), "crossref")
            take("authors", [{"family": a.get("family"), "given": a.get("given"),
                              "name": a.get("name")} for a in (xr.get("author") or [])],
                 "crossref")
            take("raw_references", xr.get("reference"), "crossref")   # <-- Job 1 enabler
            take("reference_count", xr.get("reference-count"), "crossref")

        if oa:  # OpenAlex = corpus/graph; fills gaps + adds its own ids/metrics
            take("openalex_id", oa.get("id"), "openalex")
            take("cited_by_count", oa.get("cited_by_count"), "openalex")
            if "title" not in rec:
                take("title", oa.get("title"), "openalex")
            if "title_norm" not in rec and oa.get("title"):
                take("title_norm", norm_title_exact(oa.get("title")), "computed")
            if "year" not in rec:
                take("year", oa.get("publication_year"), "openalex")
            if "journal" not in rec:
                take("journal", oa.get("venue_name"), "openalex")
            if "type" not in rec:
                take("type", oa.get("type"), "openalex")
        return rec

    def _oa_title_exact(self, title, year=None):
        """Exact title match against OpenAlex Solr `title_exact` (case-insensitive but
        PUNCTUATION-sensitive, so pass the RAW title, not norm_title_exact). Multi-hit
        titles (reprints) are disambiguated by year (fq) and a DOI-bearing record is
        preferred. `fq=""` clears the handler's default publication_year filter."""
        if not title:
            return None
        params = {"q": 'title_exact:"%s"' % str(title).replace('"', " "),
                  "fq": "", "facet": "false", "hl": "false", "wt": "json", "rows": 10,
                  "fl": "id,title,doi,publication_year,venue_name,author_names"}
        if year:
            params["fq"] = "publication_year:[%d TO %d]" % (int(year) - 1, int(year) + 1)
        try:
            docs = requests.get(_OA_SOLR, params=params, timeout=20).json()["response"]["docs"]
        except Exception:
            return None
        if not docs:
            return None
        docs.sort(key=lambda d: 0 if d.get("doi") else 1)   # prefer 1:1 DOI record
        return docs[0]

    def oa_by_metadata(self, ref):
        """OpenAlex structured match: venue_id (resolved from the cited journal via the
        journal authority) + volume + first_page, year-filtered. Asserted ONLY when it
        uniquely identifies one work (numFound==1) — precision guard, since without the
        venue_id or with plain numeric pages the triple can collide. Mirrors the Crossref
        biblio match against the OpenAlex corpus. Returns a solr_lookup.SolrResult."""
        from solr_lookup import SolrResult, MatchMethod
        nf_result = SolrResult(found=False, method=MatchMethod.NOT_FOUND)
        vol = getattr(ref, "volume", None); pg = getattr(ref, "first_page", None)
        yr = getattr(ref, "year", None); j = getattr(ref, "journal", None)
        if not (vol and pg and yr):
            return nf_result
        try:
            from journal_authority import venue_ids_for
            vids = venue_ids_for(j) if j else []
        except Exception:
            vids = []
        clauses = ['volume:"%s"' % _solr_esc(vol), 'first_page:"%s"' % _solr_esc(pg)]
        if vids:
            vc = " OR ".join('venue_id:"%s"' % str(v).rsplit("/", 1)[-1] for v in vids)
            clauses.insert(0, "(%s)" % vc)
        params = {"q": " AND ".join(clauses),
                  "fq": "publication_year:[%d TO %d]" % (int(yr) - 1, int(yr) + 1),
                  "facet": "false", "hl": "false", "wt": "json", "rows": 2,
                  "fl": "id,title,doi,publication_year,venue_name,volume,author_names"}
        try:
            resp = requests.get(_OA_SOLR, params=params, timeout=20).json()["response"]
        except Exception:
            return nf_result
        if resp.get("numFound") != 1:            # uniqueness guard
            return nf_result
        d = resp["docs"][0]
        t = d.get("title"); t = t[0] if isinstance(t, list) and t else t
        rec = {"doi": (d.get("doi") or "").replace("https://doi.org/", "") or None,
               "openalex_id": d.get("id"), "title": t,
               "year": d.get("publication_year"), "publication_year": d.get("publication_year"),
               "journal": d.get("venue_name"), "venue_name": d.get("venue_name"),
               "volume": d.get("volume")}
        return SolrResult(found=True, method=MatchMethod.META_MATCH, record=rec, confidence=1.0)

    def _oa_title_phrase(self, title, year=None):
        """Punctuation-INSENSITIVE OpenAlex title match: a phrase query on the analyzed
        `title` field (tokenization already strips punctuation, so a cited colon matches a
        stored dash), then keep ONLY an exact canonical-key match — stays exact, not fuzzy.
        Closes the title_exact punctuation gap for OpenAlex-only works. Runs as a fallback,
        anchored on a multi-token title + year filter so it stays fast and selective."""
        from title_normalize import normalize_title_key
        key = normalize_title_key(title)
        if not key or len(key.split()) < 4:      # too-generic phrase -> skip
            return None
        params = {"q": 'title:"%s"' % key.replace('"', " "),
                  "fq": ("publication_year:[%d TO %d]" % (int(year) - 1, int(year) + 1)
                         if year else ""),
                  "facet": "false", "hl": "false", "wt": "json", "rows": 10,
                  "fl": "id,title,doi,publication_year,venue_name,author_names"}
        try:
            docs = requests.get(_OA_SOLR, params=params, timeout=20).json()["response"]["docs"]
        except Exception:
            return None
        for d in docs:
            tt = d.get("title")
            tt = tt[0] if isinstance(tt, list) and tt else tt
            if normalize_title_key(tt or "") == key:
                return d
        return None

    def by_title_exact(self, title, year=None, journal=None, author=None, _repaired=False):
        """Federated exact-title match: Crossref `title_norm` first (punctuation-collapsed,
        robust), then OpenAlex `title_exact` (broader corpus). Returns a LookupResult so it
        is a drop-in for the pipeline's Phase 3.9."""
        from mongo_lookup import LookupResult, MatchMethod
        hit = self.xr.by_title_exact(title, year=year, journal=journal, author=author)
        if hit and hit.found:
            return hit
        d = self._oa_title_exact(title, year=year)
        if not d:                                  # punctuation-variant fallback
            d = self._oa_title_phrase(title, year=year)
        if d:
            t = d.get("title")
            t = t[0] if isinstance(t, list) and t else t
            rec = {"doi": (d.get("doi") or "").replace("https://doi.org/", "") or None,
                   "openalex_id": d.get("id"), "title": t,
                   "year": d.get("publication_year"),
                   "publication_year": d.get("publication_year"),
                   "journal": d.get("venue_name"), "venue_name": d.get("venue_name"),
                   "author_names": d.get("author_names") or []}
            return LookupResult(
                found=True,
                method=MatchMethod.TITLE_YEAR if year else MatchMethod.TITLE_ONLY,
                record=rec, confidence=1.0)
        if not _repaired:                         # retry with cleaned title variants
            from text_repair import title_repair_variants  # ligature / author-strip / greek
            for _variant in title_repair_variants(title):
                _r = self.by_title_exact(_variant, year=year, journal=journal,
                                         author=author, _repaired=True)
                if _r.found:
                    return _r
        return LookupResult(found=False, method=MatchMethod.NOT_FOUND)

    def references(self, doi: str) -> list:
        """Raw deposited reference list (Crossref) — the claims to verify."""
        return (self._crossref(doi) or {}).get("reference") or []
