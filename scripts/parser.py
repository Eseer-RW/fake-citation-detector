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

_DOI_RE      = re.compile(
    r'10\.\d{4,9}/[^\s,;>\]()\'\"]*(?:\([^)\s]*\)[^\s,;>\]()\'"]*)*',
    re.IGNORECASE,
)
# Note: both char classes exclude ( and ) so parenthesised groups are only consumed
# by the (?:\([^)\s]*\)...) alternation — this lets Elsevier DOIs like
# 10.1016/S0140-6736(13)60103-8 match in full while a trailing ) from
# "(see doi:10.1016/xxx)" is excluded.
_YEAR_RE     = re.compile(r'\b((?:19|20)\d{2})[a-z]?\b')
# Publication-year contexts, tried before the generic first-year fallback. A
# year embedded in a title (e.g. "...deaths to 2030:") matches none of these, so
# it no longer wins over the real publication year that follows the journal.
_PUB_YEAR_RES = (
    re.compile(r'\b((?:19|20)\d{2})[a-z]?\s*;'),         # Vancouver  "2014;74:..."
    re.compile(r'\(((?:19|20)\d{2})[a-z]?\)'),           # APA/NLM/Nature/Chicago  "(2014)"
    re.compile(r'(?<![(\d])\.\s+((?:19|20)\d{2})\.(?=\s)'),  # eLife bare  ". 2014. "
)
_PAGES_RE    = re.compile(r'(?:pp?\.\s*)?(\d+)\s*[–—-]\s*(\d+)')
_VOL_RE      = re.compile(r'\bvol(?:ume)?\.?\s*(\d+)', re.IGNORECASE)
# "Journal, Vol, Pages." — comma-delimited alternative to the "Journal Vol:Pages"
# colon form (Strategy 2 below), used by some journals' own house reference
# style (e.g. Bioinformatics/Database (Oxford): "BMC Bioinformatics, 13, 161.",
# "Database (Oxford), 2014, bau074." -- the latter uses the year in the volume
# slot and an alphanumeric article id in the pages slot, both handled as-is
# since neither field is validated beyond its shape here).
_CVP_RE      = re.compile(
    r'\.\s+([A-Z][\w.()]*(?:\s+[\w.()]+)*),\s*'
    r'(\d+(?:\s*\(Suppl\.?\s*\d+\))?),\s*'
    r'([\w][\w–—-]*)\s*\.?\s*$'
)
_ISSUE_RE    = re.compile(r'\bno\.?\s*(\d+)', re.IGNORECASE)
_QUOTED_RE   = re.compile(r'[""“](.+?)[""”]')
_BRACKET_RE  = re.compile(r'\s*\[[^\]]+\]')        # [DOI], [PubMed], etc.
# Leading list number to strip from a single citation: "1.", "[1]", "1)".
# `[ \t]*(?=\D)` (not `[ \t]+`) so the tight PMC form "2.Von Hoff DD" is stripped
# too; the `(?=\D)` guard keeps it from eating into a leading number that is part
# of the citation body.
_LIST_NUM_RE = re.compile(r'^\s*(?:\[\d+\]|\d+[.):])[ \t]*(?=\D)')  # 1. or [1] prefix


def _doi(text: str) -> Optional[str]:
    m = _DOI_RE.search(text)
    return m.group(0).rstrip('.,;') if m else None

def _year(text: str) -> Optional[int]:
    for rx in _PUB_YEAR_RES:
        m = rx.search(text)
        if m:
            return int(m.group(1))
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

    # NLM/Vancouver: semicolon after "et al" separates a consortium or group author.
    # "Smith JA, Jones BC, et al; Latin American Network (LANCOVID-19)"
    # Split at "; " so the consortium name is a separate author entry.
    if ';' in text:
        semi_parts = [p.strip() for p in text.split(';') if p.strip()]
        if len(semi_parts) > 1:
            # Recursively parse each part
            authors: list[str] = []
            for sp in semi_parts:
                authors.extend(_split_authors(sp))
            return authors

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


