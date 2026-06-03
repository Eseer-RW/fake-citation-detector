"""
parse_refs.py — extract, parse, and look up every citation in a paper.

Usage:
    python3 parse_refs.py paper.pdf             # extract refs from PDF, then parse + lookup
    python3 parse_refs.py references.txt        # parse a plain-text reference list
    python3 parse_refs.py                        # paste text, then press Ctrl+D
"""
import sys
import re
import requests
sys.path.insert(0, ".")
from parser import parse_all_citations, ParsedCitation, CitationStyle


# ── PDF extraction ────────────────────────────────────────────────────────

# Headings that mark the start of a references section (case-insensitive)
_REF_HEADING_RE = re.compile(
    r'^\s*(?:\d+[\.\s]+)?'                          # optional section number e.g. "7. "
    r'(?:references|bibliography|works\s+cited'
    r'|literature\s+cited|reference\s+list)'
    r'\s*$',
    re.IGNORECASE | re.MULTILINE,
)



def _is_garbled(text: str) -> bool:
    """Return True if text has too many suspiciously long 'words' (spaces missing)."""
    words = text.split()
    if not words:
        return True
    long_runs = sum(1 for w in words if len(w) > 25)
    return (long_runs / len(words)) > 0.08   # >8% of tokens are 25+ chars → garbled


def _detect_column_split(page: str) -> int | None:
    """Return the character column where a right column of numbered refs starts, or None.

    Specifically looks for lines that begin with a numbered reference (e.g. "12. Author…")
    after heavy leading whitespace — the surest sign of a two-column layout.
    """
    indents = []
    for line in page.splitlines():
        stripped = line.lstrip(' ')
        indent   = len(line) - len(stripped)
        if indent > 50 and re.match(r'\d+\.\s+\w', stripped):
            indents.append(indent)
    if len(indents) < 2:
        return None
    return min(indents)   # left edge of the right column


def _deinterleave_columns(page: str, split_col: int) -> str:
    """Re-order a two-column pdftotext page: all left-column lines, then right-column lines.

    pdftotext -layout places left and right column content side-by-side on each
    physical line.  Splitting at split_col and collecting each half separately
    restores the natural reading order (left column top→bottom, then right column
    top→bottom).

    Right-column content before the first numbered reference is skipped — this
    drops running headers and other page-margin text that sits in the right column
    above where the reference list begins.
    """
    left_lines  = []
    right_lines = []
    right_started = False   # becomes True once we see a numbered ref in the right col
    for line in page.splitlines():
        left_part  = line[:split_col].rstrip()        if len(line) >= split_col else line.rstrip()
        right_part = line[split_col:].strip()          if len(line) >  split_col else ""
        if left_part:
            left_lines.append(left_part)
        if right_part:
            if not right_started and re.match(r'\d+\.\s+\w', right_part):
                right_started = True
            if right_started:
                right_lines.append(right_part)
    return '\n'.join(left_lines) + '\n\n' + '\n'.join(right_lines)


