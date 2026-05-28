import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class CitationStyle(str, Enum):
    APA = "apa"
    MLA = "mla"
    CHICAGO = "chicago"
    VANCOUVER = "vancouver"
    IEEE = "ieee"
    UNKNOWN = "unknown"


@dataclass
class ParsedCitation:
    raw: str
    style: CitationStyle
    doi: Optional[str] = None
    authors: list[str] = field(default_factory=list)
    title: Optional[str] = None
    year: Optional[int] = None
    journal: Optional[str] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None


#shared patterns

_DOI_RE = re.compile(r'10\.\d{4,9}/[^\s,;>\])"\']+', re.IGNORECASE)
_YEAR_RE = re.compile(r'\b((?:19|20)\d{2})\b')
_PAGES_RE = re.compile(r'(?:pp?\.\s*)?(\d+)\s*[–—-]\s*(\d+)')
_VOL_RE = re.compile(r'\bvol(?:ume)?\.?\s*(\d+)', re.IGNORECASE)
_ISSUE_RE = re.compile(r'\bno\.?\s*(\d+)', re.IGNORECASE)

# matches straight and curly quotes
_QUOTED_TITLE_RE = re.compile(r'["\u201c](.+?)["\u201d]')


def _doi(text: str) -> Optional[str]:
    m = _DOI_RE.search(text)
    return m.group(0).rstrip('.,;') if m else None


def _year(text: str) -> Optional[int]:
    m = _YEAR_RE.search(text)
    return int(m.group(1)) if m else None


def _pages(text: str) -> Optional[str]:
    m = _PAGES_RE.search(text)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def _vol(text: str) -> Optional[str]:
    m = _VOL_RE.search(text)
    return m.group(1) if m else None


def _issue(text: str) -> Optional[str]:
    m = _ISSUE_RE.search(text)
    return m.group(1) if m else None


def _quoted_title(text: str) -> Optional[str]:
    m = _QUOTED_TITLE_RE.search(text)
    return m.group(1).strip() if m else None


# style detection

def _detect_style(citation: str) -> CitationStyle:
    # IEEE: author list starts with initials  "J. A. Smith and ..."
    if re.match(r'^[A-Z]\.\s+[A-Z]?\.?\s*\w+', citation):
        return CitationStyle.IEEE

    # Vancouver numbered: "1. Smith JA, ..."
    if re.match(r'^\d+\.\s+\w', citation):
        return CitationStyle.VANCOUVER

    # APA: year in parentheses immediately after authors "(2020)."
    if re.search(r'\((?:19|20)\d{2}\)\.', citation):
        return CitationStyle.APA

    # Chicago: "Journal Vol, no. X (Year): pages"
    if re.search(r'no\.\s*\d+\s*\((?:19|20)\d{2}\)\s*:', citation, re.IGNORECASE):
        return CitationStyle.CHICAGO

    # MLA: "vol. X, no. Y" with a quoted title
    if re.search(r'vol\.\s*\d+', citation, re.IGNORECASE) and _QUOTED_TITLE_RE.search(citation):
        return CitationStyle.MLA

    # Vancouver without number: "Year;vol(issue):pages"
    if re.search(r'(?:19|20)\d{2};\d+\(\d+\):', citation):
        return CitationStyle.VANCOUVER

    return CitationStyle.UNKNOWN


# style specific paper

def _parse_apa(citation: str) -> dict:
    result = {}

    # Authors are everything before "(YEAR)."
    m = re.match(r'^(.*?)\s*\((?:19|20)\d{2}\)', citation, re.DOTALL)
    if m:
        raw_authors = m.group(1).strip().rstrip('.')
        # Split on ", & " or " & " — handles "Smith, J., Jones, B., & Brown, C."
        parts = re.split(r',?\s*&\s*', raw_authors)
        # Each part may contain multiple "Last, F." authors separated by ", "
        authors = []
        for part in parts:
            # Split on ", " only when followed by another "Last, F." pattern
            sub = re.split(r',\s+(?=[A-Z][a-z])', part)
            authors.extend(s.strip().rstrip(',') for s in sub if s.strip())
        result['authors'] = authors

    # Title: first sentence after "(YEAR). "
    m = re.search(r'\((?:19|20)\d{2}\)\.\s+(.+?)\.(?:\s+[A-Z]|$)', citation)
    if m:
        result['title'] = m.group(1).strip()

    # Journal, volume(issue): everything after the title sentence
    m = re.search(r'\((?:19|20)\d{2}\)\.\s+.+?\.\s+(.+)', citation)
    if m:
        rest = m.group(1)
        jm = re.match(r'^([^,\d]+),\s*(\d+)(?:\((\d+)\))?', rest)
        if jm:
            result['journal'] = jm.group(1).strip()
            result['volume'] = jm.group(2)
            result['issue'] = jm.group(3)

    return result


def _parse_mla(citation: str) -> dict:
    result = {}

    # Authors: everything before the opening quote
    m = re.match(r'^(.*?)\s*["\u201c]', citation, re.DOTALL)
    if m:
        raw = m.group(1).strip().rstrip('.')
        result['authors'] = [
            a.strip() for a in re.split(r',\s+and\s+|\s+and\s+', raw) if a.strip()
        ]

    result['title'] = _quoted_title(citation)

    # Journal: after closing quote and punctuation, before ", vol."
    m = re.search(
        r'["\u201d]\s*[.,]?\s+([^,]+?),\s+vol\.', citation, re.IGNORECASE
    )
    if m:
        result['journal'] = m.group(1).strip()

    return result


