"""
citation_parser.py — parse a free-text academic citation string into structured fields.

Strategy
--------
1. Extract DOI via regex (most reliable field).
2. Extract year via regex (4-digit, 1800–2099).
3. Extract title using a cascade of heuristics:
   a. Text inside double-quotes (some styles use them explicitly).
   b. GROBID /api/processCitation if the server is reachable.
   c. Heuristic: strip author block and volume/page noise, take the
      longest remaining sentence-like chunk.
4. Extract authors (best-effort; used for display only).
5. Extract journal (best-effort; used for display only).

The title extraction is deliberately conservative: when uncertain we
return MORE of the string rather than less, because the downstream
vector search is tolerant of extra words.

Usage
-----
    from citation_parser import parse_citation

    result = parse_citation(
        "Smith J, Johnson A, Williams B. Characteristics of hospitalized "
        "patients with COVID-19. JAMA. 2020;324(12):1205-1216."
    )
    # result.title  → "Characteristics of hospitalized patients with COVID-19"
    # result.year   → 2020
    # result.doi    → None
    # result.raw    → original string
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GROBID_URL = "http://galaxy:8070/api/processCitation"

_DOI_RE = re.compile(
    r'\b(10\.\d{4,}/[^\s,;)\]>]+)',
    re.IGNORECASE,
)

_YEAR_RE = re.compile(r'\b(1[89]\d{2}|20[012]\d)\b')

# Author-block heuristics: "Smith J" / "Smith, J." / "Smith JA" / "Smith, John"
# A crude but useful signal: author tokens tend to look like "Word Word," or "Word W."
_AUTHOR_BLOCK_RE = re.compile(
    r'^(?:[A-Z][a-zA-Zéàüöä\-\']+(?:,\s*(?:[A-Z]\.?\s*){1,3})?'
    r'(?:\s*[,;&]\s*)?){1,10}',
)

# Volume/pages noise that appears after the journal name
_VOLPAGE_RE = re.compile(
    r'\b(?:vol\.?\s*)?\d+\s*[\(（]\d+[\)）]\s*[:\-–]\s*\d+[\-–]\d+',
    re.IGNORECASE,
)
_PAGES_RE = re.compile(r':\s*\d+[-–]\d+\.?$')
_DOI_SUFFIX_RE = re.compile(r'\s*(?:doi:|https?://doi\.org/)[^\s]+', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class ParsedCitation:
    raw:     str
    title:   Optional[str]   = None
    year:    Optional[int]   = None
    doi:     Optional[str]   = None
    authors: Optional[str]   = None
    journal: Optional[str]   = None
    source:  str             = "heuristic"   # "heuristic" | "grobid" | "quoted"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(s: str) -> str:
    """Strip leading/trailing punctuation and whitespace."""
    return re.sub(r'^[\s.,;:]+|[\s.,;:]+$', '', s)


def _extract_doi(raw: str) -> Optional[str]:
    m = _DOI_RE.search(raw)
    if m:
        doi = m.group(1).rstrip('.,;)')
        return doi.lower()
    return None


def _extract_year(raw: str) -> Optional[int]:
    """Return the most likely publication year."""
    hits = _YEAR_RE.findall(raw)
    if not hits:
        return None
    # Prefer years in parentheses (APA style)
    paren_years = re.findall(r'\((1[89]\d{2}|20[012]\d)\)', raw)
    if paren_years:
        return int(paren_years[-1])
    return int(hits[-1])


def _try_quoted_title(raw: str) -> Optional[str]:
    """If the citation has an explicitly quoted title, extract it."""
    m = re.search(r'["“](.+?)["”]', raw)
    if m:
        t = _clean(m.group(1))
        if len(t.split()) >= 3:
            return t
    return None


def _try_grobid(raw: str) -> Optional[ParsedCitation]:
    """Call GROBID processCitation endpoint; return ParsedCitation or None."""
    try:
        import requests
        resp = requests.post(
            GROBID_URL,
            data={"citations": raw, "includeRawCitations": "0"},
            timeout=8,
        )
        if resp.status_code != 200:
            return None
        return _parse_grobid_tei(raw, resp.text)
    except Exception:
        return None


def _parse_grobid_tei(raw: str, tei_xml: str) -> Optional[ParsedCitation]:
    """Extract structured fields from GROBID's TEI XML response."""
    try:
        import xml.etree.ElementTree as ET
        ns = {"tei": "http://www.tei-c.org/ns/1.0"}
        root = ET.fromstring(tei_xml)

        def text(xpath):
            el = root.find(xpath, ns)
            return el.text.strip() if el is not None and el.text else None

        title = text(".//tei:title[@level='a']") or text(".//tei:title")
        year_raw = text(".//tei:date[@type='published']/@when") or \
                   root.findtext(".//tei:date[@type='published']", namespaces=ns)
        year = None
        if year_raw:
            m = _YEAR_RE.search(year_raw)
            if m:
                year = int(m.group(1))
        if not year:
            year = _extract_year(raw)

        doi = text(".//tei:idno[@type='DOI']")
        if doi:
            doi = doi.lower()
        else:
            doi = _extract_doi(raw)

        # Authors
        authors_els = root.findall(".//tei:author/tei:persName", ns)
        author_names = []
        for a in authors_els[:3]:
            surname = a.findtext("tei:surname", namespaces=ns) or ""
            forename = a.findtext("tei:forename[@type='first']", namespaces=ns) or ""
            if surname:
                author_names.append(f"{surname} {forename[0]}." if forename else surname)
        authors = ", ".join(author_names) or None

        journal = text(".//tei:title[@level='j']") or text(".//tei:publisher")

        return ParsedCitation(
            raw=raw, title=title, year=year, doi=doi,
            authors=authors, journal=journal, source="grobid",
        )
    except Exception:
        return None


