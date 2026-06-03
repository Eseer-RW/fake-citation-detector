import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class CitationStyle(str, Enum):
    APA       = "apa"
    MLA       = "mla"
    CHICAGO   = "chicago"
    VANCOUVER = "vancouver"
    IEEE      = "ieee"
    NLM       = "nlm"
    UNKNOWN   = "unknown"


@dataclass
class ParsedCitation:
    raw: str
    style: CitationStyle          # best guess — informational only, does not affect parsing
    doi: Optional[str] = None
    authors: list[str] = field(default_factory=list)
    title: Optional[str] = None
    year: Optional[int] = None
    journal: Optional[str] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None


# ── shared regex patterns ─────────────────────────────────────────────────

_DOI_RE      = re.compile(r'10\.\d{4,9}/[^\s,;>\])"\']+', re.IGNORECASE)
_YEAR_RE     = re.compile(r'\b((?:19|20)\d{2})\b')
_PAGES_RE    = re.compile(r'(?:pp?\.\s*)?(\d+)\s*[–—-]\s*(\d+)')
_VOL_RE      = re.compile(r'\bvol(?:ume)?\.?\s*(\d+)', re.IGNORECASE)
_ISSUE_RE    = re.compile(r'\bno\.?\s*(\d+)', re.IGNORECASE)
_QUOTED_RE   = re.compile(r'[""“](.+?)[""”]')
_BRACKET_RE  = re.compile(r'\s*\[[^\]]+\]')        # [DOI], [PubMed], etc.
_LIST_NUM_RE = re.compile(r'^\s*(?:\[\d+\]|\d+[.):])[ \t]+')  # 1. or [1] prefix


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


# ── style guesser (informational only — does not affect parsing) ──────────

def _detect_style(citation: str) -> CitationStyle:
    if re.match(r'^[A-Z]\.\s+[A-Z]?\.?\s*\w+', citation):
        return CitationStyle.IEEE
    if re.match(r'^\d+\.\s+\w', citation):
        return CitationStyle.VANCOUVER
    if re.search(r'\((?:19|20)\d{2}\)\.', citation):
        return CitationStyle.APA
    if re.search(r'\((?:19|20)\d{2}\)\s+[A-Z0-9]', citation):
        return CitationStyle.NLM
    if re.search(r'no\.\s*\d+\s*\((?:19|20)\d{2}\)\s*:', citation, re.IGNORECASE):
        return CitationStyle.CHICAGO
    if re.search(r'vol\.\s*\d+', citation, re.IGNORECASE) and _QUOTED_RE.search(citation):
        return CitationStyle.MLA
    if re.search(r'(?:19|20)\d{2};\d+\(\d+\):', citation):
        return CitationStyle.VANCOUVER
    return CitationStyle.UNKNOWN


# ── universal author splitter ─────────────────────────────────────────────

def _split_authors(text: str) -> list[str]:
    """Split an author string into individual names.

    Handles:
      NLM/Vancouver  "Smith JA, Jones BC, Brown CD"
      APA            "Smith, J. A., Jones, B. C., & Brown, C. D."
      MLA/IEEE/Harvard  "Smith, John and Bob Jones"
    """
    text = text.strip().rstrip('.,')
    if not text:
        return []

    # Split on "and" / "&" when present (use \band\b to avoid matching "sand" etc.)
    if re.search(r'\band\b|(?<!\w)&(?!\w)', text):
        parts = re.split(r',?\s+and\s+|,?\s*&\s*', text)
        return [p.strip().rstrip(',') for p in parts if p.strip()]

    # Comma-separated — detect APA "Surname, Initials, Surname, Initials"
    # by checking whether every other part (starting at index 1) looks like "J." or "J. A."
    parts = [p.strip() for p in text.split(',') if p.strip()]
    if len(parts) >= 2:
        odd_parts = parts[1::2]
        if odd_parts and all(re.match(r'^[A-Z]\.', p) for p in odd_parts):
            # APA-style: merge pairs → "Surname, Initials"
            authors = []
            for i in range(0, len(parts) - 1, 2):
                authors.append(f"{parts[i]}, {parts[i+1]}")
            if len(parts) % 2 == 1:
                authors.append(parts[-1])
            return authors

    # Default: each comma-separated item is one author (NLM/Vancouver)
    return parts


# ── universal parser ──────────────────────────────────────────────────────

