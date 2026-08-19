"""author_agreement.py — author-mismatch check for the partial-attribute-corruption
fabrication mode (a REAL paper cited with FABRICATED authors), which existence checks miss.

`author_mismatch(cited_first_author, resolved_doi)` -> True when the cited first author
appears NOWHERE among the resolved paper's authors (fabricated-author candidate). It matches
the cited surname against the FULL author list (all families + all given tokens) of the
resolved work, with unicode/diacritic + LaTeX normalization and initial/1-edit/prefix fuzz.

Validated 2026-08-16: 1.00% FP on 400 legit found-by-DOI refs; catches 16/16 partial-
corruption cases on GPTZero's NeurIPS-2025 set. FP floor is compound surnames / rare
transliteration -> keep as a flag feeding verification unless the base rate is high.

Author data (SHARED, env-configurable; SQLite over NFS -> reads shared read-only, writes a
per-user overflow so concurrent users never contend on the shared file):
  AUTHOR_CACHE_DB     shared read-only bulk cache  [default /space/rwang/author_cache.db]
  AUTHOR_CACHE_LOCAL  per-user writable overflow   [default ~/.author_cache_local.db]
On a cache miss it fetches from arXiv (preprints) or the Crossref API (published), falling
back to local biblio `author1`, and records the result in the per-user overflow.
"""
import os, re, sqlite3, time, unicodedata, requests

SHARED = os.environ.get("AUTHOR_CACHE_DB", "/space/rwang/author_cache.db")
LOCAL  = os.environ.get("AUTHOR_CACHE_LOCAL", os.path.expanduser("~/.author_cache_local.db"))
_UA = {"User-Agent": "insilicom-citation-audit (rwang@insilicom.com)"}
_shared = None; _local = None; _mem = {}

def _sh():
    global _shared
    if _shared is None:
        try: _shared = sqlite3.connect("file:%s?mode=ro" % SHARED, uri=True, timeout=10)
        except Exception: _shared = False
    return _shared or None

def _lo():
    global _local
    if _local is None:
        try:
            _local = sqlite3.connect(LOCAL, timeout=30)
            _local.execute("CREATE TABLE IF NOT EXISTS arxiv_auth(arxiv_id TEXT PRIMARY KEY, families TEXT, tokens TEXT)")
            _local.execute("CREATE TABLE IF NOT EXISTS xref_auth(doi TEXT PRIMARY KEY, families TEXT, tokens TEXT)")
            _local.commit()
        except Exception: _local = False
    return _local or None

def _strip_latex(s):
    s = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\[`'\"^~=.vcuH]\s*\{?([a-zA-Z])\}?", r"\1", s)
    s = re.sub(r"\\[a-zA-Z]+", "", s)
    return re.sub(r"[\\{}$]", "", s)
def _uni(s): return "".join(c for c in unicodedata.normalize("NFKD", str(s or "")) if not unicodedata.combining(c))
def _toks(s): return [t for t in re.sub(r"[^a-z]+", " ", _uni(_strip_latex(str(s or ""))).lower()).split() if len(t) > 1]
def _sur(n):
    t = _toks(n); return t[-1] if t else ""
def sur_match(a, b):
    if not a or not b: return True
    if a == b: return True
    if len(a) <= 2 or len(b) <= 2: return a[0] == b[0]
    if abs(len(a) - len(b)) <= 1 and sum(1 for x, y in zip(a, b) if x != y) <= 1: return True
    return a.startswith(b) or b.startswith(a)

def _get(table, key, col):
    if (table, key) in _mem: return _mem[(table, key)]
    for db in (_sh(), _lo()):
        if db is None: continue
        try:
            r = db.execute("SELECT families,tokens FROM %s WHERE %s=?" % (table, col), (key,)).fetchone()
            if r and r[0] is not None:
                v = (set(r[0].split()), set(r[1].split())); _mem[(table, key)] = v; return v
        except Exception: pass
    return None
def _put(table, key, col, fam, tk):
    _mem[(table, key)] = (set(fam), set(tk))
    db = _lo()
    if db is None: return
    try:
        db.execute("INSERT OR REPLACE INTO %s VALUES(?,?,?)" % table,
                   (key, " ".join(sorted(fam)), " ".join(sorted(tk)))); db.commit()
    except Exception: pass

def arxiv_authors(arxiv_id):
    aid = arxiv_id.split("v")[0]
    v = _get("arxiv_auth", aid, "arxiv_id")
    if v is not None: return v
    fam = set(); tk = set()
    try:
        x = requests.get("https://export.arxiv.org/api/query", params={"id_list": aid}, timeout=20, headers=_UA).text
        for nm in re.findall(r"<author>\s*<name>([^<]+)</name>", x):
            t = _toks(nm)
            if t: fam.add(t[-1]); tk.update(t)
        time.sleep(0.34)
    except Exception: pass
    _put("arxiv_auth", aid, "arxiv_id", fam, tk); return (fam, tk)

def crossref_authors(doi):
    d = str(doi or "").replace("https://doi.org/", "").strip().lower()
    if not d: return (set(), set())
    v = _get("xref_auth", d, "doi")
    if v is not None: return v
    fam = set(); tk = set()
    try:
        r = requests.get("https://api.crossref.org/works/" + d, params={"mailto": "rwang@insilicom.com"}, timeout=20, headers=_UA)
        if r.status_code == 200:
            for a in (r.json().get("message", {}).get("author", []) or []):
                f = _sur(a.get("family", ""))
                if f: fam.add(f); tk.add(f)
                for g in _toks(a.get("given", "")): tk.add(g)
        time.sleep(0.05)
    except Exception: pass
    if not fam:  # fallback: local biblio author1 (family only)
        try:
            import batch_verify_years as bvy
            b = sqlite3.connect("file:%s?mode=ro" % bvy.BIBLIO_DB, uri=True)
            rr = b.execute("SELECT author1 FROM biblio WHERE doi=? LIMIT 1", (d,)).fetchone()
            if rr and rr[0]:
                f = _sur(rr[0]); fam.add(f); tk.add(f)
        except Exception: pass
    _put("xref_auth", d, "doi", fam, tk); return (fam, tk)

def author_mismatch(cited_first_author, resolved_doi):
    """True = the cited first author appears NOWHERE among the resolved paper's authors
    (fabricated-author candidate). Conservative: False whenever it cannot check."""
    cl = _sur(cited_first_author)
    if not cl: return False
    m = re.search(r"10\.48550/arxiv\.(.+)$", str(resolved_doi or "").lower())
    fam, tk = arxiv_authors(m.group(1)) if m else crossref_authors(resolved_doi)
    if not fam and not tk: return False
    if any(sur_match(cl, f) for f in fam): return False
    if cl in tk: return False
    return True