def _extract_with_pdftotext(pdf_path: str) -> list[str]:
    """Use pdftotext -layout (poppler) — best at preserving spaces and columns.

    Also de-interleaves two-column pages so each column's text runs sequentially.
    """
    import subprocess
    result = subprocess.run(
        ["pdftotext", "-layout", pdf_path, "-"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return []
    # Split into per-page chunks on the form-feed character pdftotext inserts
    pages = result.stdout.split("\x0c")
    out = []
    for p in pages:
        if not p.strip():
            continue
        split = _detect_column_split(p)
        if split:
            p = _deinterleave_columns(p, split)
        out.append(p)
    return out


def _extract_with_pymupdf(pdf_path: str) -> list[str]:
    """Use PyMuPDF — good font-aware extraction with column ordering."""
    import fitz
    doc = fitz.open(pdf_path)
    pages_text = []
    for page in doc:
        # get_text("text") handles most fonts and preserves proper spacing
        text = page.get_text("text")
        pages_text.append(text or "")
    doc.close()
    return pages_text


def _extract_with_pdfplumber(pdf_path: str) -> list[str]:
    """Fall back to pdfplumber with crop-based column splitting."""
    import pdfplumber
    pages_text = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            mid_x = page.width / 2
            left  = page.crop((0, 0, mid_x, page.height)).extract_text() or ""
            right = page.crop((mid_x, 0, page.width, page.height)).extract_text() or ""
            if len(left.strip()) > 100 and len(right.strip()) > 100:
                pages_text.append(left + "\n" + right)
            else:
                pages_text.append(page.extract_text() or "")
    return pages_text


def extract_references_from_pdf(pdf_path: str) -> str:
    """Return the references section text extracted from a PDF file.

    Tries three extraction engines in order of reliability:
      1. pdftotext -layout  (poppler — best spacing and column handling)
      2. PyMuPDF            (fitz — good font-aware extraction)
      3. pdfplumber         (last resort — crop-based column split)
    Automatically detects garbled output (missing spaces) and falls through
    to the next engine.
    """
    import shutil

    print(f"Opening PDF: {pdf_path}")

    pages_text: list[str] = []
    engine_used = ""

    if shutil.which("pdftotext"):
        pages_text = _extract_with_pdftotext(pdf_path)
        sample = " ".join(pages_text[-3:])   # check last few pages (where refs usually are)
        if pages_text and not _is_garbled(sample):
            engine_used = "pdftotext"

    if not engine_used:
        try:
            import fitz  # noqa: F401
            pages_text = _extract_with_pymupdf(pdf_path)
            sample = " ".join(pages_text[-3:])
            if pages_text and not _is_garbled(sample):
                engine_used = "PyMuPDF"
        except ImportError:
            pass

    if not engine_used:
        try:
            import pdfplumber  # noqa: F401
            pages_text = _extract_with_pdfplumber(pdf_path)
            engine_used = "pdfplumber"
        except ImportError:
            print("Error: no PDF library found. Run: pip install pdfplumber")
            sys.exit(1)

    print(f"  {len(pages_text)} pages extracted using {engine_used}.")

    full_text = "\n".join(pages_text)

    # Find the references heading
    m = _REF_HEADING_RE.search(full_text)
    if not m:
        # Softer fallback: "References" anywhere as its own line
        m = re.search(r'(?m)^\s*References\s*$', full_text)
    if not m:
        # Last resort: find "References" followed by a newline and an author-like pattern
        m = re.search(r'References\n', full_text)

    if not m:
        print("  Warning: could not find a references heading — extracting last 30% of document.")
        start = int(len(full_text) * 0.70)
        refs_text = full_text[start:]
    else:
        # Figure out which page the match is on for the user's info
        matched_pos = m.start()
        chars_so_far = 0
        ref_page = 1
        for page_num, pt in enumerate(pages_text, 1):
            chars_so_far += len(pt) + 1  # +1 for the \n we joined with
            if chars_so_far >= matched_pos:
                ref_page = page_num
                break
        print(f"  References section found on page {ref_page}.")
        refs_text = full_text[m.end():].strip()

    # Clean up: remove bare page numbers (lines that are just a digit string)
    refs_text = re.sub(r'(?m)^\s*\d+\s*$', '', refs_text)
    # Fix soft hyphens at line breaks: "Manage-\n    ment" → "Management"
    refs_text = re.sub(r'(\w)-\n[ \t]*(\w)', r'\1\2', refs_text)
    # Fix PDF encoding artifact: "999e1006" → "999-1006" (en-dash encoded as "e")
    refs_text = re.sub(r'(\d)e(\d)', r'\1-\2', refs_text)
    # Collapse runs of blank lines down to one
    refs_text = re.sub(r'\n{3,}', '\n\n', refs_text).strip()

    return refs_text


# ── PMC / JATS XML extraction ────────────────────────────────────────────

def _xml_text(elem) -> str:
    """Return all text inside an XML element, including nested tags (e.g. <italic>)."""
    return ''.join(elem.itertext()).strip()


def _parse_xml_ref(ref_elem) -> 'ParsedCitation | None':
    """Extract citation fields from a JATS <ref> element.

    Handles both <element-citation> (fully structured) and
    <mixed-citation> (text mixed with inline tags).
    """
    citation = ref_elem.find('element-citation')
    if citation is None:
        citation = ref_elem.find('mixed-citation')
    if citation is None:
        return None

    # ── authors ──────────────────────────────────────────────────────────
    authors = []
    pg = citation.find("person-group[@person-group-type='author']")
    if pg is None:
        pg = citation.find('person-group')
    if pg is not None:
        for name in pg.findall('name'):
            surname   = (name.findtext('surname')   or '').strip()
            given     = (name.findtext('given-names') or '').strip()
            if surname:
                authors.append(f"{surname} {''.join(c for c in given if c.isupper())}".strip())
        collab = pg.find('collab')
        if collab is not None:
            authors.append(_xml_text(collab))

    # ── title ─────────────────────────────────────────────────────────────
    title = None
    t_elem = citation.find('article-title')
    if t_elem is not None:
        title = _xml_text(t_elem).rstrip('.')

    # ── journal ───────────────────────────────────────────────────────────
    journal = None
    s_elem = citation.find('source')
    if s_elem is not None:
        journal = _xml_text(s_elem)

    # ── year ──────────────────────────────────────────────────────────────
    year = None
    y_elem = citation.find('year')
    if y_elem is not None and y_elem.text:
        try:
            year = int(y_elem.text.strip()[:4])
        except ValueError:
            pass

    # ── volume / issue / pages ────────────────────────────────────────────
    volume = citation.findtext('volume')
    issue  = citation.findtext('issue')
    fpage  = citation.findtext('fpage')
    lpage  = citation.findtext('lpage')
    pages  = f"{fpage}-{lpage}" if fpage and lpage else (fpage or None)

    # ── DOI ───────────────────────────────────────────────────────────────
    doi = None
    for pub_id in citation.findall('pub-id'):
        if pub_id.get('pub-id-type') == 'doi' and pub_id.text:
            doi = pub_id.text.strip()
            break

    raw = _xml_text(citation)

    return ParsedCitation(
        raw     = raw,
        style   = CitationStyle.NLM,
        doi     = doi,
        authors = authors,
        title   = title,
        year    = year,
        journal = journal,
        volume  = volume,
        issue   = issue,
        pages   = pages,
    )


def extract_citations_from_xml(xml_path: str) -> list:
    """Extract all citations from a JATS/PMC XML file.

    Returns a list of ParsedCitation objects — no text parsing needed
    because the fields are already discrete XML elements.
    """
    import xml.etree.ElementTree as ET

    print(f"Opening XML: {xml_path}")
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as e:
        print(f"  Error parsing XML: {e}")
        return []

    root = tree.getroot()

    # ref-list can appear anywhere in the document tree
    ref_list = None
    for elem in root.iter():
        if elem.tag in ('ref-list',) or elem.tag.endswith('}ref-list'):
            ref_list = elem
            break

    if ref_list is None:
        print("  Warning: no <ref-list> found.")
        return []

    refs = [r for r in ref_list if r.tag == 'ref']
    print(f"  {len(refs)} references found in XML.")

    citations = []
    for ref in refs:
        c = _parse_xml_ref(ref)
        if c:
            citations.append(c)
    return citations


# ── Crossref API lookup ───────────────────────────────────────────────────

def _clean_fragment(raw: str) -> str:
    """Find the longest run of properly-spaced words inside garbled citation text.

    Many PDFs lose spaces in some places but keep them in others.
    This extracts the longest readable fragment to use as a title query.
    """
    # Sequences of 3+ space-separated words starting with a letter
    matches = re.findall(r'[A-Za-z][a-z]+(?:\s+[a-z]+){2,}', raw)
    if not matches:
        matches = re.findall(r'[A-Za-z]\w+(?:\s+[A-Za-z]\w+){2,}', raw)
    return max(matches, key=len) if matches else ""


def lookup_crossref(title: str, year: int = None, raw: str = None) -> dict:
    """Query the Crossref API and return structured metadata for the top match.

    Tries four strategies in order:
      1. Parsed title + year filter        (clean title, most precise)
      2. Parsed title without year filter  (broader date range)
      3. Longest clean fragment from raw   (handles partial garbling from PDFs)
      4. query.bibliographic with raw      (Crossref's NLP on the full citation)
    """
    def _query(params):
        resp = requests.get(
            "https://api.crossref.org/works",
            params=params,
            timeout=10,
        )
        return resp.json()["message"]["items"]

    try:
        items = []

        # Strategy 1 & 2: use parsed title — but only if it looks like a real title
        # (at least 4 words, no embedded YEAR;VOL pattern, not just a journal abbreviation)
        def _good_title(t):
            if not t or len(t.split()) < 4:
                return False
            if re.search(r'\d{4};|\d+\(\d+\):', t):   # looks like "J Clin. 2020;12(3):"
                return False
            return True

        if _good_title(title):
            params = {"query.title": title, "rows": 3}
            if year:
                params["filter"] = f"from-pub-date:{year - 1},until-pub-date:{year + 1}"
            items = _query(params)
            if not items and year:
                items = _query({"query.title": title, "rows": 3})

        # Strategy 3: extract the longest clean fragment from the raw citation text
        if not items and raw:
            fragment = _clean_fragment(raw)
            if len(fragment) > 20:
                params = {"query.title": fragment, "rows": 3}
                if year:
                    params["filter"] = f"from-pub-date:{year - 1},until-pub-date:{year + 1}"
                items = _query(params)
                if not items and year:
                    items = _query({"query.title": fragment, "rows": 3})

        # Strategy 4: send the full raw citation to Crossref's bibliographic search
        if not items and raw:
            items = _query({"query.bibliographic": raw[:200], "rows": 3})

        if not items:
            return {}

        top = items[0]

        authors = []
        for a in top.get("author", []):
            given  = a.get("given", "")
            family = a.get("family", "")
            authors.append(f"{family}, {given}".strip(", "))

        year_found = None
        try:
            year_found = top["issued"]["date-parts"][0][0]
        except (KeyError, IndexError, TypeError):
            pass

        ct = top.get("container-title", [])
        return {
            "doi":       top.get("DOI"),
            "title":     top.get("title", [None])[0],
            "authors":   authors,
            "year":      year_found,
            "journal":   ct[0] if ct else None,
            "volume":    top.get("volume"),
            "issue":     top.get("issue"),
            "pages":     top.get("page"),
            "publisher": top.get("publisher"),
            "type":      top.get("type"),
            "score":     top.get("score"),
        }

    except Exception as e:
        print(f"  [API error: {e}]")
        return {}


# ── get the text / citations ──────────────────────────────────────────────

citations = None   # set directly for XML; derived from text for everything else

if len(sys.argv) > 1:
    path = sys.argv[1]
    if path.lower().endswith(".pdf"):
        text = extract_references_from_pdf(path)
        print()
    elif path.lower().endswith(".xml"):
        citations = extract_citations_from_xml(path)
        print()
    else:
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            print(f"Reading from: {path}\n")
        except FileNotFoundError:
            print(f"Error: file not found: {path}")
            sys.exit(1)
else:
    print("Paste your reference section below, then press Ctrl+D (Mac/Linux) or Ctrl+Z + Enter (Windows):")
    print("-" * 60)
    text = sys.stdin.read()
    print("-" * 60 + "\n")

# ── parse + lookup ────────────────────────────────────────────────────────

if citations is None:
    citations = parse_all_citations(text)
print(f"Found {len(citations)} citations — looking up DOIs via Crossref...\n")

print("=" * 70)
for i, c in enumerate(citations, 1):
    print(f"\n[{i}] PARSED ({c.style.value.upper()})")
    print(f"     Title:   {c.title}")
    authors_str = ", ".join(c.authors[:3]) + ("..." if len(c.authors) > 3 else "")
    print(f"     Authors: {authors_str}")
    print(f"     Year:    {c.year}   Journal: {c.journal}")

    if c.doi:
        print(f"     DOI:     {c.doi}  (found in text)")
    elif c.title or c.raw:
        meta = lookup_crossref(c.title, year=c.year, raw=c.raw)
        if meta.get("doi"):
            print(f"     CROSSREF MATCH  (score={meta.get('score', '-')})")
            print(f"     DOI:       {meta['doi']}")
            print(f"     Title:     {meta.get('title')}")
            match_authors = ", ".join(meta.get("authors", [])[:3])
            if len(meta.get("authors", [])) > 3:
                match_authors += "..."
            print(f"     Authors:   {match_authors}")
            print(f"     Year:      {meta.get('year')}   Journal: {meta.get('journal')}")
            print(f"     Volume:    {meta.get('volume')}   Issue: {meta.get('issue')}   Pages: {meta.get('pages')}")
            print(f"     Publisher: {meta.get('publisher')}")
        else:
            print("     DOI:       not found in Crossref")
    else:
        print("     DOI:       (could not look up — no usable text)")