def _parse_universal(citation: str) -> dict:
    """Extract citation fields without assuming any particular style.

    Strategies tried in order of reliability:
      1. Quoted title      → covers IEEE / MLA / Chicago
      2. (YEAR) Title      → covers NLM  (no period after year)
      3. (YEAR). Title     → covers APA / Harvard-with-period
      4. Authors. Title.   → covers Vancouver / Harvard
    """
    result = {}

    # Pre-process: strip [DOI], [PubMed] tags and leading list numbers
    clean = _BRACKET_RE.sub('', citation).strip()
    clean = _LIST_NUM_RE.sub('', clean).strip()

    # ── Strategy 1: quoted title (IEEE / MLA / Chicago) ──────────────────
    qm = _QUOTED_RE.search(clean)
    if qm:
        result['title'] = qm.group(1).strip().rstrip('.,')
        # Authors: everything before the opening quote
        before = clean[:qm.start()].strip().rstrip('.,')
        result['authors'] = _split_authors(before)
        # Journal: text right after the closing quote, before vol./year
        after_q = clean[qm.end():].lstrip('.,').strip()
        jm = re.match(r'^([A-Za-z][^,\d]+?)(?=,\s*vol\.|\s+vol\.|\s+\d|\s*$)',
                      after_q, re.IGNORECASE)
        if jm:
            result['journal'] = jm.group(1).strip().rstrip('.,')
        return result

    # ── Strategy 2: (YEAR) without period — NLM-like ─────────────────────
    # Match "(YEAR) " where the character before the paren is NOT a period
    nlm_m = re.search(r'(?<![.])\((?:19|20)\d{2}\)[ \t]+', clean)
    if nlm_m:
        result['authors'] = _split_authors(clean[:nlm_m.start()])
        after = clean[nlm_m.end():]

        # Book chapter: "Title. In: ..."
        if re.search(r'\bIn:', after):
            bm = re.search(r'^(.+?)\.\s+In:', after)
            if bm:
                result['title'] = bm.group(1).strip()
            return result

        # vol:pages pattern e.g. "9:e106903", "16(1):456", "32:1713-1723"
        vol_m = re.search(r'\s(\d+(?:\(\d+\))?):[\w–—-]+', after)
        if vol_m:
            before_vol = after[:vol_m.start()].strip()
            vi_m = re.match(r'(\d+)(?:\((\d+)\))?', vol_m.group(1))
            if vi_m:
                result['volume'] = vi_m.group(1)
                if vi_m.group(2):
                    result['issue'] = vi_m.group(2)
            result['pages'] = vol_m.group(0).split(':')[-1].strip()
            last_dot = before_vol.rfind('. ')
            if last_dot > 0:
                result['title']   = before_vol[:last_dot].strip()
                result['journal'] = before_vol[last_dot + 2:].strip()
            else:
                result['title'] = before_vol.strip()
        else:
            # No vol:pages — first sentence is the title
            sm = re.match(r'^(.+?)\.(?:\s|$)', after)
            result['title'] = sm.group(1).strip() if sm else after.strip().rstrip('.')
        return result

    # ── Strategy 3: (YEAR). with period — APA-like ───────────────────────
    apa_m = re.search(r'\((?:19|20)\d{2}\)\.\s+', clean)
    if apa_m:
        result['authors'] = _split_authors(clean[:apa_m.start()])
        after = clean[apa_m.end():]

        # Title: first sentence. Use lookahead so the capital letter isn't consumed.
        tm = re.search(r'^(.+?)\.(?=\s+[A-Z]|$)', after)
        if tm:
            result['title'] = tm.group(1).strip()
            rest = after[tm.end():].lstrip('. ').strip()
            # Journal: word(s) before first comma+digit
            jm = re.match(r'^([^,\d]+),\s*(\d+)(?:\((\d+)\))?', rest)
            if jm:
                result['journal'] = jm.group(1).strip()
                result['volume']  = jm.group(2)
                result['issue']   = jm.group(3)
        else:
            result['title'] = after.strip().rstrip('.')
        return result

    # ── Strategy 4: period-separated sentences — Vancouver / Harvard ──────
    sentences = re.split(r'\.\s+', clean)
    if len(sentences) >= 2:
        result['authors'] = _split_authors(sentences[0])
        result['title']   = sentences[1].strip()
        if len(sentences) >= 3:
            jpart = sentences[2].strip()
            # Journal is the text before a year, semicolon, or end
            jm = re.match(r'^([A-Za-z][^;0-9]+?)(?:\s+(?:19|20)\d{2}|;|$)', jpart)
            result['journal'] = jm.group(1).strip() if jm else jpart.strip()
        # Vancouver-style: "2020;12(3):45-67"
        vm = re.search(r'(?:19|20)\d{2};(\d+)\((\d+)\):([\d–—-]+)', clean)
        if vm:
            result['volume'] = vm.group(1)
            result['issue']  = vm.group(2)
            result['pages']  = vm.group(3)
        return result

    return result


