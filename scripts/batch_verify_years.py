"""
batch_verify_years.py — download sampled papers and run citation verification.

Reads the manifest produced by sample_papers.py, downloads each PDF,
runs it through GROBID + 4-phase citation verification, and writes per-paper
results to a JSONL file. A separate aggregation step produces the comparison
table by year and field.

Usage:
    # Step 1: generate manifest
    python3 sample_papers.py --n 10 --out manifest.jsonl

    # Step 2: download + verify (can be restarted — skips already-done papers)
    python3 batch_verify_years.py manifest.jsonl --out results.jsonl

    # Step 3: print comparison table
    python3 batch_verify_years.py --summarise results.jsonl
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
import types
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import numpy as np
import requests

# ── Config ────────────────────────────────────────────────────────────────────
GROBID_URL       = "http://localhost:8070/api/processFulltextDocument"
DOWNLOAD_DIR     = pathlib.Path.home() / "cross_year_study" / "pdfs"
GROBID_TIMEOUT   = 240
DOWNLOAD_TIMEOUT = 60
CROSSREF_API     = "https://api.crossref.org/works"
CROSSREF_EMAIL   = "rwang@insilicom.com"
CROSSREF_TIMEOUT = 15
OPENALEX_API     = "https://api.openalex.org/works"

_NS = {"tei": "http://www.tei-c.org/ns/1.0"}
_DOI_RE  = re.compile(r'(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,}/[^\s,;)\]>]+)', re.I)
_YEAR_RE = re.compile(r'\b(1[89]\d{2}|20[012]\d)\b')

DL_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "application/pdf,*/*",
}

# ── TEI parsing (copied from verify_pdf.py) ──────────────────────────────────

def _tei_text(el, xpath):
    found = el.find(xpath, _NS)
    return found.text.strip() if found is not None and found.text else None

def _tei_doi(ref):
    for idno in ref.findall(".//tei:idno", _NS):
        if (idno.get("type") or "").upper() == "DOI" and idno.text:
            doi = idno.text.strip().lower()
            doi = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', doi)
            return doi.rstrip(".,;)")
    return None

def _tei_year(ref):
    date = ref.find(".//tei:date[@type='published']", _NS)
    if date is not None:
        when = date.get("when") or date.text or ""
        m = _YEAR_RE.search(when)
        if m: return int(m.group(1))
    return None

def _tei_title(ref):
    for xpath in (".//tei:analytic/tei:title[@level='a']",
                  ".//tei:analytic/tei:title",
                  ".//tei:monogr/tei:title[@level='j']",
                  ".//tei:monogr/tei:title"):
        t = _tei_text(ref, xpath)
        if t and len(t.split()) >= 2:
            return t
    return None


def _tei_journal(ref):
    """Extract journal name from monogr > title[@level='j']."""
    t = _tei_text(ref, ".//tei:monogr/tei:title[@level='j']")
    return t if t and len(t) > 2 else None

def _tei_volume(ref):
    """Extract volume number."""
    vol = ref.find(".//tei:monogr/tei:imprint/tei:biblScope[@unit='volume']", _NS)
    return (vol.text or "").strip() or None if vol is not None else None

def _tei_first_page(ref):
    """Extract first page from @from attribute or element text."""
    page = ref.find(".//tei:monogr/tei:imprint/tei:biblScope[@unit='page']", _NS)
    if page is not None:
        fp = page.get("from") or (page.text or "").strip()
        return fp.replace("–", "-").split("-")[0].strip() or None
    return None

def _tei_first_author(ref):
    """Extract first author's last name."""
    for xpath in (".//tei:analytic/tei:author/tei:persName/tei:surname",
                  ".//tei:monogr/tei:author/tei:persName/tei:surname"):
        s = _tei_text(ref, xpath)
        if s:
            return s
    return None

def parse_tei_refs(tei_xml: str) -> list:
    try:
        root = ET.fromstring(tei_xml)
    except ET.ParseError:
        return []
    refs = []
    for bib in root.findall(".//tei:listBibl/tei:biblStruct", _NS):
        obj = types.SimpleNamespace()
        raw_note = bib.find(".//tei:note[@type='raw_reference']", _NS)
        obj.raw   = raw_note.text.strip() if raw_note is not None and raw_note.text else ""
        obj.doi          = _tei_doi(bib)
        obj.year         = _tei_year(bib)
        obj.title        = _tei_title(bib)
        obj.journal      = _tei_journal(bib)
        obj.volume       = _tei_volume(bib)
        obj.first_page   = _tei_first_page(bib)
        obj.first_author = _tei_first_author(bib)
        if not obj.doi and obj.raw:
            m = _DOI_RE.search(obj.raw)
            if m: obj.doi = m.group(1).rstrip(".,;)").lower()
        refs.append(obj)
    return refs


# ── Download ─────────────────────────────────────────────────────────────────

_ELIFE_ID_RE   = re.compile(r'elife[./](\d+)', re.I)
_IEEE_ARNO_RE  = re.compile(r'/0*(\d{6,})(?:\.pdf)?$')

