"""text_repair.py — repair PDF text-extraction artifacts in reference titles.

Old (Adobe/Times-encoded) PDFs export fi/fl ligatures and some dashes as the wrong glyphs,
so GROBID hands us titles like `modi®ed`, `in¯uenza`, `®rst`, `WorkshopÐMay`. GROBID also
sometimes leaks a leading consortium/author string into the title (`The BAC Resource
Consortium. Integration of…`) or spells a Greek letter as a word (`beta`, `lambda`). Any of
these makes the normalized title fail exact matching even though the paper is real. These
helpers restore/clean the title BEFORE normalization; `title_repair_variants` yields the
distinct cleaned candidates to retry.
"""
import re

# ── ligature / dash mojibake ───────────────────────────────────────────────
_LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "ft", "ﬆ": "st"}
_MOJIBAKE_LIG = {"®": "fi", "¯": "fl"}
_LIG_RE = {bad: re.compile(re.escape(bad) + r"(?=[A-Za-z])") for bad in _MOJIBAKE_LIG}
_MOJIBAKE_DASH = {"Ð": "-"}


def repair_pdf_ligatures(s):
    """Restore observed PDF ligature/dash mojibake; idempotent."""
    if not s:
        return s
    for lig, rep in _LIGATURES.items():
        if lig in s:
            s = s.replace(lig, rep)
    for bad, good in _MOJIBAKE_LIG.items():
        if bad in s:
            s = _LIG_RE[bad].sub(good, s)
    for bad, good in _MOJIBAKE_DASH.items():
        if bad in s:
            s = s.replace(bad, good)
    return s


# ── leaked consortium/author prefix ────────────────────────────────────────
_LEAD_CONSORTIUM_RE = re.compile(
    r"^\s*(?:The\s+)?[A-Z][\w&'./-]*(?:\s+[\w&'./-]+){0,8}?\s+"
    r"(?:Consortium|Collaboration|Working\s+Group|Study\s+Group|Group|Project|Team"
    r"|Initiative|Network|Investigators|Committee)\.?\s+(?=[A-Z0-9])")
_LEAD_INITIAL_RE = re.compile(r"^\s*(?:[A-Z]\.\s*){1,3}(?=[A-Z][a-z])")
# Leaked first-author "Surname, I.[I.] [et al.] " glued to the title.
_LEAD_SURNAME_RE = re.compile(
    r"^\s*[A-Z\u00c0-\u024f][A-Za-z\u00c0-\u024f'\u2019.-]+,\s+"
    r"(?:[A-Z]\.\s*){1,4}(?:et\s+al\.?\s*)?(?=[A-Za-z0-9])")


def strip_leading_author(title):
    """Remove a leaked leading consortium/group name or orphan initials from a title."""
    if not title:
        return title
    t = _LEAD_CONSORTIUM_RE.sub("", title, count=1)
    t = _LEAD_SURNAME_RE.sub("", t, count=1)
    t = _LEAD_INITIAL_RE.sub("", t, count=1)
    return t.strip()


# ── Greek letter spelled as a word ↔ symbol (conservative, last-resort) ─────
_GREEK = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
          "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ",
          "lambda": "λ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
          "chi": "χ", "psi": "ψ", "omega": "ω", "mu": "μ", "nu": "ν", "pi": "π"}
_GREEK_RE = re.compile(r"\b(" + "|".join(_GREEK) + r")\b", re.IGNORECASE)


def greek_words_to_symbols(title):
    """Convert spelled-out Greek letter words to their symbols (beta→β, lambda→λ)."""
    if not title:
        return title
    return _GREEK_RE.sub(lambda m: _GREEK[m.group(0).lower()], title)


_MERGE_SUFFIX_RE = re.compile(
    r"(?<=[a-z]{3})(specific|associated|related|dependent|independent|mediated|induced"
    r"|derived|labell?ed|based|wide|scale|resolution|throughput|dimensional|coding"
    r"|binding|encoded|containing|deficient|positive|negative|driven|enriched|enhanced"
    r"|regulated|activated|targeted|dependant|density|level|type|like)\b", re.IGNORECASE)