# ── Elsevier / ScienceDirect HTML export ──────────────────────────────────
# Format:  Author1 ∙ Author2 ∙ Author3 [… elided]  Title  Journal. YEAR;<nbsp>VOL:PAGES
#          <badges: Full Text | Scopus (N) | PubMed | Crossref | Google Scholar>
# The author list is BULLET-delimited (U+2219) and every entry trails a run of
# link badges. The generic strategies understand neither, so they scatter author
# fragments across the title/journal fields and title verification then fails.
_SD_BADGE = (
    r'(?:Full\s+Text(?:\s*\(PDF\))?|Abstract|Cross\s*[Rr]ef'
    r'|Scopus(?:\s*\(\d[\d,]*\))?|PubMed(?:\s+Central)?|PMC(?:\s+free\s+article)?'
    r'|Google\s+Scholar|View\s+in\s+Article|Download\s+PDF|PDF|ScienceDirect)'
)
_SD_BADGE_TAIL_RE = re.compile(r'(?:\s*' + _SD_BADGE + r')+\s*[.,]?\s*$', re.IGNORECASE)
_SD_BULLET_RE   = re.compile(r'[∙·•]')             # U+2219 / U+00B7 / U+2022 author sep
# Elided middle authors: a run of 2+ dots (whitespace-preceded so it can't latch
# onto a preceding initial's period, "M.A. ..."), or the unicode ellipsis.
_SD_ELLIPSIS_RE = re.compile(r'(?<=\s)\.{2,}|…')
# Journal. YEAR;<nbsp>VOL(:ISSUE):PAGES  — the reliable structural anchor. \s
# matches the non-breaking space Elsevier puts after the semicolon.
_SD_TAIL_RE = re.compile(
    r'\.\s+((?:19|20)\d{2})[a-z]?\s*;\s*(\d+)(?:\s*\((\d+)\))?'
    r'\s*:\s*([\dA-Za-z]+(?:\s*[–—-]\s*[\dA-Za-z]+)?)'
)
# Journal name = the trailing run of Capitalised tokens (incl. abbreviations
# like "J. Clin. Oncol.") right before that tail; the title's final words are
# lower-case here, so the run stops at the title/journal boundary.
_SD_JOURNAL_RE = re.compile(r'((?:[A-Z][A-Za-z]*\.?\s+)*[A-Z][A-Za-z]*\.?)\s*$')
# "Surname, I.I." at the head of the final bullet chunk (last author glued to
# the title when there is no eliding ellipsis).
_SD_LAST_AUTHOR_RE = re.compile(
    r"^([A-ZÀ-ɏ][\w'’.\-]*,\s+(?:[A-ZÀ-ɏ]\.\s*-?\s*)+)\s+(.+)$"
)


def _is_sciencedirect(clean: str) -> bool:
    return bool(_SD_BULLET_RE.search(clean) or _SD_BADGE_TAIL_RE.search(clean))


def _parse_sciencedirect(clean: str) -> dict:
    """Parse an Elsevier/ScienceDirect reference into fields. Returns {} (so the
    caller falls through to the generic strategies) unless a title is isolated."""
    result: dict = {}
    body = _SD_BADGE_TAIL_RE.sub('', clean).strip()

    tm = _SD_TAIL_RE.search(body)
    if tm:
        result['volume'] = tm.group(2)
        if tm.group(3):
            result['issue'] = tm.group(3)
        result['pages'] = re.sub(r'\s*[–—-]\s*', '-', tm.group(4))
        before_tail = body[:tm.start()].strip()
    else:
        before_tail = body

    # Journal: trailing Capitalised run right before the ". YEAR;" tail.
    before_journal = before_tail
    if tm:
        jm = _SD_JOURNAL_RE.search(before_tail)
        if jm:
            result['journal'] = jm.group(1).strip().rstrip('.')
            before_journal = before_tail[:jm.start()].strip()

    # Authors (bullet-delimited) vs title. The final bullet chunk still holds the
    # last author glued to the title: split on the elided-authors ellipsis if
    # present, else at the "Surname, I.I." boundary.
    chunks = [c.strip() for c in _SD_BULLET_RE.split(before_journal) if c.strip()]
    authors: list[str] = []
    title = None
    if chunks:
        *lead, last = chunks
        authors.extend(a.rstrip('.,') for a in lead)
        em = _SD_ELLIPSIS_RE.search(last)
        if em:
            head = last[:em.start()].strip().rstrip('.,')
            if head:
                authors.append(head)
            title = last[em.end():].strip()
        else:
            nm = _SD_LAST_AUTHOR_RE.match(last)
            if nm:
                authors.append(nm.group(1).strip().rstrip('.,'))
                title = nm.group(2).strip()
            else:
                title = last.strip()
    if authors:
        result['authors'] = authors
    if title:
        result['title'] = title.strip().rstrip('.,')
    return result