def _candidate_urls(doi: str, url: str) -> list[str]:
    """Return ordered list of URLs to try, with publisher-specific fixes applied."""
    # PLOS ONE: always use the printable PDF endpoint
    if "10.1371/" in doi:
        return [f"https://journals.plos.org/plosone/article/file?id={doi}&type=printable"]

    # eLife: CDN versioned PDF (try v1 → v4)
    if "10.7554/" in doi:
        m = _ELIFE_ID_RE.search(doi)
        if m:
            aid = m.group(1)
            return [f"https://cdn.elifesciences.org/articles/{aid}/elife-{aid}-v{v}.pdf"
                    for v in range(1, 5)]

    # IEEE Access: strip the leading zero from the article number
    if "ieeexplore.ieee.org" in url:
        m = _IEEE_ARNO_RE.search(url)
        if m:
            return [f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={m.group(1)}"]

    return [url]


def download_pdf(doi: str, url: str, dest: pathlib.Path) -> Optional[pathlib.Path]:
    """Download a PDF; return path on success, None on failure. Skips if exists."""
    # Only treat an existing PDF as "done"; HTML/XML from a failed first attempt
    # should not block a retry with a better URL.
    pdf_candidate = dest.with_suffix(".pdf")
    if pdf_candidate.exists() and pdf_candidate.stat().st_size > 5_000:
        return pdf_candidate

    for try_url in _candidate_urls(doi, url):
        try:
            resp = requests.get(try_url, headers=DL_HEADERS, timeout=DOWNLOAD_TIMEOUT,
                                allow_redirects=True)
            if resp.status_code == 200 and len(resp.content) > 5_000:
                ct = resp.headers.get("Content-Type", "")
                ext = ".pdf" if "pdf" in ct else ".html" if "html" in ct else ".pdf"
                fpath = dest.with_suffix(ext)
                fpath.write_bytes(resp.content)
                return fpath
        except Exception:
            pass
    return None


# ── Crossref reference-list fetch (fast path — replaces GROBID when available) ─

def crossref_refs(doi: str) -> list | None:
    """Fetch the source paper's reference list from Crossref.

    Returns a list of SimpleNamespace(doi, title, year, raw) — same shape
    as parse_tei_refs() — or None if Crossref has no reference list for this DOI.
    """
    try:
        r = requests.get(
            f"{CROSSREF_API}/{doi}",
            params={"mailto": CROSSREF_EMAIL},
            timeout=CROSSREF_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        raw_refs = r.json().get("message", {}).get("reference", [])
        if not raw_refs:
            return None

        refs = []
        for ref in raw_refs:
            obj = types.SimpleNamespace()
            # DOI
            doi_raw = (ref.get("DOI") or "").strip().lower()
            doi_raw = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', doi_raw)
            obj.doi = doi_raw.rstrip(".,;)") or None
            # Raw string
            obj.raw = ref.get("unstructured", "")
            # Extract DOI from unstructured if structured field missing
            if not obj.doi and obj.raw:
                m = _DOI_RE.search(obj.raw)
                if m:
                    obj.doi = m.group(1).rstrip(".,;)").lower()
            # Title
            title = ref.get("article-title") or ref.get("volume-title") or ""
            obj.title = title.strip() or None
            # Year
            obj.year = None
            year_str = str(ref.get("year", ""))
            m = _YEAR_RE.search(year_str)
            if m:
                obj.year = int(m.group(1))
            # Bibliographic metadata for exact-match search (Phase 2)
            obj.journal    = (ref.get("journal-title") or "").strip() or None
            obj.volume     = str(ref.get("volume", "") or "").strip() or None
            _fp_raw        = str(ref.get("first-page", "") or ref.get("page", "") or "")
            obj.first_page = _fp_raw.split("-")[0].strip() or None
            _auth_raw      = ref.get("author", "") or ""
            obj.first_author = _auth_raw.split(",")[0].strip() or None
            refs.append(obj)

        return refs or None
    except Exception:
        return None


# ── GROBID ───────────────────────────────────────────────────────────────────

def grobid_process(pdf_path: pathlib.Path) -> Optional[str]:
    import time as _t
    for _attempt in range(3):          # retry transient GROBID timeouts / 5xx / resets
        try:
            with pdf_path.open("rb") as fh:
                resp = requests.post(
                    GROBID_URL,
                    files={"input": (pdf_path.name, fh, "application/pdf")},
                    data={"consolidateHeader": "0", "consolidateCitations": "0",
                          "includeRawCitations": "1"},
                    timeout=GROBID_TIMEOUT,
                )
            if resp.status_code == 200:
                return resp.text
        except Exception:
            pass
        _t.sleep(3)
    return None


# ── Verification ─────────────────────────────────────────────────────────────

# ── Heuristic non-academic reference filter ───────────────────────────────────
# Implements the equivalent of Zhao et al.'s (2026) GPT-4o-mini cleaning pass
# using rule-based patterns.  These patterns identify non-academic sources
# (websites, reports, gray literature) that cause false positives in the
# unmatched-citation count.

_H_URL_RE = re.compile(r'https?://\S{10,}', re.I)
_H_ACCESS_RE = re.compile(
    r'\b(accessed|retrieved|last\s+visited|last\s+access|available\s+at'
    r'|available\s+from|online\s+at|available\s+online)\b',
    re.I,
)
_H_NONACAD_RE = re.compile(
    r'\b(wikipedia\.org|github\.com|github\.io|stackoverflow\.com'
    r'|medium\.com|twitter\.com|youtube\.com|reddit\.com'
    r'|cdc\.gov|who\.int|fda\.gov|cms\.gov|hhs\.gov'
    r'|ourworldindata\.org|statista\.com)\b',
    re.I,
)


# Author/affiliation fragments that GROBID over-segments out of consortium author
# lists or address blocks (no title, no year) -- noise, not citations.
_H_AFFIL_RE = re.compile(
    r"\([^)]*\b(?:University|Universit[e\u00e9]|Institut\w*|College|Hospital|Laborator"
    r"|Department|Ministry|Organi[sz]ation|Collaboration|Cent(?:er|re)\w*|School|Foundation"
    r"|Council|Agency|Consortium|Group)\b", re.IGNORECASE)
_H_ADDRESS_RE = re.compile(
    r"\b(?:Drive|Street|Road|Avenue|Ave\.|Boulevard|Blvd|Lane)\b[^.]*\b[A-Z]{2}\b\s*\d{5}"
    r"|\b\d{5}(?:-\d{4})?\b[^.]*\bUSA\b", re.IGNORECASE)
_H_NAMEAFFIL_RE = re.compile(r"^[A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]*){1,4}\s*\([^)]*\)\s*\*?\s*;?\s*$")
_H_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def is_likely_nonacademic(ref) -> bool:
    """Return True if this unmatched reference is likely a non-academic source.

    Conservative: only fires on clear, unambiguous signals so that genuine
    academic references with URLs (e.g. arXiv links) pass through if they
    carry a DOI or a GROBID-extracted title.
    """
    if ref.doi:        # DOI-bearing refs are always academic
        return False
    raw = ref.raw or ""
    if _H_URL_RE.search(raw) and _H_ACCESS_RE.search(raw) and not ref.title:   # "accessed" + URL, no title
        return True
    if _H_NONACAD_RE.search(raw):                             # known non-academic site
        return True
    if _H_ACCESS_RE.search(raw) and not ref.title:            # "available at" with no title
        return True
    if len(raw.strip()) < 15 and not ref.title:               # near-empty parse artifact
        return True
    # Author/affiliation fragment: no title, no year, institution parenthetical or address.
    if (not ref.title and not _H_YEAR_RE.search(raw)
            and (_H_AFFIL_RE.search(raw) or _H_ADDRESS_RE.search(raw))):
        return True
    return False



def _journals_match(query: str, result: str) -> bool:
    """Return True if query and result journal names likely refer to the same journal.

    Handles common GROBID abbreviations via prefix matching:
      "Nat" matches "Nature"; "Med" matches "Medicine"; "Phys" matches "Physics".
    If either side is empty, returns True (don't reject on missing data).
    """
    if not query or not result:
        return True
    # Primary: local journal authority (ISSN-based resolution of canonical names,
    # ISO-4 abbreviations, alternate titles, and acronyms).
    try:
        from journal_authority import same_journal
        return same_journal(query, result)
    except Exception:
        pass
    # Fallback: simple prefix heuristic if the authority DB is unavailable.
    import re as _re
    def _words(s):
        return [w for w in _re.sub(r'[^\w]', ' ', s.lower()).split() if len(w) >= 3]
    qwords = _words(query)
    rwords = _words(result)
    if not qwords or not rwords:
        return True
    for qw in qwords:
        for rw in rwords:
            if rw.startswith(qw) or qw.startswith(rw):
                return True
    return False


def _openalex_get(params: dict, headers: dict, max_retries: int = 3) -> list:
    """GET OpenAlex /works with automatic retry on 429 (rate-limit) responses."""
    import time as _time
    for attempt in range(max_retries):
        try:
            resp = requests.get(
                OPENALEX_API, params=params, headers=headers,
                timeout=CROSSREF_TIMEOUT,
            )
            if resp.status_code == 200:
                return resp.json().get("results", [])
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 2 * (attempt + 1)))
                _time.sleep(wait)
                continue
        except Exception:
            pass
        break
    return []


