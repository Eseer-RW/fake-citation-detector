"""
title_normalize.py — canonical title key shared by the crs.crossref backfill
and the query side of CrossrefVerifier.by_title.

Exports:
    normalize_title_key(title) -> str

The SAME function must be applied when writing `title_norm` into crs.crossref
(scripts/citation_detector/backfill_crossref_title_norm.py) and when building
the lookup key from a user/manuscript title, or exact matching silently breaks.
Do not change the normalization without re-running the backfill.

Why stronger than lowercasing: Crossref titles carry embedded JATS/HTML markup
("The role of <i>TP53</i> ..."), HTML entities (sometimes double-escaped,
"&amp;amp;"), non-breaking spaces, curly quotes, and en-dashes. A plain-text
title from a manuscript reference never contains those, so a case-insensitive
comparison alone still misses. The key produced here is:

    1. first element when Crossref's `title` is a list
    2. HTML entities decoded repeatedly (max 3x) to unwind double-escaping
    3. tags <...> stripped
    4. NFKD-decomposed with combining marks dropped (naïve == naive)
    5. casefolded
    6. every run of non-word chars (punctuation, dashes, quotes, whitespace)
       collapsed to a single space; \\w is unicode-aware so CJK/Cyrillic titles
       survive
"""
import html
import re
import unicodedata
from typing import Any

_TAG_RE = re.compile(r"<[^>]*>")
_NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)


def normalize_title_key(title: Any) -> str:
    """Canonical matching key for a title; "" when there is no usable title."""
    if isinstance(title, list):
        title = title[0] if title else ""
    if not title:
        return ""
    text = str(title)
    for _ in range(3):  # unescape until stable: "&amp;amp;" -> "&amp;" -> "&"
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    text = _TAG_RE.sub(" ", text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    return _NON_WORD_RE.sub(" ", text).strip()