# ── universal parser ──────────────────────────────────────────────────────

def _parse_universal(citation: str) -> dict:
    """Extract citation fields without assuming any particular style.

    Strategies tried in order of reliability:
      1. Quoted title               → covers IEEE / MLA / Chicago
      2. (YEAR) Title               → covers NLM  (no period after year)
      3. (YEAR). Title              → covers APA / Harvard-with-period
      4. Vol (YEAR) Pages           → covers Physics / APS (no title)
      5. Authors. Title.            → covers Vancouver / Harvard
    """
    result = {}

    # Pre-process: strip [DOI], [PubMed] tags and leading list numbers
    clean = _BRACKET_RE.sub('', citation).strip()
    clean = _LIST_NUM_RE.sub('', clean).strip()

    # ── Strategy 0: Elsevier / ScienceDirect (bullet authors + badge tail) ─
    if _is_sciencedirect(clean):
        sd = _parse_sciencedirect(clean)
        if sd.get('title'):
            return sd

    # ── Strategy 1: quoted title (IEEE / MLA / Chicago) ──────────────────
    qm = _QUOTED_RE.search(clean)
    if qm:
        before_q = clean[:qm.start()]
        # Guard: if the text before the opening quote already contains a bare year
        # (". 2014. ") this is an eLife/NLM citation whose title happens to include
        # a word or project name wrapped in curly/typographic quotes (e.g.
        # 'The SILVA and "All-species Living Tree Project (LTP)" taxonomic frameworks').
        # In that case the quote is NOT a title delimiter — fall through to later strats.
        if not re.search(r'(?<![(\d])\.\s+(?:19|20)\d{2}\.\s', before_q):
            result['title'] = qm.group(1).strip().rstrip('.,')
            # Authors: everything before the opening quote
            before = before_q.strip().rstrip('.,')
            result['authors'] = _split_authors(before)
            # Journal: text right after the closing quote, before vol./year
            after_q = clean[qm.end():].lstrip('.,').strip()
            jm = re.match(r'^([A-Za-z][^,\d]+?)(?=,\s*vol\.|\s+vol\.|\s+\d|\s*$)',
                          after_q, re.IGNORECASE)
            if jm:
                result['journal'] = jm.group(1).strip().rstrip('.,')
            return result

    # ── Strategy 2: (YEAR) without period — NLM-like ─────────────────────
    # Match "(YEAR) " where the character before the paren is NOT a period.
    # Also accept a trailing letter after the year: "(2016a) " (used by eLife to
    # disambiguate multiple papers by the same author in the same year).
    nlm_m = re.search(r'(?<![.])\((?:19|20)\d{2}[a-z]?\)[ \t]+', clean)
    # Physics/math pattern: year is preceded by a volume number or a closing
    # paren from an issue: "Vol (Issue) (Year)" or "Vol, (Year)".
    # In that case the (YEAR) is NOT an NLM author-date marker.
    if nlm_m and re.search(r'[\d)]\s*,?\s*$', clean[:nlm_m.start()]):
        nlm_m = None
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
            cvp_m = _CVP_RE.search(after)
            if cvp_m:
                result['title']   = after[:cvp_m.start()].strip().rstrip('.')
                result['journal'] = cvp_m.group(1).strip()
                result['volume']  = cvp_m.group(2).strip()
                result['pages']   = cvp_m.group(3).strip()
            else:
                # No vol:pages / comma form — first sentence is the title
                sm = re.match(r'^(.+?)\.(?:\s|$)', after)
                result['title'] = sm.group(1).strip() if sm else after.strip().rstrip('.')
        return result

    # ── Strategy 3: (YEAR). with period — APA-like ───────────────────────
    # Also accepts a trailing disambiguation letter before the closing paren,
    # e.g. "(2016a). " used by eLife/Cell preprints.
    apa_m = re.search(r'\((?:19|20)\d{2}[a-z]?\)\.\s+', clean)
    # Guard: Science/Nature inline format "Author, Journal Vol, Page (Year). NextAuthor"
    # looks like APA but has a digit (page number) immediately before the (Year). marker.
    # In true APA, only punctuation/text follows the author block — never a bare number.
    # Suppress APA so Strategy 3c can handle it correctly.
    if apa_m and re.search(r'\d\s*$', clean[:apa_m.start()]):
        apa_m = None
    if apa_m:
        result['authors'] = _split_authors(clean[:apa_m.start()])
        after = clean[apa_m.end():]

        # Title: first sentence. Use lookahead so the capital letter isn't consumed.
        tm = re.search(r'^(.+?)\.(?=\s+[A-Z]|$)', after)
        if tm:
            result['title'] = tm.group(1).strip()
            rest = after[tm.end():].lstrip('. ').strip()
            # Journal: word(s) before first comma+digit  (handles "Journal, Vol(Issue)")
            jm = re.match(r'^([^,\d]+),\s*(\d+)(?:\((\d+)\))?', rest)
            if jm:
                result['journal'] = jm.group(1).strip()
                result['volume']  = jm.group(2)
                result['issue']   = jm.group(3)
            else:
                # Also handle "Journal Vol, Pages" (space instead of comma before vol)
                # e.g. "Science 353, 925-928." or "Genome Res 44, D286-D293."
                jm2 = re.match(r'^([A-Za-z][^,\d]+\S)\s+(\d+),\s*([\d–—A-Za-z-]+)', rest)
                if jm2:
                    result['journal'] = jm2.group(1).strip()
                    result['volume']  = jm2.group(2)
                    result['pages']   = jm2.group(3)
        else:
            result['title'] = after.strip().rstrip('.')
        return result

    # ── Strategy 3b: eLife/NLM bare-year — "Authors. YEAR. Title. Journal Vol:Pages." ──
    # Signature: year appears as a free-standing "sentence" — a bare 4-digit year
    # immediately preceded AND followed by ". ".  This is distinct from APA "(YEAR)."
    # (parentheses) and Vancouver "YEAR;Vol" (semicolon).
    # e.g. "COHORTS group. 2013. Associations of linear growth … Lancet 382:525–534."
    elife_nlm_m = re.search(r'(?<![(\d])\.\s+((?:19|20)\d{2})\.\s+(?=\S)', clean)
    if elife_nlm_m and not nlm_m and not apa_m:
        result['authors'] = _split_authors(clean[:elife_nlm_m.start()])
        after = clean[elife_nlm_m.end():]   # starts with capital of title

        # vol:pages pattern e.g. "Lancet 382:525–534"  or  "eLife 5:e13410"
        vol_m = re.search(r'\s(\d+(?:\(\d+\))?):[\w–—-]+', after)
        if vol_m:
            before_vol = after[:vol_m.start()].strip()
            # "Title. Journal" — split at the LAST ". " before the volume number
            last_dot = before_vol.rfind('. ')
            if last_dot > 0:
                result['title']   = before_vol[:last_dot].strip()
                result['journal'] = before_vol[last_dot + 2:].strip()
            else:
                result['title'] = before_vol.strip()
            vi_m = re.match(r'(\d+)(?:\((\d+)\))?', vol_m.group(1))
            if vi_m:
                result['volume'] = vi_m.group(1)
                if vi_m.group(2):
                    result['issue'] = vi_m.group(2)
            result['pages'] = vol_m.group(0).split(':')[-1].strip()
        else:
            # No vol:pages — title is everything up to the first ". Capital"
            sm = re.match(r'^(.+?)\.(?=\s+[A-Z]|$)', after)
            result['title'] = sm.group(1).strip() if sm else after.strip().rstrip('.')
        return result

    # ── Strategy 3c: Nature/end-year — "Authors. Title. Journal Vol, Pages (Year)." ──
    # Signature: (YEAR) appears at the very end, not near the authors.
    # e.g. "Novoselov, K. S. et al. Electric field effect … Science 306, 666–669 (2004)."
    #      "Harrow, J. et al. GENCODE: … Genome Res. http://doi.org/… (2012)."
    # This is the standard Nature/Science citation format.
    #
    # Key difference from APA/NLM: the year is at the END inside parentheses, not near
    # the authors.  Vol, Pages use a comma separator (not colon as in NLM/eLife).
    #
    # Two sub-cases:
    #   (a) Vol, Pages (Year)   — traditional print citation
    #   (b) URL/DOI (Year)      — online-first without volume/pages
    nat_vol_m = re.search(
        r'\s(\d+),\s*([\d–—-]+(?:[\d–—-]\d+)?)\s*\((?:19|20)\d{2}\)\.?\s*$', clean
    )
    # Fallback: same pattern WITHOUT the end-of-string anchor — catches merged refs
    # where the reference extractor ran two citations together, so the year is not
    # at position $. Used only when nat_vol_m (anchored) is None.
    nat_vol_m_any = nat_vol_m or re.search(
        r'\s(\d+),\s*([\d–—-]+(?:[\d–—-]\d+)?)\s*\((?:19|20)\d{2}\)\.?', clean
    )
    nat_end_m = re.search(
        r'(?:\((?:19|20)\d{2}[a-z]?\)|\(this\s+issue\)|\(in\s+press\))\s*\.?\s*$',
        clean,
    )
    if (nat_end_m or nat_vol_m_any) and not nlm_m and not apa_m and not elife_nlm_m:
        # ── Extract vol and pages if present ─────────────────────────────────
        if nat_vol_m_any:
            result['volume'] = nat_vol_m_any.group(1)
            result['pages']  = nat_vol_m_any.group(2)
            # Text before the Vol, Pages (Year) block
            before_end = clean[:nat_vol_m_any.start()].strip()
        else:
            # No vol/pages — strip the trailing URL (if any) and "(Year)." /
            # "(this issue)." / "(in press)."
            # e.g. "http://dx.doi.org/10.xxxx/gr.xxx.111 (2012)."
            before_end = re.sub(
                r'\s*(?:https?://\S+\s*)?'
                r'(?:\((?:19|20)\d{2}[a-z]?\)|\(this\s+issue\)|\(in\s+press\))'
                r'\.?\s*$',
                '',
                clean,
            ).strip()

        # ── Extract the journal name: the last run of uppercase-starting words
        # (incl. abbreviations) immediately before the vol number or URL/year.
        # e.g. "…elegans. Proc. Natl Acad. Sci. USA" → "Proc. Natl Acad. Sci. USA"
        #      "…carbon films. Science" → "Science"
        #      "…ENCODE project. Genome Res." → "Genome Res"
        jour_m = re.search(
            r'(?:^|\.\s+)((?:[A-Z][^\s,\d]*\s+)*[A-Z][^\s,\d]*)\s*$',
            before_end,
        )
        if jour_m:
            result['journal'] = jour_m.group(1).strip().rstrip('.')
            # Everything before the journal name = authors + title
            before_journal = before_end[:jour_m.start()].strip()
        else:
            before_journal = before_end

        # ── Find the author / title boundary ─────────────────────────────────
        # Priority 1: "et al." as a definitive author-list terminator.
        etal_m = re.search(r'\bet\s+al\.?[\s,]+', before_journal, re.IGNORECASE)
        # Priority 2: "& Surname, I." — the last author in the list.
        amp_m  = re.search(r'&\s+\w+,\s+[A-Z]\.\s+(?=[A-Z\d])', before_journal)

        if etal_m:
            auth_end = etal_m.end()
        elif amp_m:
            auth_end = amp_m.end()
        else:
            # Fallback: first ". " followed by a non-initial word
            # (title starts with a full word, not an author initial)
            fm = re.search(r'\.\s+(?=[A-Z][a-z])', before_journal)
            auth_end = fm.end() if fm else 0

        if auth_end > 0:
            result['authors'] = _split_authors(
                before_journal[:auth_end].rstrip(' ,').rstrip('.')
            )
            result['title'] = before_journal[auth_end:].strip().rstrip('.')
        else:
            result['title'] = before_journal.strip().rstrip('.')
        return result

    # ── Strategy 4: Vol (YEAR) Pages — Physics/APS (no title) ───────────────
    # Signature: "Journal Abbrev. Vol (YEAR) Pages"
    # e.g. "F.D.M. Haldane, Phys. Rev. Lett. 93 (2004) 206602."
    # Optional comma covers "Vol, (YEAR)" format (e.g. "Phys. Lett. A 378, (2014) 1180")
    phys_m = re.search(r'\b(\d+)\s*,?\s*\((?:19|20)\d{2}\)\s+\S', clean)
    if phys_m and not nlm_m and not apa_m:
        before = clean[:phys_m.start()].rstrip().rstrip(',')
        # Journal: last abbreviated chunk — "Word. Word." right before the volume.
        # Handles:  Phys. Rev. Lett.   Phys. Rev. B   Phys. Lett. A
        #           J. Phys. A: Math. Theor.   Eur. Phys. J. B
        # Key changes vs. old regex:
        #   [a-zA-Z]* (not +) so single-letter abbrevs like "J." work
        #   optional trailing single capital (series letter: "B", "A", …)
        #   optional colon-sub-journal after that letter ("A: Math. Theor.")
        jm = re.search(
            r'([A-Z][a-zA-Z]*\.(?:\s+[A-Z][a-zA-Z]*\.)*'
            r'(?:\s+[A-Z](?::\s*[A-Z][a-zA-Z]*\.(?:\s+[A-Z][a-zA-Z]*\.)*)?)?'
            r')\s*$',
            before,
        )
        if jm:
            result['journal'] = jm.group(1).rstrip('.')
            result['authors'] = _split_authors(before[:jm.start()].rstrip().rstrip(','))
        else:
            result['authors'] = _split_authors(before)
        result['volume'] = phys_m.group(1)
        # No title in physics format — leave as None so Crossref uses raw text
        return result

    # ── Strategy 5: period-separated sentences — Vancouver / Harvard ──────
    sentences = re.split(r'\.\s+', clean)
    # Guard: if the first "sentence" is just one or two letters, the citation
    # starts with an author initial (e.g. "A" from "A. Bruguières, Titre, …"
    # or "Ö" from "Ö. F. Dayi, …").  Period-splitting is unreliable here.
    # Instead try comma-splitting to extract "Initial. Surname" authors.
    if len(sentences) >= 2 and re.match(r'^[\wÀ-ɏ]{1,2}$', sentences[0].strip()):
        parts = [p.strip() for p in clean.split(',') if p.strip()]
        authors = []
        for part in parts:
            # "et al." — end-of-author-list marker; append and stop
            if part.lower().startswith('et al'):
                authors.append(part.rstrip('.,'))
                break
            # Author initial pattern: uppercase-only prefix (no lowercase letters),
            # possibly hyphenated ("S.-M."), followed by period + space + capital.
            # This reliably distinguishes initials from journal abbreviations:
            #   "A. Smith"   "I. M. Isaacs"  "S.-M. Hong"  "Ö. F. Dayi"  → match
            #   "Phys."  "Math."  "Ann."  (have lowercase before ".") → reject
            # Also reject parts that end with "." (journal abbrev trailing period).
            if (re.match(r'^[A-ZÀ-ɏ]([A-ZÀ-ɏ\-\.])*\.\s+[A-ZÀ-ɏ]', part)
                    and not part.rstrip().endswith('.')):
                authors.append(part.rstrip('.,'))
            else:
                break
        if authors:
            result['authors'] = authors
            rest_parts = parts[len(authors):]
            if rest_parts:
                result['title'] = rest_parts[0].rstrip('.,')
        return result
    if len(sentences) >= 2:
        # Special case: "Author, I. et al. Title. Journal (Year)."
        # Period-splitting gives sentences[1] == "et al" because "et al." ends with
        # a period, making it look like a separate sentence.  When this happens,
        # merge "et al" back into the author string and shift title/journal indices.
        if sentences[1].strip().lower().startswith('et al'):
            result['authors'] = _split_authors(sentences[0] + ', et al')
            title_idx = 2
        else:
            result['authors'] = _split_authors(sentences[0])
            title_idx = 1
        if title_idx < len(sentences):
            result['title'] = sentences[title_idx].strip()
        if title_idx + 1 < len(sentences):
            jpart = sentences[title_idx + 1].strip()
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
    # Strip editor role markers that appear in book / proceedings citations.
    # e.g. "Murray CJL, Lopez AD, (eds) (1996) The global burden of disease"
    # "(eds)" and "(ed.)" mark editors in the author list — remove so the NLM year
    # detector can find "(YEAR)" without the "(eds)" confusing the guard heuristic.
    citation = re.sub(r'\s*\([Ee]ds?\)\.?', '', citation)
    # Strip Nature-style running footers that survived line-joining.
    # Four patterns handled:
    #   Full footer:   "… (2012). 7 2 | N AT U R E | VO L 489 | …"   (contains "| VOL")
    #   Second line:   "… 4 8 9 | 6 S E P T E M B E R 2 0 1 2 ©2012 Macmill" (pipe + ©)
    #   Inline ©year:  "… http://doi.org/10.1101/gr.xxx (2012). ©2012 Macmillan …"
    #   Column-split:  "… n Magazines Ltd NATURE | VOL 409 | 15 FEBRUARY …"
    citation = re.sub(r'\s+[\d\s]*\|[^|]*\|\s*VO\s*L\b.*$', '', citation)
    citation = re.sub(r'\s+[\d\s]*\|[^\n]*©\s*(?:19|20)\d{2}\S*.*$', '', citation)
    citation = re.sub(r'\s+©\s*(?:19|20)\d{2}\b.*$', '', citation)
    # Strip Nature/Macmillan footer fragments surviving the column-deinterleave split.
    # Full footer: "©2001 Macmillan Magazines Ltd  NNN articles  Nature Vol NNN | ..."
    # The ©YEAR prefix is already stripped above; what remains can be:
    #   "n Magazines Ltd NATURE | VOL 409 | …"  (Macmilla-n / column split)
    #   "es Ltd NNN articles Nature 380 …"  (Magazin-es / column split)
    # Strip "NATURE | VOL" first (covers the most text), then clean up any "Magazines Ltd" remnant.
    citation = re.sub(r'\s+NATURE\s*\|\s*VOL\b.*$', '', citation, flags=re.IGNORECASE)
    citation = re.sub(r'\s+(?:\w+\s+)?Magazines?\s+Ltd\b.*$', '', citation, flags=re.IGNORECASE)
    citation = re.sub(r'\s+\w+\s+Ltd\s+\d+\s+articles\s+Nature\b.*$', '', citation, flags=re.IGNORECASE)
    # Strip PLOS running page-number footers that appear inline in citation text.
    # e.g. ". October 6, 2015 18 / 22 PLOS Medicine | DOI:10.1371/…"
    citation = re.sub(
        r'\s+\d+\s*/\s*\d+\s+PLOS\b.*$',
        '',
        citation,
    )
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