def openalex_by_metadata(refs: list) -> list:
    """Phase 2: exact-field citation lookup via OpenAlex API.

    Strategy A (most precise): filter on year + volume + first_page.
      OpenAlex indexed these as exact integer fields; the combination is nearly
      unique across 250M works.  Journal name verified client-side.

    Strategy B (fallback when no volume/page): search query on journal+author
      filtered by year, then verify journal name client-side.

    No fuzzy title matching.  Boss instruction: exact match only.
    """
    from solr_lookup import MatchMethod, SolrResult
    _OA_HEADERS = {"User-Agent": f"FakeCitationDetector/1.0 (mailto:{CROSSREF_EMAIL})"}
    results = [SolrResult(found=False, method=MatchMethod.NOT_FOUND) for _ in refs]

    for i, ref in enumerate(refs):
        journal      = getattr(ref, 'journal',      None)
        year         = getattr(ref, 'year',         None)
        volume       = getattr(ref, 'volume',       None)
        first_page   = getattr(ref, 'first_page',   None)
        first_author = getattr(ref, 'first_author', None)

        if not year:
            continue

        matched = False

        # ── Strategy A: exact filter on year + volume + first_page ───────────
        if volume and first_page and not matched:
            filt = f"publication_year:{year},biblio.volume:{volume},biblio.first_page:{first_page}"
            items = _openalex_get(
                {"filter": filt, "per-page": "10", "mailto": CROSSREF_EMAIL},
                _OA_HEADERS,
            )
            for item in items:
                item_j = ((item.get("primary_location") or {})
                          .get("source") or {}).get("display_name", "")
                if _journals_match(journal, item_j):
                    results[i] = SolrResult(
                        found=True, method=MatchMethod.META_MATCH,
                        record=item, confidence=1.0,
                    )
                    matched = True
                    break

        # ── Strategy B: search by journal + author, filter by year ───────────
        if not matched and journal:
            q_parts = [journal]
            if first_author:
                q_parts.append(first_author)
            items = _openalex_get(
                {"search": " ".join(q_parts),
                 "filter": f"publication_year:{year}",
                 "per-page": "5",
                 "mailto": CROSSREF_EMAIL},
                _OA_HEADERS,
            )
            for item in items:
                if first_page:
                    item_fp = (item.get("biblio") or {}).get("first_page", "")
                    if item_fp and str(item_fp) != str(first_page):
                        continue
                item_j = ((item.get("primary_location") or {})
                          .get("source") or {}).get("display_name", "")
                if _journals_match(journal, item_j):
                    results[i] = SolrResult(
                        found=True, method=MatchMethod.META_MATCH,
                        record=item, confidence=1.0,
                    )
                    matched = True
                    break

    return results