def _parse_chicago(citation: str) -> dict:
    result = {}

    m = re.match(r'^(.*?)\s*["\u201c]', citation, re.DOTALL)
    if m:
        raw = m.group(1).strip().rstrip('.')
        result['authors'] = [
            a.strip() for a in re.split(r',\s+and\s+|\s+and\s+', raw) if a.strip()
        ]

    result['title'] = _quoted_title(citation)

    # Journal: after closing quote, before "Vol, no. X (Year)"
    m = re.search(
        r'["\u201d]\s*[.,]?\s+([A-Za-z][^"]+?)\s+\d+,\s+no\.', citation, re.IGNORECASE
    )
    if m:
        result['journal'] = m.group(1).strip().rstrip('.')

    # Volume right before "no."
    m = re.search(r'(\d+),\s+no\.', citation, re.IGNORECASE)
    if m:
        result['volume'] = m.group(1)

    return result


def _parse_vancouver(citation: str) -> dict:
    result = {}

    # Strip leading list number
    text = re.sub(r'^\d+\.\s*', '', citation)

    # Authors end at the first ". " followed by a capital letter (start of title)
    m = re.match(r'^(.+?)\.\s+([A-Z].+)', text, re.DOTALL)
    if m:
        result['authors'] = [a.strip() for a in m.group(1).split(',') if a.strip()]
        rest = m.group(2)

        # Title ends at ". Journal. Year" — grab first sentence
        tm = re.match(r'^(.+?)\.\s+([A-Za-z][A-Za-z\s]+)\.\s+(?:19|20)\d{2}', rest)
        if tm:
            result['title'] = tm.group(1).strip()
            result['journal'] = tm.group(2).strip()

    # Year;vol(issue):pages
    m = re.search(r'(?:19|20)\d{2};(\d+)\((\d+)\):(\d+[-–]\d+)', citation)
    if m:
        result['volume'] = m.group(1)
        result['issue'] = m.group(2)
        result['pages'] = m.group(3)

    return result


def _parse_ieee(citation: str) -> dict:
    result = {}

    # Authors: everything before the opening quote, strip trailing comma
    m = re.match(r'^(.*?),\s*["\u201c]', citation, re.DOTALL)
    if m:
        result['authors'] = [
            a.strip() for a in re.split(r'\s+and\s+', m.group(1)) if a.strip()
        ]

    result['title'] = _quoted_title(citation)

    # Journal: after closing quote and comma, before ", vol."
    m = re.search(
        r'["\u201d],\s*([^,]+),\s*vol\.', citation, re.IGNORECASE
    )
    if m:
        result['journal'] = m.group(1).strip()

    return result


def _parse_unknown(citation: str) -> dict:
    result = {}
    result['title'] = _quoted_title(citation)
    return result


_PARSERS = {
    CitationStyle.APA: _parse_apa,
    CitationStyle.MLA: _parse_mla,
    CitationStyle.CHICAGO: _parse_chicago,
    CitationStyle.VANCOUVER: _parse_vancouver,
    CitationStyle.IEEE: _parse_ieee,
    CitationStyle.UNKNOWN: _parse_unknown,
}


# =====================================================
# PUBLIC API
# =====================================================

def parse_citation(citation: str) -> ParsedCitation:
    citation = citation.strip()
    style = _detect_style(citation)
    fields = _PARSERS[style](citation)

    return ParsedCitation(
        raw=citation,
        style=style,
        doi=_doi(citation),
        authors=fields.get("authors", []),
        title=fields.get("title"),
        year=_year(citation),
        journal=fields.get("journal"),
        volume=fields.get("volume") or _vol(citation),
        issue=fields.get("issue") or _issue(citation),
        pages=fields.get("pages") or _pages(citation),
    )


class CitationParser:
    def parse(self, citation: str) -> ParsedCitation:
        return parse_citation(citation)

    def parse_many(self, citations: list[str]) -> list[ParsedCitation]:
        return [parse_citation(c) for c in citations]


# test

if __name__ == "__main__":
    examples = [
        # APA
        'Smith, J. A., & Jones, B. C. (2020). Effects of X on Y. Journal of Science, 12(3), 45-67. https://doi.org/10.1234/abc123',
        # MLA
        'Smith, John A., and Bob Jones. "Effects of X on Y." Journal of Science, vol. 12, no. 3, 2020, pp. 45-67.',
        # Chicago
        'Smith, John A., and Bob Jones. "Effects of X on Y." Journal of Science 12, no. 3 (2020): 45-67.',
        # Vancouver
        '1. Smith JA, Jones BC. Effects of X on Y. J Sci. 2020;12(3):45-67.',
        # IEEE
        'J. A. Smith and B. Jones, "Effects of X on Y," Journal of Science, vol. 12, no. 3, pp. 45-67, 2020.',
    ]

    parser = CitationParser()
    for ex in examples:
        c = parser.parse(ex)
        print(f"\nStyle:   {c.style}")
        print(f"Authors: {c.authors}")
        print(f"Title:   {c.title}")
        print(f"Journal: {c.journal}")
        print(f"Year:    {c.year}  Vol: {c.volume}  Issue: {c.issue}  Pages: {c.pages}")
        print(f"DOI:     {c.doi}")