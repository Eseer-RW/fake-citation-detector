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

    Handles two layouts:
    - Classic two-column: lines *start* with a numbered reference after heavy whitespace
      (indent > 50).
    - Three-column (e.g. JAMA): numbered references appear *mid-line* after a column
      gap (≥ 3 spaces) at position > 35.  Each column of refs is on the same physical
      line as the other columns.

    Returns the leftmost column position where any reference column begins.
    """
    positions = []
    for line in page.splitlines():
        stripped = line.lstrip(' ')
        indent   = len(line) - len(stripped)
        # Case 1: line starts with a numbered ref after heavy indentation (classic 2-col)
        if indent > 50 and re.match(r'\d+\.\s+\w', stripped):
            positions.append(indent)
        # Case 2: numbered ref mid-line after a column gap (multi-column, e.g. JAMA)
        for m_ref in re.finditer(r'(?<!\w)\d{1,2}\.\s+[A-Z]\w', line):
            pos = m_ref.start()
            if pos > 35 and line[max(0, pos - 3):pos] == '   ':
                positions.append(pos)
                break   # at most one mid-line match per line
    if len(positions) < 2:
        return None
    return min(positions)   # left edge of the leftmost reference column


def _deinterleave_columns(page: str, split_col: int,
                          _all_right: bool = False) -> str:
    """Re-order a pdftotext page that has content in two or more columns.

    Splits each physical line at *split_col*: everything to the left becomes
    "left-column" content, everything to the right becomes "right-column" content.
    The two halves are output sequentially (left column top→bottom, then right column
    top→bottom), restoring natural reading order.

    If the right column itself has a secondary column split (detected by
    _detect_column_split), it is recursively deinterleaved, handling 3-col layouts.

    _all_right=True skips the "wait for first numbered ref" guard on the right column —
    used for recursive inner calls where we're already inside the reference section.
    """
    left_lines  = []
    right_lines = []
    right_started = _all_right   # if True, include right content from the very first line
    for line in page.splitlines():
        # Special case: a section-heading line ("References", "Bibliography", …) that
        # spans or falls just before the split column.  Such lines are often shorter than
        # split_col (the heading has no continuation text to the right), so the normal
        # left/right split would either swallow the heading into left_part or chop it
        # mid-word.  Detect them early and route them into right_lines whole.
        stripped_line = line.strip()
        if re.match(
            r'^(?:references|bibliography|works\s+cited'
            r'|literature\s+cited|reference\s+list)\s*$',
            stripped_line, re.IGNORECASE
        ):
            right_lines.append(stripped_line)
            right_started = True
            continue

        left_part  = line[:split_col].rstrip()   if len(line) >= split_col else line.rstrip()
        right_part = line[split_col:].strip()     if len(line) >  split_col else ""
        if left_part:
            left_lines.append(left_part)
        if right_part:
            if not right_started:
                # Start capturing right column once we see a numbered ref OR the
                # section heading "REFERENCES" (e.g. mid-line header in JAMA)
                if re.match(r'\d+\.\s+\w', right_part) or re.match(r'REFERENCES', right_part):
                    right_started = True
            if right_started:
                right_lines.append(right_part)

    right_text = '\n'.join(right_lines)

    # Recursive: if the right portion also has a column split, deinterleave it too.
    # Use _all_right=True so we don't skip any content in the inner right column.
    inner_split = _detect_column_split(right_text)
    if inner_split:
        right_text = _deinterleave_columns(right_text, inner_split, _all_right=True)

    return '\n'.join(left_lines) + '\n\n' + right_text


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


def _strip_line_numbers(text: str) -> str:
    """Remove manuscript line numbers that eLife preprints stamp on every line.

    Handles both 4-digit and 5-digit sequential numbers followed by whitespace:
      '1104   Achim, K., Pettit, J.B., …' → 'Achim, K., Pettit, J.B., …'
    """
    return re.sub(r'(?m)^\d{4,5}[ \t]+', '', text)


def _segment_hanging_indent(text: str) -> str:
    """Insert blank lines between hanging-indent NLM citation blocks (eLife format).

    In eLife NLM PDFs the reference section has no blank lines between citations.
    Each citation's first line is at a *shallower* indent than its continuation
    lines (a hanging-indent layout):

      [  0 sp] Adair LS, Fall CH, …          ← first citation (indent 0 after .strip())
      [ 36 sp]   Micklesfield L, … Lancet…   ← continuation
      [ 34 sp] Barker DJ, … 2005. …          ← new citation start
      [ 36 sp]   in Childhood 90:272…        ← continuation
      [ 34 sp] Baten J, … 2012. …            ← new citation start

    Strategy:
      1. Find the *dominant deep indent* — the most-common indent among non-empty
         lines with indent ≥ 20.  That is the continuation-line indent.
      2. Any non-empty line with indent < cont_indent immediately following a
         non-blank line is a citation boundary → insert a blank line before it.
    Only activates when the dominant deep indent is ≥ 20 chars and appears ≥ 3 times.
    """
    from collections import Counter

    lines = text.splitlines()
    if not lines:
        return text

    # Count how often each deep indent level appears
    deep_counts: Counter = Counter(
        len(l) - len(l.lstrip(' '))
        for l in lines
        if l.strip() and len(l) - len(l.lstrip(' ')) >= 20
    )
    if not deep_counts:
        return text  # no deep indentation → not a hanging-indent layout

    # Dominant deep indent = the continuation-line column
    cont_indent, cnt = deep_counts.most_common(1)[0]
    if cnt < 3:
        return text  # not enough evidence

    # Sanity check: the continuation-indent lines must make up at least 40% of all
    # non-empty lines.  If only a small fraction have the deep indent (e.g. scattered
    # supplementary table rows), this is NOT a hanging-indent citation block and we
    # should not insert spurious blank lines before every shallow line.
    total_nonempty = len([l for l in lines if l.strip()])
    if cnt / total_nonempty < 0.40:
        return text

    # Insert blank lines before every line that is shallower than cont_indent
    # and follows a non-blank line (i.e. starts a new citation).
    out: list[str] = []
    for line in lines:
        s = line.lstrip(' ')
        if not s:
            out.append(line)
            continue
        indent = len(line) - len(s)
        if indent < cont_indent and out and out[-1].strip():
            out.append('')   # blank separator before new citation
        out.append(line)

    # Strip leading whitespace from all content lines now that indentation is no longer
    # needed as a structural signal (citations are separated by blank lines).
    out = [l.lstrip(' ') if l.strip() else l for l in out]

    return '\n'.join(out)


def _segment_apa_refs(text: str) -> str:
    """Insert blank lines between APA-style citations that lack blank-line separators.

    Used for eLife preprints whose line numbers have been stripped but whose
    citation blocks still run together.  Detects the boundary where the
    previous line ends a sentence (".") and the next line starts an APA author
    name (Surname, Initial.) followed by a comma or a year in parentheses:

      'tissue of origin. Nature Biotechnology 33, 503-509.'   ← prev ends with "."
      'Adamson, B., Norman, T.M., …'                          ← new citation start
    """
    _APA_START = re.compile(
        r'^[A-Z][a-záéíóúàèìòùâêîôûäëïöüãõçñ\-]+,\s+[A-Z][a-zA-Z.]*'
        r'(?:,|\s*\((?:19|20)\d{2}\))'
    )

    lines = text.splitlines()
    out: list[str] = []
    prev_ends_sentence = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            prev_ends_sentence = False
            continue
        if prev_ends_sentence and _APA_START.match(stripped):
            if out and out[-1].strip():
                out.append('')   # blank separator
        out.append(line)
        prev_ends_sentence = stripped.endswith('.')

    return '\n'.join(out)


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
        # Inline heading: "REFERENCES" embedded mid-line (e.g. JAMA 3-column header)
        m = re.search(r'\bREFERENCES\b', full_text)
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
    # Strip Nature-style running page headers/footers that get interleaved with refs.
    # These look like: "7 2 | N AT U R E | VO L 4 8 9 | 6 S E P T E M B E R 2 0 1 2"
    # The footer may span two physical lines:
    #   Line 1: "7 2 | N AT U R E | VO L"          ← matched by first pattern (has "VOL")
    #   Line 2: "4 8 9 | 6 S E P T E M B E R 2 0 1 2 ©2012 Macmill"  ← matched by second
    refs_text = re.sub(
        r'(?m)^\s*[\d\s]+\|[^|\n]+\|\s*VO\s*L\b[^\n]*$',
        '',
        refs_text,
    )
    # Second line of the Nature footer: "digits | MONTH YEAR ©YEAR Publisher"
    refs_text = re.sub(
        r'(?m)^\s*[\d\s]+\|[^\n]*©\s*(?:19|20)\d{2}[^\n]*$',
        '',
        refs_text,
    )
    # Strip eLife running section headers that get injected into the ref section
    # on every page.  The label appears on its own (indented) line, optionally followed
    # by discipline text on the SAME line:
    # "    Research article                          Epidemiology and Global Health"
    # "    Tools and resources    Computational and Systems Biology Microbiology …"
    # Also strip Nature "ARTICLE RESEARCH" / "LETTER RESEARCH" running headers.
    refs_text = re.sub(
        r'(?m)^\s*(?:Research article|Tools and resources|Research Advance'
        r'|Short report|Insight|Review article|Feature article'
        r'|ARTICLE\s+RESEARCH|LETTER\s+RESEARCH|LETTER|ARTICLE'
        r'|Extended\s+Data)(?:\s+[A-Z][^\n]*)?\s*$',
        '',
        refs_text,
    )
    # Fix soft hyphens and dashes at line breaks: "Manage-\nment" → "Management",
    # "627–\n640" → "627-640"  (en-dash and em-dash variants too)
    refs_text = re.sub(r'(\w)[-–—]\n[ \t]*(\w)', r'\1-\2', refs_text)
    # Fix PDF encoding artifact: "999e1006" → "999-1006" (en-dash encoded as "e")
    refs_text = re.sub(r'(\d)e(\d)', r'\1-\2', refs_text)
    # Fix PDF ± artifact: "225±246" → "225-246" (old Nature PDFs encode en-dash as ±)
    refs_text = re.sub(r'(\d)±(\d)', r'\1-\2', refs_text)
    # Strip old Macmillan/Nature running footers that don't use the | separator:
    # "©2001 Macmillan Magazines Ltd  NNN articles Nature Vol NNN …"
    refs_text = re.sub(
        r'(?m)^\s*©\s*(?:19|20)\d{2}\s+Macmillan[^\n]*$',
        '',
        refs_text,
    )
    # Strip JAMA "(Reprinted)" footers that get appended to citation text.
    # e.g. " (Reprinted) JAMA August 25, 2020 Volume 324, Number 8 ©2020 …"
    refs_text = re.sub(
        r'\s*\(Reprinted\)\s+JAMA\b[^\n]*',
        '',
        refs_text,
    )
    # Strip PLOS running page-number footers that appear mid-references section.
    # e.g. "October 6, 2015 18 / 22 PLOS Medicine | DOI:10.1371/journal.pmed.1001885"
    refs_text = re.sub(
        r'(?m)^\s*(?:January|February|March|April|May|June|July|August'
        r'|September|October|November|December)\s+\d{1,2},\s+\d{4}'
        r'\s+\d+\s*/\s*\d+\s+PLOS\b[^\n]*$',
        '',
        refs_text,
    )
    # Collapse runs of blank lines down to one
    refs_text = re.sub(r'\n{3,}', '\n\n', refs_text).strip()

    # ── eLife / preprint post-processing ────────────────────────────────────
    # Strip 4-5 digit manuscript line numbers (eLife preprints stamp every line)
    refs_text = _strip_line_numbers(refs_text)
    # Re-run blank-line collapse after stripping (line numbers may leave artifacts)
    refs_text = re.sub(r'\n{3,}', '\n\n', refs_text).strip()
    # Segment hanging-indent NLM-style blocks (eLife published: deep indent, no gaps)
    refs_text = _segment_hanging_indent(refs_text)
    # If still no blank lines between refs, try APA sentence-boundary detection
    if '\n\n' not in refs_text:
        refs_text = _segment_apa_refs(refs_text)

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

    except KeyboardInterrupt:
        raise   # let Ctrl+C propagate so the main loop can catch it cleanly
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
try:
  for i, c in enumerate(citations, 1):
    print(f"\n[{i}] PARSED ({c.style.value.upper()})")
    raw_display = c.raw[:95].replace('\n', ' ') + ('…' if len(c.raw) > 95 else '')
    print(f"     Raw:     {raw_display}")
    print(f"     Title:   {c.title}")
    authors_str = ", ".join(c.authors[:3]) + ("..." if len(c.authors) > 3 else "")
    print(f"     Authors: {authors_str}")
    print(f"     Year:    {c.year}   Journal: {c.journal}")

    if c.doi:
        print(f"     DOI:     {c.doi}  (found in text)")
    elif c.title or c.raw:
        meta = lookup_crossref(c.title, year=c.year, raw=c.raw)
        if meta.get("doi"):
            score = meta.get('score') or 0
            confidence = "LOW CONFIDENCE — verify manually" if score < 40 else f"score={score:.1f}"
            print(f"     CROSSREF MATCH  ({confidence})")
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
except KeyboardInterrupt:
    print("\n\nStopped early — results above are complete up to this point.")
    sys.exit(0)