def solr_by_metadata(refs: list, solr) -> list:
    """Phase 2.6: LOCAL exact-match on journal + year + volume + first-author surname
    against the OpenAlex Solr index. The journal name (full / ISO-4 abbreviation /
    alternate / acronym) is resolved to venue_id(s) via the journal authority
    (MongoDB-backed, multi-source), so shortened & alternate journal names match
    exactly. No fuzzy title matching; uses only local indexes (no metered API)."""
    from solr_lookup import MatchMethod, SolrResult, _solr_get
    try:
        from journal_authority import venue_ids_for
    except Exception:
        return [SolrResult(found=False, method=MatchMethod.NOT_FOUND) for _ in refs]

    def _esc(s):
        return re.sub(r'([+\-!(){}\[\]^"~*?:\\/])', r'\\\1', str(s))

    results = [SolrResult(found=False, method=MatchMethod.NOT_FOUND) for _ in refs]
    for i, ref in enumerate(refs):
        year   = getattr(ref, "year", None)
        volume = getattr(ref, "volume", None)
        journal = getattr(ref, "journal", None)
        author = getattr(ref, "first_author", None)
        if not (year and volume and journal and author):
            continue
        vids = venue_ids_for(journal)
        if not vids:
            continue
        vq = " OR ".join(vids)
        params = {
            "q":     f"venue_id:({vq})",
            "fq":    [f"publication_year:{int(year)}",
                      f'volume:"{_esc(volume)}"',
                      f"author_names:{_esc(author)}"],
            "fl":    "id,doi,volume,publication_year,author_names,title,venue_id",
            "rows":  "3", "wt": "json", "facet": "false",
        }
        try:
            resp = _solr_get(params)["response"]
        except Exception:
            continue
        # Uniqueness guard: accept ONLY an unambiguous single hit. Multiple hits mean
        # venue+year+volume+author is not unique (e.g. common surname in a high-volume
        # journal) -> reject, to avoid a wrong-paper or fabricated-citation false match.
        if resp.get("numFound") == 1 and resp.get("docs"):
            results[i] = SolrResult(found=True, method=MatchMethod.META_MATCH,
                                    record=resp["docs"][0], confidence=1.0)
    return results


def solr_by_metadata(refs: list, solr) -> list:
    """Phase 2.6: LOCAL exact-match on journal + year + volume + first-author surname
    against the OpenAlex Solr index. The journal name (full / ISO-4 abbreviation /
    alternate / acronym) is resolved to venue_id(s) via the journal authority
    (MongoDB-backed, multi-source), so shortened & alternate journal names match
    exactly. No fuzzy title matching; uses only local indexes (no metered API)."""
    from solr_lookup import MatchMethod, SolrResult, _solr_get
    try:
        from journal_authority import venue_ids_for
    except Exception:
        return [SolrResult(found=False, method=MatchMethod.NOT_FOUND) for _ in refs]

    def _esc(s):
        return re.sub(r'([+\-!(){}\[\]^"~*?:\\/])', r'\\\1', str(s))

    results = [SolrResult(found=False, method=MatchMethod.NOT_FOUND) for _ in refs]
    for i, ref in enumerate(refs):
        year   = getattr(ref, "year", None)
        volume = getattr(ref, "volume", None)
        journal = getattr(ref, "journal", None)
        author = getattr(ref, "first_author", None)
        if not (year and volume and journal and author):
            continue
        vids = venue_ids_for(journal)
        if not vids:
            continue
        vq = " OR ".join(vids)
        params = {
            "q":     f"venue_id:({vq})",
            "fq":    [f"publication_year:{int(year)}",
                      f'volume:"{_esc(volume)}"',
                      f"author_names:{_esc(author)}"],
            "fl":    "id,doi,volume,publication_year,author_names,title,venue_id",
            "rows":  "3", "wt": "json", "facet": "false",
        }
        try:
            resp = _solr_get(params)["response"]
        except Exception:
            continue
        # Uniqueness guard: accept ONLY an unambiguous single hit. Multiple hits mean
        # venue+year+volume+author is not unique (e.g. common surname in a high-volume
        # journal) -> reject, to avoid a wrong-paper or fabricated-citation false match.
        if resp.get("numFound") == 1 and resp.get("docs"):
            results[i] = SolrResult(found=True, method=MatchMethod.META_MATCH,
                                    record=resp["docs"][0], confidence=1.0)
    return results


def crossref_by_metadata(refs: list) -> list:
    """Phase 3: exact bibliographic metadata match via the FREE Crossref API.

    Matches journal + year + volume + page + first-author, ALL exact. Journal names
    (full / ISO-4 abbreviation / alternate / acronym) are compared via the synonym
    authority. No fuzzy title matching. Reached only after DOI matching is exhausted,
    so it handles the DOI-less remainder. Uses the free polite-pool Crossref API (NOT
    the metered OpenAlex API); rate-limited, so slower than local indexes at scale."""
    from solr_lookup import MatchMethod, SolrResult
    try:
        from journal_authority import same_journal
    except Exception:
        def same_journal(a, b):
            return True

    results = [SolrResult(found=False, method=MatchMethod.NOT_FOUND) for _ in refs]
    for i, ref in enumerate(refs):
        journal = getattr(ref, "journal", None)
        year    = getattr(ref, "year", None)
        volume  = getattr(ref, "volume", None)
        page    = getattr(ref, "first_page", None)
        author  = getattr(ref, "first_author", None)
        # Need year + journal + author + at least one of (volume, page) to be discriminating.
        if not (year and journal and author and (volume or page)):
            continue
        params = {
            "query.bibliographic": f"{journal} {author}",
            "filter": f"from-pub-date:{int(year)}-01-01,until-pub-date:{int(year)}-12-31",
            "rows":   "20",
            "select": "DOI,container-title,volume,page,author,published",
        }
        try:
            resp = requests.get(
                CROSSREF_API, params=params,
                headers={"User-Agent": f"FakeCitationDetector/1.0 (mailto:{CROSSREF_EMAIL})"},
                timeout=CROSSREF_TIMEOUT,
            )
            if resp.status_code != 200:
                continue
            items = resp.json().get("message", {}).get("items", [])
        except Exception:
            continue
        for it in items:
            # journal exact (via synonym authority) — candidate MUST carry a matching title
            ct = it.get("container-title") or []
            ic = ct[0] if ct else ""
            if journal and (not ic or not same_journal(journal, ic)):
                continue
            # volume exact — if the citation has a volume, the candidate must match it
            # (a candidate lacking a volume cannot be an exact volume match -> reject)
            if volume:
                iv = str(it.get("volume") or "").strip()
                if iv != str(volume).strip():
                    continue
            # first page exact — same rule: missing candidate page -> reject
            if page:
                ip = str(it.get("page") or "").split("-")[0].strip()
                if ip != str(page).strip():
                    continue
            # first-author surname must appear among the work's authors
            authors = it.get("author") or []
            names = " ".join(((a.get("family") or "") + " " + (a.get("given") or ""))
                             for a in authors).lower()
            if author.lower() not in names:
                continue
            results[i] = SolrResult(found=True, method=MatchMethod.META_MATCH,
                                    record=it, confidence=1.0)
            break
    return results