# ── public API ────────────────────────────────────────────────────────────

def parse_citation(citation: str) -> ParsedCitation:
    citation = citation.strip()
    fields   = _parse_universal(citation)

    return ParsedCitation(
        raw     = citation,
        style   = _detect_style(citation),    # informational only
        doi     = _doi(citation),
        authors = fields.get('authors', []),
        title   = fields.get('title'),
        year    = _year(citation),
        journal = fields.get('journal'),
        volume  = fields.get('volume') or _vol(citation),
        issue   = fields.get('issue')  or _issue(citation),
        pages   = fields.get('pages')  or _pages(citation),
    )


# ── block splitting ───────────────────────────────────────────────────────

_NUM_MARKER_RE = re.compile(r'(?m)^[ \t]*(?:\[\d+\]|\d+[.):])[ \t]+')


def split_citations(text: str) -> list[str]:
    """Split a block of text into individual citation strings.

    Handles three formats automatically:
      1. Numbered   — [1], 1., 1) at line start
      2. Blank-line — paragraphs separated by empty lines
      3. Per-line   — each non-empty line is one citation
    """
    text = text.strip()
    if not text:
        return []

    num_matches = list(_NUM_MARKER_RE.finditer(text))
    if num_matches:
        chunks = []
        for i, m in enumerate(num_matches):
            start = m.end()
            end   = num_matches[i + 1].start() if i + 1 < len(num_matches) else len(text)
            chunk = re.sub(r' {2,}', ' ', text[start:end].strip().replace('\n', ' '))
            if chunk:
                chunks.append(chunk)
        return chunks

    paragraphs = re.split(r'\n[ \t]*\n+', text)
    if len(paragraphs) > 1:
        return [re.sub(r' {2,}', ' ', p.strip().replace('\n', ' ')) for p in paragraphs if p.strip()]

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return lines if lines else [text]


def parse_all_citations(text: str) -> list[ParsedCitation]:
    """Parse every citation in a block of text.

    Splits automatically (numbered list, blank-line paragraphs, or one-per-line),
    then parses each citation with the universal parser.
    """
    return [parse_citation(c) for c in split_citations(text)]


class CitationParser:
    def parse(self, citation: str) -> ParsedCitation:
        return parse_citation(citation)

    def parse_many(self, citations: list[str]) -> list[ParsedCitation]:
        return [parse_citation(c) for c in citations]

    def parse_block(self, text: str) -> list[ParsedCitation]:
        return parse_all_citations(text)


# ── self-test ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    examples = [
        ("NLM",      "Smith JA, Jones BC (2020) Effects of X on Y. J Sci 12:45-67"),
        ("APA",      "Smith, J. A., & Jones, B. C. (2020). Effects of X on Y. Journal of Science, 12(3), 45-67."),
        ("MLA",      'Smith, John A., and Bob Jones. "Effects of X on Y." Journal of Science, vol. 12, no. 3, 2020, pp. 45-67.'),
        ("Chicago",  'Smith, John A., and Bob Jones. "Effects of X on Y." Journal of Science 12, no. 3 (2020): 45-67.'),
        ("Vancouver","1. Smith JA, Jones BC. Effects of X on Y. J Sci. 2020;12(3):45-67."),
        ("IEEE",     'J. A. Smith and B. Jones, "Effects of X on Y," Journal of Science, vol. 12, no. 3, pp. 45-67, 2020.'),
        ("Harvard",  "Smith, J. and Jones, B. (2020) Effects of X on Y. Journal of Science, 12(3), pp. 45-67."),
    ]

    for label, ex in examples:
        c = parse_citation(ex)
        print(f"\n── {label} ──")
        print(f"  Authors: {c.authors}")
        print(f"  Title:   {c.title}")
        print(f"  Journal: {c.journal}")
        print(f"  Year: {c.year}  Vol: {c.volume}  Issue: {c.issue}  Pages: {c.pages}")