# Matches [1], [ABC+94], [DL+ 94], 1., 1) at line start.
# Note: colon (:) is intentionally excluded from the separator class — "Vol:pages"
# patterns like "365: 488-492" would otherwise be falsely detected as ref #365.
# Trailing `[ \t]*(?!\d)` (not `[ \t]+`) so PMC's tight "1.Rahib L" form is caught;
# the `(?!\d)` guard avoids mis-firing on decimals like "19.5%". Mirrors
# ref_isolation._NUM_ENTRY_RE — keep the two in sync.
_NUM_MARKER_RE = re.compile(r'(?m)^[ \t]*(?:\[[\w+\-][\w+\-\s]*\]|\d{1,3}[.)])[ \t]*(?!\d)')

# PMC/PubMed HTML-export link badges that trail each reference. Stripped before
# splitting so they can't be mistaken for entry-start markers. Mirrors
# ref_isolation._LINK_BADGE_RE.
_LINK_BADGE_RE = re.compile(
    r'\[(?:DOI|PubMed|PubMed\s+Central|PMC(?:\s+free\s+article)?|Free\s+PMC\s+article'
    r'|Google\s+Scholar|CrossRef|Web\s+of\s+Science|Full\s+Text|Abstract)\]',
    re.IGNORECASE,
)