def validate_metadata(ref, record) -> list:
    """Compare a citation's metadata against its matched record; return a list of
    per-field discrepancy strings (cited vs actual). A reference that resolves to a
    real paper but carries wrong year / journal / volume / author is FOUND_MISMATCH
    -- a common hallucination signature (real DOI, garbled surrounding metadata)."""
    if not record:
        return []
    def _first(v):
        if isinstance(v, list):
            return v[0] if v else None
        return v
    issues = []
    # Year: exact, with +/-1 tolerance (online-first vs print)
    cy = getattr(ref, "year", None)
    ry = _first(record.get("publication_year")) or _first(record.get("year"))
    if cy and ry:
        try:
            d = abs(int(cy) - int(ry))
            if d > 1:
                issues.append(f"year: cited {cy}, actual {ry} (off by {d})")
        except (ValueError, TypeError):
            pass
    # Journal: compared via the synonym authority (not fuzzy)
    cj = getattr(ref, "journal", None)
    rj = (_first(record.get("venue_name")) or _first(record.get("container-title"))
          or _first(record.get("journal")))
    if cj and rj:
        # Flag ONLY when confident the journals differ (both resolve to DIFFERENT
        # identities). If either name does not resolve (ambiguous abbreviation), give
        # the benefit of the doubt -- do not raise a false mismatch.
        try:
            from journal_authority import resolve as _jresolve
            _rc, _rr = _jresolve(cj), _jresolve(rj)
            if _rc and _rr and _rc != _rr:
                issues.append(f"journal: cited '{cj}', actual '{rj}'")
        except Exception:
            pass
    # Volume: exact
    cv = getattr(ref, "volume", None)
    rv = _first(record.get("volume"))
    def _vnorm(v):
        m = re.match(r"\s*([0-9A-Za-z]+)", str(v))
        return (m.group(1).lower() if m else str(v).strip().lower())
    if cv and rv and _vnorm(cv) != _vnorm(rv):
        issues.append(f"volume: cited '{cv}', actual '{rv}'")
    # First-author surname must appear among the paper's authors. Cited names vary
    # ("AV Raveendran", "Raveendran AV", "Raveendran, A"), so extract the surname as
    # the longest alphabetic token and token-match it against the actual authors
    # (diacritic-folded). Lenient by design: only flag when the surname is clearly absent.
    ca = getattr(ref, "first_author", None)
    ra = record.get("author_names") or record.get("author") or []
    if ca and ra:
        import unicodedata as _ud
        def _fold(x): return "".join(c for c in _ud.normalize("NFKD", str(x).lower()) if not _ud.combining(c))
        actual_tokens = set()
        for a in (ra if isinstance(ra, list) else [ra]):
            a = ((a.get("family") or "") + " " + (a.get("given") or "")) if isinstance(a, dict) else str(a)
            for t in re.split(r"[^A-Za-z]+", _fold(a)):
                if len(t) >= 3:
                    actual_tokens.add(t)
        ca_tokens = [t for t in re.split(r"[^A-Za-z]+", _fold(ca)) if len(t) >= 3]
        surname = max(ca_tokens, key=len) if ca_tokens else None
        if surname and actual_tokens and surname not in actual_tokens:
            issues.append(f"author: cited first-author '{ca}' not among actual authors")

    return issues


BIBLIO_DB = str(pathlib.Path.home() / "crossref" / "biblio_index.db")

def _biblio_surname_match(cited, actual) -> bool:
    import unicodedata as _ud
    def _fold(x): return "".join(c for c in _ud.normalize("NFKD", str(x).lower()) if not _ud.combining(c))
    ct = [t for t in re.split(r"[^A-Za-z]+", _fold(cited)) if len(t) >= 3]
    if not ct:
        return True
    surname = max(ct, key=len)
    at = set(t for t in re.split(r"[^A-Za-z]+", _fold(actual)) if len(t) >= 3)
    return (not at) or (surname in at)