def _heuristic_title(raw: str, year: Optional[int], doi: Optional[str]) -> str:
    """
    Extract a title from a raw citation string using heuristics.

    Steps:
    1. Remove DOI suffixes.
    2. Remove vol/page noise.
    3. Split on period-space — each chunk is a candidate sentence.
    4. Skip chunks that look like author blocks or journal+volume lines.
    5. Return the first remaining chunk that is 4+ words long.
    """
    text = raw

    # Remove DOI
    text = _DOI_SUFFIX_RE.sub("", text)
    if doi:
        text = text.replace(doi, "")

    # Remove year in parens
    if year:
        text = re.sub(rf'\({year}\)', '', text)
        text = re.sub(rf'\b{year}\b', '', text, count=1)

    # Remove volume/pages
    text = _VOLPAGE_RE.sub("", text)
    text = _PAGES_RE.sub("", text)

    # Split on ". " or "." at sentence boundaries
    parts = re.split(r'\.\s+', text)

    # Heuristic scoring: prefer parts with mostly lowercase words (title-like)
    def _score(chunk: str) -> float:
        words = chunk.split()
        if len(words) < 2:
            return -1.0
        # Penalise if it looks like an author block (starts with caps, short words)
        caps_ratio = sum(1 for w in words if w and w[0].isupper()) / len(words)
        # Author blocks have high caps ratio AND short average word length
        avg_len = sum(len(re.sub(r'\W', '', w)) for w in words) / len(words)
        if caps_ratio > 0.8 and avg_len < 4:
            return -1.0
        # Penalise chunks that look like "Journal Vol(Issue)"
        if re.search(r'\d+\s*[\(（]\d+[\)）]', chunk):
            return 0.5
        # Prefer longer chunks with mixed case
        return len(words) + (1 - caps_ratio) * 3

    best = max(parts, key=_score, default=text)
    return _clean(best) or _clean(text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_citation(raw: str) -> ParsedCitation:
    """
    Parse a free-text citation string into a ParsedCitation.

    Try in order:
    1. Quoted title (fast, reliable when present).
    2. GROBID API (best quality, requires GROBID server).
    3. Heuristic extraction (always available).
    """
    raw = raw.strip()
    doi  = _extract_doi(raw)
    year = _extract_year(raw)

    # 1. Quoted title
    quoted = _try_quoted_title(raw)
    if quoted:
        return ParsedCitation(raw=raw, title=quoted, year=year, doi=doi, source="quoted")

    # 2. GROBID
    grobid_result = _try_grobid(raw)
    if grobid_result and grobid_result.title and len(grobid_result.title.split()) >= 3:
        return grobid_result

    # 3. Heuristic
    title = _heuristic_title(raw, year, doi)
    return ParsedCitation(raw=raw, title=title, year=year, doi=doi, source="heuristic")


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        'Petrilli CM, Jones SA, Yang J, et al. Factors associated with hospital admission '
        'and critical illness among 5279 people with coronavirus disease 2019 in New York City: '
        'prospective cohort study. BMJ. 2020;369:m1966. doi:10.1136/bmj.m1966',

        'Vaswani A, Shazeer N, Parmar N, et al. "Attention is all you need." '
        'Advances in Neural Information Processing Systems. 2017;30.',

        'Zhu N, Zhang D, Wang W, et al. A Novel Coronavirus from Patients with Pneumonia '
        'in China, 2019. N Engl J Med. 2020;382(8):727-733.',

        'LeCun Y, Bengio Y, Hinton G. Deep learning. Nature. 2015;521(7553):436-444.',

        'WHO. COVID-19 Weekly Epidemiological Update. Geneva: World Health Organization; 2021.',
    ]

    for raw in tests:
        p = parse_citation(raw)
        print(f"Input:   {raw[:80]}...")
        print(f"  title:  {p.title}")
        print(f"  year:   {p.year}   doi: {p.doi}   source: {p.source}")
        print()
