#!/usr/bin/env python3
"""
ref_classify.py — per-reference classification layers for verify_refs.

Two additions to the detector, both validated against the GPTZero NeurIPS ground truth
(100 human-verified fabricated citations, 68 mappable to arXiv):

1) author_hijack(row) -> bool|None
   For FOUND refs: does the citation's claimed author appear in the matched work's
   author field? Catches "real title + fabricated authors" (Kingma/Ba-DeepFM class),
   which exact-match existence checks structurally miss (32% of real fabrications).
   Validated: recall 57%, FPR 6.9%. None = unjudgeable (initials-style citation,
   surname-first style, missing/mangled index author) — NOT evidence either way.

2) not_found_reason(row) -> str
   For NOT-FOUND refs: separates the benign causes from genuine fabrication candidates.
   Categories (from the v7 full-population + 5.5k-verification campaigns):
     no_title            untitled journal/vol/page ref — can't be an LLM-fab (those invent titles)
     parse_junk          GROBID scraped body text/captions/prompts/code as a "reference"
     non_article         real-but-not-a-paper: books, manuals, URLs, datasets, standards
     foreign_language    non-ASCII-heavy title — coverage gap, verified ~all real
     datacite_preprint   arXiv/RG DOI (10.48550/10.13140) — real preprint outside Crossref
     short_title         <4 content tokens — unmatchable fragment
     fab_candidate       well-formed, English, titled, resolves nowhere — the ONLY bucket
                         where fabrication can hide (still ~99% real-unindexed at verify)
"""
import re, os, sqlite3, threading, unicodedata