def biblio_by_metadata(refs: list) -> list:
    """Phase 3 (full 5-field): exact match on journal + year + volume + page + first-
    author against the local Crossref bibliographic index (115M records WITH page).
    Journal names are resolved to canonical form via the synonym authority. No fuzzy
    title. Local & free, and includes the page field OpenAlex Solr lacks / Crossref's
    API cannot query. When the citation carries a page, page uniquely discriminates;
    without a page, an author match must be unique to be accepted (precision guard)."""
    from solr_lookup import MatchMethod, SolrResult
    import os, sqlite3
    try:
        from journal_authority import _norm as _jn, canonical_name
    except Exception:
        return [SolrResult(found=False, method=MatchMethod.NOT_FOUND) for _ in refs]
    if not os.path.exists(BIBLIO_DB):
        return [SolrResult(found=False, method=MatchMethod.NOT_FOUND) for _ in refs]
    con = sqlite3.connect(f"file:{BIBLIO_DB}?mode=ro", uri=True, check_same_thread=False)
    results = [SolrResult(found=False, method=MatchMethod.NOT_FOUND) for _ in refs]
    try:
        for i, ref in enumerate(refs):
            journal = getattr(ref, "journal", None)
            year    = getattr(ref, "year", None)
            volume  = getattr(ref, "volume", None)
            page    = getattr(ref, "first_page", None)
            author  = getattr(ref, "first_author", None)
            if not (year and volume and journal and (page or author)):
                continue
            norms = set()
            k = _jn(journal)
            if k:
                norms.add(k)
            cn = canonical_name(journal)
            if cn:
                ck = _jn(cn)
                if ck:
                    norms.add(ck)
            cand = []
            for nm in norms:
                try:
                    cand += con.execute(
                        "SELECT doi,first_page,article_num,author1 FROM biblio "
                        "WHERE journal_norm=? AND year=? AND volume=?",
                        (nm, int(year), str(volume).strip())).fetchall()
                except Exception:
                    pass
            def _pn(x):
                x = str(x or "").strip().lower()
                return x[1:] if (x[:1] == "e" and x[1:].isdigit()) else x
            cp = _pn(page) if page else None
            matches = []
            for doi, fp, art, a1 in cand:
                if page:
                    # modern journals use ARTICLE NUMBERS (Crossref article-number),
                    # not page ranges; match the cited page against page OR article-number
                    if cp and (cp == _pn(fp) or cp == _pn(art)) and (not author or _biblio_surname_match(author, a1)):
                        matches.append((doi, fp, a1))
                elif author and _biblio_surname_match(author, a1):
                    matches.append((doi, fp, a1))
            uniq = set(m[0] for m in matches)
            if matches and (page or len(uniq) == 1):
                d, fp, a1 = matches[0]
                results[i] = SolrResult(found=True, method=MatchMethod.META_MATCH,
                                        record={"doi": d, "first_page": fp, "author1": a1}, confidence=1.0)
    finally:
        con.close()
    return results


_TITLE_XR = None
def _get_title_xr():
    """Exact-title backend: MongoLookup over the crossref.title_norm index (fast, indexed,
    ~1500 lookups/sec) with a local-Crossref SQLite fallback if Mongo is unreachable."""
    global _TITLE_XR
    if _TITLE_XR is None:
        try:
            from mongo_lookup import MongoLookup
            _TITLE_XR = MongoLookup()
        except Exception:
            from crossref_lookup import CrossrefLookup
            _TITLE_XR = CrossrefLookup()
    return _TITLE_XR


_INTEGRATED = None
def _get_integrated(solr):
    """Federated Crossref+OpenAlex accessor, reusing the pipeline's SolrLookup."""
    global _INTEGRATED
    if _INTEGRATED is None:
        from integrated_lookup import IntegratedLookup
        _INTEGRATED = IntegratedLookup(solr=solr)
    return _INTEGRATED