# Blocks that should be silently discarded — they are PDF noise, not citations.
#   • eLife section labels that survived the parse_refs strip (leading indent, multi-line)
#   • Figure captions / panel labels embedded mid-references (eLife 27041)
#   • Bare page-number/range fragments from a citation split across pages
#   • tSNE / biSNE / axis-label artefacts (eLife 27041 figure panels)
#   • Single-character / Roman-numeral figure panel labels ("I", "II", "IV VI VII")
_NOISE_BLOCK_RE = re.compile(
    r'^(?:'
    r'(?:Research article|Tools and resources|Research Advance'
    r'|Short report|Insight|Review article|Feature article'
    r'|ARTICLE\s+RESEARCH|LETTER\s+RESEARCH)\b'                # eLife / Nature running headers
    r'|Figure\s+\d+'                                             # "Figure 1", "Figure 2A"
    r'|[A-Z]\s+(?:Retina|Cell|Splenic|Bone\s+marrow|Annelid|Neurogenesis|'
    r'Intestinal|Stem\s+cell|Wishbone)\b'                       # "A Retina…", "C Stem cell…"
    r'|biSNE\b|tSNE\s*\d*\b'                                   # dimensionality-reduction axes
    r'|(?:DC|Tfh|Latent\s+variable)\s*\d*\b'                   # cell type and feature labels
    r'|(?:[IVX]+\s+){2,}[A-Za-z\s]*$'                           # "IV VI VII Apical view Lateral"
    r'|[IVX]+\s*$'                                             # single Roman numeral "I", "II"
    r'|Circular\s+projection\b'                                 # figure legend keywords
    r'|(?:Tip|Precursor|Decision)\s+(?:branch|state)\b'        # scRNA-Seq trajectory labels
    r'|(?:Intestinal|Gastrointestinal|Small\s+intestine)\b'     # anatomy figure labels
    r'|PC\d+(?:\s+PC\d+)*\b'                                   # PCA axis labels "PC1", "PC2 PC1"
    r'|\d+\s*hr\b|\d+\s*day\b'                                 # time-course labels "6 hr", "14 day"
    r'|PMID:\s*\d+'                                            # PMID-only blocks "PMID: 17230172"
    r'|\d+\.\s*(?:PMID|doi)[:\s]'                             # "259. PMID: 17230172" page+ID fragment
    r'|See\s+support(?:ing)?\s+material\b'                     # supplementary material references
    r'|[eE]?\d+[–—-]\d+\.?\s*'                                # bare page range "488–492. text..." or "737–40."
    r'|e\d+\.?\s*'                                             # PLoS article number "e3376." fragment
    r'|\d+\.\s*$'                                              # lone reference number "1." with nothing after
    r')',
    re.IGNORECASE,
)
# Note: \d{1,3} (max 3 digits) intentionally excludes 4-digit years (e.g. "2011.")
# that appear at line-starts when NLM citations wrap across lines.


def split_citations(text: str) -> list[str]:
    """Split a block of text into individual citation strings.

    Handles three formats automatically:
      1. Numbered   — [1], 1., 1) at line start
      2. Blank-line — paragraphs separated by empty lines
      3. Per-line   — each non-empty line is one citation
    """
    text = _LINK_BADGE_RE.sub('', text).strip()
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
    then parses each citation with the universal parser.  Known noise blocks
    (eLife section labels, figure captions, bare page fragments) are silently skipped.
    """
    return [
        parse_citation(c)
        for c in split_citations(text)
        if not _NOISE_BLOCK_RE.match(c.strip())
    ]


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