# --------------------------------------------------------------------------- utils
def _deacc(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()

_GENERIC = {"and", "the", "for", "with", "der", "van", "von", "della", "team", "robotics",
            "computational", "association", "findings", "press", "university", "institute",
            "group", "collaboration", "consortium"}

def _alltoks(s):
    return set(t for t in re.split(r"[^a-z]+", _deacc(s).lower()) if len(t) >= 3)

def _tn(s):
    s = re.sub(r"[^a-z0-9 ]+", " ", _deacc(s).lower())
    return re.sub(r"\s+", " ", s).strip()

# thread-local RO connections (verify runs under a ThreadPoolExecutor)
_LOCAL = threading.local()
_OA_DB = os.environ.get("OA_LOCAL_INDEX", "/space/rwang/oa_index/oa_index.db")
_CR_DB = os.environ.get("BIBLIO_DB", os.path.expanduser("~/crossref/biblio_index.db"))

def _conn(attr, path):
    c = getattr(_LOCAL, attr, None)
    if c is None:
        c = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        setattr(_LOCAL, attr, c)
    return c

def _oa_authors(matched_title):
    t = _tn(matched_title)
    if not t:
        return []
    try:
        cur = _conn("oa", _OA_DB).execute(
            "SELECT author1 FROM oa WHERE title_norm=? LIMIT 8", (t,))
        return [r[0] for r in cur.fetchall() if r[0]]
    except Exception:
        return []

def _cr_author(doi):
    if not doi:
        return None
    try:
        r = _conn("cr", _CR_DB).execute(
            "SELECT author1 FROM biblio WHERE doi=? LIMIT 1",
            (doi.lower().strip(),)).fetchone()
        return r[0] if r and r[0] else None
    except Exception:
        return None

# --------------------------------------------------------------- 1) author hijack
def author_hijack(raw, matched_title, ref_doi=None, has_doi=False):
    """True = citation authors disagree with every index record for the matched work.
    None = unjudgeable (style/index limits); False = an index author appears in raw."""
    raw = raw or ""
    if len(raw) < 40:
        return None
    rt = _alltoks(raw) - _GENERIC
    cands = []
    if matched_title:
        cands += _oa_authors(matched_title)
    if has_doi:
        ca = _cr_author(ref_doi)
        if ca:
            cands.append(ca)
    cands = [c for c in cands if _alltoks(c) - _GENERIC]
    if not cands:
        return None
    for c in cands:
        if (_alltoks(c) - _GENERIC) & rt:
            return False
    head = raw[:130]
    # initials-style ("F. Sciortino") — a given-name index token can never appear
    if re.match(r"\s*(?:[A-Z]\.[-\s]*){1,4}[A-Z]?[a-z]", head):
        return None
    # surname-first style ("Zhang Y, Li K") — same problem. Distinguish from Western
    # "Diederik P. Kingma": surname-first has a comma right after the initial, or a
    # second surname-initial pair; Western continues with a capitalized full word.
    if re.match(r"\s*[A-Z][a-z]+\s+[A-Z]\s*,", head) or \
       re.match(r"\s*[A-Z][a-z]+\s+[A-Z]\.?[,\s]+(?:and\s+)?[A-Z][a-z]+\s+[A-Z]\b[,.\s]", head):
        return None
    # need >=2 full given/surname words in the head that are NOT title words —
    # counts author names only ("Diederik P. Kingma and Jimmy Ba. DeepFM..." -> 3),
    # so initials-only heads stay unjudgeable without splitting on middle initials
    title_toks = _alltoks(matched_title or "")
    fullnames = [t for t in re.split(r"[^A-Za-z]+", head)
                 if len(t) >= 4 and t[0].isupper() and t.lower() not in title_toks]
    if len(fullnames) < 2:
        return None
    # don't treat short (accent-mangled) index tokens as disagreement evidence
    if all(max((len(t) for t in _alltoks(c) - _GENERIC), default=0) < 5 for c in cands):
        return None
    return True

# ----------------------------------------------------------- 2) not-found reason
_NONART = re.compile(
    r"https?://|www\.|github|gitlab|\bmanual\b|datasheet|white ?paper|documentation|"
    r"\bwiki\b|readme|user guide|toolkit|repository|\bdataset\b|R package|version \d|"
    r"technical report|tech\. rep|\bstandard\b|\bRFC\b|patent|\bthesis\b|dissertation|"
    r"\bhandbook\b|\bencyclopedia\b|lecture notes|market (research|report)|"
    r"play\.google|app store|\bbook\b|monograph|private communication|to appear|in press", re.I)
_JUNK = re.compile(
    r"[{}<>]=?|:-|::|\)\s*:|_[a-z]+\(|->|instruction:|\bdef \b|MUST |labels:|score:|"
    r"### |as an ai\b|let's first|carefully review|respond to the", re.I)
_JUNK_START = re.compile(
    r"^(proof|remark|definition|lemma|theorem|corollary|note|figure|table|eq|step|a:|q:|"
    r"based on|use a|share|respond|stay|although|notice that|given that|if a|start-pos|"
    r"non-empty|email address|affiliated|his main|her main|dr\.)", re.I)
_DATACITE = re.compile(r"10\.48550/|10\.13140/", re.I)

def _content_toks(s):
    s = re.sub(r"(\w)-\s*(\w)", r"\1\2", s or "")
    stop = {"the", "a", "an", "of", "for", "and", "or", "to", "in", "on", "with"}
    return [w for w in re.split(r"[^a-z0-9]+", s.lower()) if len(w) > 2 and w not in stop]

def not_found_reason(raw, ref_title, ref_doi=None, has_title=True):
    raw = raw or ""
    t = (ref_title or "").strip()
    if not has_title or not t:
        return "no_title"
    if ref_doi and _DATACITE.search(ref_doi):
        return "datacite_preprint"
    if _JUNK.search(t) or _JUNK.search(raw[:200]) or _JUNK_START.match(t):
        return "parse_junk"
    if _NONART.search(t) or _NONART.search(raw):
        return "non_article"
    letters = [c for c in t if c.isalpha()]
    if letters and sum(1 for c in letters if ord(c) > 127) / len(letters) > 0.15:
        return "foreign_language"
    if len(_content_toks(t)) < 4:
        return "short_title"
    return "fab_candidate"


# ------------------------------------------------------------- 3) title hijack
def title_hijack(ref_title, matched_title):
    """True if the citation's own title flatly disagrees with the matched work's title —
    the identifier-hijack signature (fabricated citation carrying a DOI/ID that resolves
    to an unrelated real work). Conservative: needs >=4 content tokens and <0.3 containment
    in BOTH directions (subset titles are fine)."""
    ct = _content_toks(ref_title or "")
    mt = _content_toks(matched_title or "")
    if len(ct) < 4 or not mt:
        return False
    cs, ms = set(ct), set(mt)
    fwd = len(cs & ms) / len(cs)
    rev = len(cs & ms) / len(ms)
    return fwd < 0.3 and rev < 0.3