_MERGE_PREFIX_RE = re.compile(
    r"\b(two|three|four|high|low|single|multi|multiple|non|pre|post|self|well|cross"
    r"|inter|intra|sub|super|over|under|whole|full|genome|cell|cancer|tissue|dye|long"
    r"|short|large|small|real|wild)(?=[a-z]{4})", re.IGNORECASE)


def demerge_words(title):
    """Yield title variants with a space inserted at common dropped-hyphen compound
    boundaries (dyelabeled -> dye labeled, highthroughput -> high throughput,
    twodimensional -> two dimensional). Only-if-it-matches keeps this safe."""
    if not title:
        return
    v1 = _MERGE_SUFFIX_RE.sub(lambda m: " " + m.group(1), title)
    v2 = _MERGE_PREFIX_RE.sub(lambda m: m.group(1) + " ", title)
    v3 = _MERGE_PREFIX_RE.sub(lambda m: m.group(1) + " ", v1)
    for v in (v1, v2, v3):
        if v != title:
            yield v


def title_repair_variants(title):
    """Yield distinct cleaned title variants (!= original) to retry exact matching,
    applied cumulatively: ligature-repair → +author-strip → +greek-symbols."""
    if not title:
        return
    seen = {title}
    lig = repair_pdf_ligatures(title)
    au = strip_leading_author(lig)
    gk = greek_words_to_symbols(au)
    variants = [lig, au, gk]
    for base in (title, lig, au):
        variants.extend(demerge_words(base))
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            yield v


# ── arXiv id → DOI ─────────────────────────────────────────────────────────
_ARXIV_NEW = re.compile(r'arxiv[:\s]\s*(\d{4}\.\d{4,5})(?:v\d+)?', re.IGNORECASE)
_ARXIV_OLD = re.compile(
    r'\b((?:cond-mat|hep-th|hep-ph|hep-ex|hep-lat|gr-qc|quant-ph|astro-ph|nucl-th'
    r'|nucl-ex|math-ph|physics|math|cs|nlin|q-bio)(?:\.[A-Za-z]{2})?/\d{7})(?:v\d+)?\b',
    re.IGNORECASE)


def arxiv_doi_from_text(text):
    """Return the 10.48550/arXiv.<id> DOI for an arXiv id in the text, else None."""
    if not text:
        return None
    m = _ARXIV_NEW.search(text) or _ARXIV_OLD.search(text)
    return ("10.48550/arxiv." + m.group(1).lower()) if m else None


# ── PMID / bioRxiv / medRxiv identifiers ───────────────────────────────────
_PMID_RE = re.compile(r'\bPMID:?\s*(\d{6,9})\b', re.IGNORECASE)
_PREPRINT_RE = re.compile(
    r'\b(?:bio|med)Rxiv\b[^0-9]{0,25}?(\d{4}\.\d{2}\.\d{2}\.\d{6,8}|\d{6,7})', re.IGNORECASE)


def pmid_from_text(text):
    """Return a PubMed ID string found in the reference text, else None (resolve via OpenAlex)."""
    if not text:
        return None
    m = _PMID_RE.search(text)
    return m.group(1) if m else None


def preprint_doi_from_text(text):
    """Return the bioRxiv/medRxiv DOI (10.1101/<id>) for a preprint id in the text, else None."""
    if not text:
        return None
    m = _PREPRINT_RE.search(text)
    return ("10.1101/" + m.group(1)) if m else None


# ── corrupted-DOI recovery ─────────────────────────────────────────────────
_DOI_TRAIL_RE = re.compile(r"\.(?:ac-?cessed|accessed|retrieved|available)\b.*$", re.IGNORECASE)


def clean_doi_variants(doi):
    """Yield cleaned candidates for a possibly-corrupted extracted DOI: a trailing
    reference-number (".183") or 'accessed' text appended, or a leading digit error
    ("110." -> "10."). Only well-formed 10.x/ DOIs are yielded; the caller accepts a
    variant only if it actually resolves, so cleaning cannot introduce false matches."""
    d = (doi or "").strip().lower()
    seen = set()
    for c in (d, _DOI_TRAIL_RE.sub("", d), re.sub(r"^1(10\.\d{4})", r"\1", d),
              re.sub(r"\.\d{1,4}$", "", d)):
        c = c.strip().rstrip(".")
        if c and c not in seen and re.match(r"^10\.\d{4,9}/", c):
            seen.add(c); yield c
