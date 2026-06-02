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
    NLM       = "nlm"
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
_QUOTED_TITLE_RE = re.compile(r'["“](.+?)["”]')


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

    # NLM: year in parens followed by space then capital letter or digit (no period after year)
    if re.search(r'\((?:19|20)\d{2}\)\s+[A-Z0-9]', citation):
        return CitationStyle.NLM

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


# style specific parsers

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
    m = re.match(r'^(.*?)\s*["“]', citation, re.DOTALL)
    if m:
        raw = m.group(1).strip().rstrip('.')
        result['authors'] = [
            a.strip() for a in re.split(r',\s+and\s+|\s+and\s+', raw) if a.strip()
        ]

    result['title'] = _quoted_title(citation)

    # Journal: after closing quote and punctuation, before ", vol."
    m = re.search(
        r'["”]\s*[.,]?\s+([^,]+?),\s+vol\.', citation, re.IGNORECASE
    )
    if m:
        result['journal'] = m.group(1).strip()

    return result


def _parse_chicago(citation: str) -> dict:
    result = {}

    m = re.match(r'^(.*?)\s*["“]', citation, re.DOTALL)
    if m:
        raw = m.group(1).strip().rstrip('.')
        result['authors'] = [
            a.strip() for a in re.split(r',\s+and\s+|\s+and\s+', raw) if a.strip()
        ]

    result['title'] = _quoted_title(citation)

    # Journal: after closing quote, before "Vol, no. X (Year)"
    m = re.search(
        r'["”]\s*[.,]?\s+([A-Za-z][^"]+?)\s+\d+,\s+no\.', citation, re.IGNORECASE
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
    m = re.match(r'^(.*?),\s*["“]', citation, re.DOTALL)
    if m:
        result['authors'] = [
            a.strip() for a in re.split(r'\s+and\s+', m.group(1)) if a.strip()
        ]

    result['title'] = _quoted_title(citation)

    # Journal: after closing quote and comma, before ", vol."
    m = re.search(
        r'["”],\s*([^,]+),\s*vol\.', citation, re.IGNORECASE
    )
    if m:
        result['journal'] = m.group(1).strip()

    return result

_BRACKET_TAG_RE = re.compile(r'\s*\[[^\]]+\]')

def _parse_nlm(citation: str) -> dict:
    result = {}

    # Strip [DOI], [PubMed], [Google Scholar], [PMC free article] etc.
    clean = _BRACKET_TAG_RE.sub('', citation).strip()

    # Authors: everything before (YEAR)
    m = re.match(r'^(.*?)\s*\((?:19|20)\d{2}\)', clean)
    if m:
        result['authors'] = [a.strip() for a in m.group(1).split(',') if a.strip()]

    # Find where the year ends so we can get the text after it
    year_m = re.search(r'\((?:19|20)\d{2}\)\s+', clean)
    if not year_m:
        return result

    after_year = clean[year_m.end():]

    # Book chapter: has "In:" — title is everything before ". In:"
    if re.search(r'\bIn:', after_year):
        bm = re.search(r'^(.+?)\.\s+In:', after_year)
        if bm:
            result['title'] = bm.group(1).strip()
        return result

    # Find volume:page pattern — e.g. "2:100081", "16(1):456", "32:1713-1723"
    vol_m = re.search(r'\s(\d+(?:\(\d+\))?):[\w–—-]+', after_year)
    if vol_m:
        before_vol = after_year[:vol_m.start()].strip()

        # Parse volume and issue from "16(1)" or "32"
        vi_m = re.match(r'(\d+)(?:\((\d+)\))?', vol_m.group(1))
        if vi_m:
            result['volume'] = vi_m.group(1)
            if vi_m.group(2):
                result['issue'] = vi_m.group(2)

        # Pages
        result['pages'] = vol_m.group(0).split(':')[-1].strip()

        # Split "Title. Journal Name" at last ". "
        last_dot = before_vol.rfind('. ')
        if last_dot > 0:
            result['title']   = before_vol[:last_dot].strip()
            result['journal'] = before_vol[last_dot + 2:].strip()
        else:
            result['title'] = before_vol.strip()
    else:
        # No volume:page — just take the whole thing as the title
        result['title'] = after_year.strip().rstrip('.')

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
    CitationStyle.NLM:       _parse_nlm
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


# =====================================================
# BLOCK SPLITTING
# =====================================================

# Matches numbered citation list markers at the start of a line: [1], 1., 1), 1:
_NUM_MARKER_RE = re.compile(r'(?m)^[ \t]*(?:\[\d+\]|\d+[.):])[ \t]+')


def split_citations(text: str) -> list[str]:
    """Split a block of text into individual citation strings.

    Handles three common formats automatically:

    1. Numbered references  — lines starting with [1], 1., 1) etc.
    2. Blank-line separated — paragraphs divided by one or more blank lines.
    3. One citation per line — each non-empty line is one citation.

    Multi-line citations (a single citation wrapped across several lines) are
    joined with spaces so the downstream parser sees a clean, flat string.
    """
    text = text.strip()
    if not text:
        return []

    # --- Strategy 1: numbered markers ([1], 1., 1), 1:) ---
    num_matches = list(_NUM_MARKER_RE.finditer(text))
    if num_matches:
        chunks = []
        for i, m in enumerate(num_matches):
            start = m.end()  # content starts after the "1. " prefix
            end   = num_matches[i + 1].start() if i + 1 < len(num_matches) else len(text)
            chunk = text[start:end].strip().replace('\n', ' ')
            if chunk:
                chunks.append(chunk)
        return chunks

    # --- Strategy 2: blank-line separated paragraphs ---
    paragraphs = re.split(r'\n[ \t]*\n+', text)
    if len(paragraphs) > 1:
        return [p.strip().replace('\n', ' ') for p in paragraphs if p.strip()]

    # --- Strategy 3: one citation per line ---
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return lines if lines else [text]


def parse_all_citations(text: str) -> list[ParsedCitation]:
    """Parse every citation in a block of text.

    Automatically splits the block into individual citations (numbered list,
    blank-line paragraphs, or one-per-line), then parses each one.

    Returns a list of ParsedCitation objects in the same order they appear.

    Example::

        block = open("references.txt").read()
        results = parse_all_citations(block)
        for r in results:
            print(r.title, r.year, r.doi)
    """
    return [parse_citation(c) for c in split_citations(text)]


class CitationParser:
    def parse(self, citation: str) -> ParsedCitation:
        """Parse a single citation string."""
        return parse_citation(citation)

    def parse_many(self, citations: list[str]) -> list[ParsedCitation]:
        """Parse a list of already-split citation strings."""
        return [parse_citation(c) for c in citations]

    def parse_block(self, text: str) -> list[ParsedCitation]:
        """Parse all citations from a raw block of text (e.g. a reference section)."""
        return parse_all_citations(text)


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