def verify_refs(refs: list, solr, vector_lookup=None) -> dict:
    """Run 4-phase verification; return summary counts dict."""
    from solr_lookup import MatchMethod, SolrResult

    n = len(refs)
    results = [None] * n

    # Phases 1-2 (DOI): single federated accessor across BOTH corpora (OpenAlex Solr +
    # Crossref), via IntegratedLookup. Preserves the original ordering (exhaust both DOI
    # indexes before any weaker metadata/title match), the batched Crossref lookup, and the
    # SolrResult contract. DOI is exact/authoritative, so this runs first.
    integrated = _get_integrated(solr)
    for i, r in enumerate(integrated.verify_dois(refs)):
        if r is not None and r.found:
            results[i] = r

    # Phase 3: exact metadata match via local OpenAlex Solr (venue+year+volume+author,
    # journal resolved through the synonym authority) — reached only for references still
    # unmatched after BOTH DOI phases. Exact fields only, no fuzzy title. A uniqueness
    # guard (single-hit-only) protects precision. NOTE: Crossref's API cannot filter by
    # volume/page (relevance search only), so it cannot do exact structured retrieval;
    # OpenAlex is the only source that supports exact metadata queries. The local Solr
    # index lacks a page field, so this is a 4-field match (page unavailable locally).
    import os as _os
    if _os.environ.get("DISABLE_META") != "1":
        meta_idx = [i for i in range(n) if results[i] is None
                    and getattr(refs[i], "year", None)
                    and getattr(refs[i], "journal", None)
                    and getattr(refs[i], "volume", None)
                    and (getattr(refs[i], "first_page", None) or getattr(refs[i], "first_author", None))]
        if meta_idx:
            for i, r in zip(meta_idx, biblio_by_metadata([refs[i] for i in meta_idx])):
                if r.found:
                    results[i] = r
            # OpenAlex metadata fallback (federated) for meta-eligible refs the Crossref
            # biblio index did not resolve: venue_id + volume + first_page + year, asserted
            # only on a unique hit. Recovers OpenAlex-only works with structured metadata.
            for i in [k for k in meta_idx if results[k] is None]:
                r = integrated.oa_by_metadata(refs[i])
                if r.found:
                    results[i] = r

    # Phase 3 removed: local Crossref index (Phase 2.5) resolves 80-90% of citations;
    # individual Solr GETs cause 599+ timeouts per 25 papers even at 8s each,
    # wasting 3+ min/paper for ~1-2% marginal recall. FAISS (Phase 4) catches the rest.

    # Phase 4: vector
    vec_found = vec_total = 0
    if vector_lookup:
        vec_candidates = [i for i in range(n) if results[i] is None and refs[i].title]
        vec_total = len(vec_candidates)
        for i in vec_candidates:
            r = vector_lookup.by_title(refs[i].title, year=refs[i].year)
            if r.found:
                results[i] = r
                vec_found += 1

    # Phase 5: Heuristic non-academic filter (Zhao et al. 2026 cleaning step)
    # Count unmatched refs that are clearly non-academic (websites, reports, etc.).
    # These are false positives — not hallucinations — so we track them separately.
    heuristic_filtered = sum(
        1 for i in range(n)
        if results[i] is None and is_likely_nonacademic(refs[i])
    )

    # Phase 3.9: EXACT-title match (per directive). For references still unmatched that
    # carry a title, look up the local Crossref title index by EXACT NORMALIZED title
    # (case/punctuation/whitespace folded — deterministic, NOT fuzzy) with a +/-1 year
    # guard. Titles are near-unique, so this is a safe exact match, run only as the last
    # resort after DOI and structured metadata. Enabled by default; set
    # DISABLE_TITLE_MATCH=1 for fast bulk runs (the Crossref index is on network storage,
    # ~11 lookups/sec, so at corpus scale move it to SSD or disable this phase).
    if _os.environ.get("DISABLE_TITLE_MATCH") != "1":
        try:
            from solr_lookup import MatchMethod as _MM, SolrResult as _SR
            _txr = integrated  # federated Crossref title_norm + OpenAlex title_exact
            for _ti in range(n):
                if results[_ti] is not None:
                    continue
                _tr = refs[_ti]
                _tt = getattr(_tr, "title", None)
                if not _tt or is_likely_nonacademic(_tr):
                    continue
                if hasattr(_txr, "by_title_exact"):
                    _hit = _txr.by_title_exact(
                        _tt, year=getattr(_tr, "year", None),
                        journal=getattr(_tr, "journal", None),
                        author=getattr(_tr, "first_author", None)
                                or getattr(_tr, "author", None))
                else:
                    _hit = _txr.by_title(_tt, getattr(_tr, "year", None))
                if _hit and _hit.found:
                    results[_ti] = _SR(found=True, method=_MM.TITLE_EXACT,
                                       record=_hit.record, confidence=_hit.confidence)
        except Exception:
            pass

    not_found_sentinel = SolrResult(found=False, method=MatchMethod.NOT_FOUND)
    final = [results[i] or not_found_sentinel for i in range(n)]

    found = sum(1 for r in final if r.found)
    by_method: dict[str, int] = {}
    for r in final:
        k = r.method.value
        by_method[k] = by_method.get(k, 0) + 1

    # Metadata mismatch detection: for each matched reference, diff the cited metadata
    # against the matched record. Flags real-paper-but-wrong-metadata (FOUND_MISMATCH).
    mismatches = []
    for _i in range(n):
        _r = results[_i]
        if _r is None or not _r.found or not getattr(_r, "record", None):
            continue
        _iss = validate_metadata(refs[_i], _r.record)
        # Guard against OpenAlex duplicate-DOI records: if the cited metadata matches
        # ANY record sharing this DOI, it is not a real mismatch (the matched record was
        # just the wrong duplicate).
        if _iss and getattr(refs[_i], "doi", None):
            for _alt in solr.all_by_doi(refs[_i].doi):
                if not validate_metadata(refs[_i], _alt):
                    _iss = []
                    break
        if _iss:
            mismatches.append({"cited_doi": getattr(refs[_i], "doi", None),
                               "method": _r.method.value, "issues": _iss})

    not_found_count = n - found
    return {
        "total":               n,
        "found":               found,
        "not_found":           not_found_count,
        "heuristic_filtered":  heuristic_filtered,
        "not_found_academic":  not_found_count - heuristic_filtered,
        "vec_tried":           vec_total,
        "vec_found":           vec_found,
        "by_method":           by_method,
        "found_mismatch":      len(mismatches),
        "mismatches":          mismatches,
    }


# ── Main loop ────────────────────────────────────────────────────────────────

def run_pipeline(manifest_path: str, out_path: str, no_vector: bool = False, workers: int = 1):
    manifest = [json.loads(l) for l in pathlib.Path(manifest_path).open()]
    out_file  = pathlib.Path(out_path)
    done_dois = set()
    if out_file.exists():
        for l in out_file.open():
            try: done_dois.add(json.loads(l)["doi"])
            except Exception: pass

    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from solr_lookup import SolrLookup
    solr = SolrLookup()

    vector_lookup = None
    if not no_vector:
        try:
            from vector_lookup import VectorLookup, _get_model
            vector_lookup = VectorLookup()
            _get_model()  # warm up
            print("Vector model loaded.")
        except ImportError:
            print("sentence-transformers not available — skipping Phase 4.")

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    total = len(manifest)
    pending = [(idx, p) for idx, p in enumerate(manifest, 1)
               if p["doi"] not in done_dois]

    def _process_paper(args):
        idx, paper = args
        doi   = paper["doi"]
        url   = paper["oa_url"]
        field = paper["field"]
        year  = paper["year"]

        print(f"[{idx}/{total}] {paper['journal_name']} {year}  {doi[:50]}", flush=True)

        # Fast path: Crossref reference list (~300ms, no PDF needed)
        refs = crossref_refs(doi)
        ref_source = "crossref"

        if refs is None:
            # SKIP_SLOW_PATH=1 (or empty oa_url): skip PDF download + GROBID entirely.
            # Used for local-only large-scale runs where only DOIs are sampled.
            import os as _os
            if _os.environ.get('SKIP_SLOW_PATH') == '1' or not url:
                return {**paper, "status": "skipped_no_refs", "total": 0,
                        "found": 0, "not_found": 0, "ref_source": "none"}
            # Slow path: download PDF and run GROBID (~15-20s)
            ref_source = "grobid"
            safe_doi = doi.replace("/", "_").replace(".", "_")
            dest = DOWNLOAD_DIR / field / str(year) / safe_doi
            dest.parent.mkdir(parents=True, exist_ok=True)

            pdf_path = download_pdf(doi, url, dest)
            if pdf_path is None:
                print(f"  ✗ download failed")
                return {**paper, "status": "download_failed", "total": 0, "found": 0,
                        "not_found": 0, "ref_source": ref_source}

            if pdf_path.suffix != ".pdf":
                print(f"  ✗ not a PDF ({pdf_path.suffix}) — skipping GROBID")
                return {**paper, "status": "not_pdf", "total": 0, "found": 0,
                        "not_found": 0, "ref_source": ref_source}

            tei = grobid_process(pdf_path)
            if not tei:
                print(f"  ✗ GROBID failed")
                return {**paper, "status": "grobid_failed", "total": 0, "found": 0,
                        "not_found": 0, "ref_source": ref_source}

            refs = parse_tei_refs(tei)

        if not refs:
            print(f"  ✗ no references extracted")
            return {**paper, "status": "no_refs", "total": 0, "found": 0,
                    "not_found": 0, "ref_source": ref_source}

        counts = verify_refs(refs, solr, vector_lookup)
        pct = 100 * counts["found"] / counts["total"] if counts["total"] else 0
        print(f"  ✓ [{ref_source}] {counts['total']} refs  found={counts['found']} ({pct:.0f}%)  "
              f"not_found={counts['not_found']}")
        return {**paper, "status": "ok", **counts, "ref_source": ref_source}

    with out_file.open("a") as out_fh:
        if workers == 1:
            for args in pending:
                row = _process_paper(args)
                out_fh.write(json.dumps(row) + "\n"); out_fh.flush()
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for row in pool.map(_process_paper, pending):
                    out_fh.write(json.dumps(row) + "\n"); out_fh.flush()


