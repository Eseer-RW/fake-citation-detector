"""text_repair.py — repair PDF text-extraction artifacts in reference titles.

Old (Adobe/Times-encoded) PDFs export the fi/fl ligatures and some dashes as the wrong
glyphs, so GROBID hands us titles like `modi®ed` (modified), `in¯uenza` (influenza),
`®rst` (first), `¯uorescent` (fluorescent), `WorkshopÐMay` (Workshop—May). Those normalize
to garbage and fail exact title matching even though the paper is real. This restores the
intended characters BEFORE normalization.

Kept conservative — only the glyph substitutions actually observed in the corpus. The fi/fl
ligature mojibake is applied wherever the glyph is FOLLOWED by a letter (fi/fl always begin a
syllable), which catches mid-word (`modi®ed`) and word-initial (`®rst`) cases while leaving a
word-final trademark `®` ("Brand® ") or a stand-alone macron untouched.
"""
import re

# Proper Unicode ligature codepoints (NFKD also handles these; included for completeness).
_LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi",
    "ﬄ": "ffl", "ﬅ": "ft", "ﬆ": "st",
}
# Legacy-encoding mojibake: fi/fl ligatures mis-mapped to these glyphs.
_MOJIBAKE_LIG = {"®": "fi", "¯": "fl"}
# followed-by-a-letter guard: catches modi®ed AND ®rst / ¯uorescent, spares "Brand® ".
_LIG_RE = {bad: re.compile(re.escape(bad) + r"(?=[A-Za-z])") for bad in _MOJIBAKE_LIG}
# Dash mangled by the same encoding (appears between/around words, applied globally).
_MOJIBAKE_DASH = {"Ð": "-"}


def repair_pdf_ligatures(s):
    """Return s with observed PDF ligature/dash mojibake restored; idempotent."""
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


# arXiv identifiers in reference text -> the arXiv-assigned DOI (indexed by OpenAlex).
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