# ── Summarise ────────────────────────────────────────────────────────────────

def summarise(results_path: str):
    rows = [json.loads(l) for l in pathlib.Path(results_path).open()
            if json.loads(l).get("status") == "ok" and json.loads(l).get("total", 0) > 0]

    # Aggregate by year and field
    from collections import defaultdict
    def _zero(): return {"papers": 0, "total": 0, "not_found": 0, "not_found_academic": 0, "heuristic_filtered": 0}
    by_year:  dict = defaultdict(_zero)
    by_field: dict = defaultdict(_zero)
    matrix:   dict = defaultdict(_zero)

    for r in rows:
        year  = r["year"]
        field = r["field"]
        total = r["total"]
        nf    = r["not_found"]

        nfa = r.get("not_found_academic", nf)  # fallback for pre-filter results
        hf  = r.get("heuristic_filtered", 0)
        for d in (by_year[year], by_field[field], matrix[(year, field)]):
            d["papers"] += 1; d["total"] += total
            d["not_found"] += nf
            d["not_found_academic"] += nfa
            d["heuristic_filtered"] += hf

    # ── By year ──
    print("\n" + "=" * 60)
    print("NOT-FOUND RATE BY YEAR (all fields combined)")
    print("=" * 60)
    print(f"  {'Year':<6} {'Papers':>7} {'Citations':>10} {'Not found':>10} {'Rate':>7} {'Acad-only':>10} {'Acad rate':>10}")
    print("  " + "-" * 65)
    for year in sorted(by_year):
        d = by_year[year]
        rate  = 100 * d["not_found"] / d["total"] if d["total"] else 0
        arate = 100 * d["not_found_academic"] / d["total"] if d["total"] else 0
        print(f"  {year:<6} {d['papers']:>7} {d['total']:>10,} {d['not_found']:>10,} {rate:>6.1f}%"
              f"  {d['not_found_academic']:>9,} {arate:>9.1f}%  (filtered {d['heuristic_filtered']:,})")

    # ── By field ──
    print("\n" + "=" * 60)
    print("NOT-FOUND RATE BY FIELD (all years combined)")
    print("=" * 60)
    print(f"  {'Field':<25} {'Papers':>7} {'Citations':>10} {'Not found':>10} {'Rate':>7}")
    print("  " + "-" * 55)
    for field in sorted(by_field):
        d = by_field[field]
        rate = 100 * d["not_found"] / d["total"] if d["total"] else 0
        print(f"  {field:<25} {d['papers']:>7} {d['total']:>10,} {d['not_found']:>10,} {rate:>6.1f}%")

    # ── Matrix: year × field ──
    fields = sorted(by_field.keys())
    years  = sorted(by_year.keys())
    print("\n" + "=" * 70)
    print("NOT-FOUND RATE MATRIX: year (rows) × field (columns)")
    print("=" * 70)
    header = f"  {'Year':<6}" + "".join(f" {f[:12]:>13}" for f in fields)
    print(header)
    print("  " + "-" * (6 + 13 * len(fields)))
    for year in years:
        row = f"  {year:<6}"
        for field in fields:
            d = matrix.get((year, field))
            if d and d["total"] > 0:
                rate = 100 * d["not_found"] / d["total"]
                row += f" {rate:>12.1f}%"
            else:
                row += f" {'—':>13}"
        print(row)

    print(f"\n  Total papers in analysis: {sum(d['papers'] for d in by_year.values())}")
    print(f"  Total citations:          {sum(d['total'] for d in by_year.values()):,}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", nargs="?", default="",
                    help="JSONL manifest from sample_papers.py")
    ap.add_argument("--out",        default="cross_year_results.jsonl")
    ap.add_argument("--no-vector",  action="store_true")
    ap.add_argument("--workers",   type=int, default=1,
                    help="Papers to process concurrently (default 1)")
    ap.add_argument("--summarise",  metavar="RESULTS_JSONL",
                    help="Skip download/verify; just print summary table from existing results.")
    args = ap.parse_args()

    if args.summarise:
        summarise(args.summarise)
        return

    if not args.manifest:
        ap.print_help()
        sys.exit(1)

    run_pipeline(args.manifest, args.out, no_vector=args.no_vector, workers=args.workers)
    print("\nDone. Run with --summarise to print comparison tables.")


if __name__ == "__main__":
    main()
