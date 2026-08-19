"""
oa_local.py — local OpenAlex index backend (oa_index.db, 486M rows) that replaces the slow
OpenAlex-Solr queries (metadata ~2-3s, title, DOI) with sub-ms SQLite lookups. Full works
coverage, so the curated/works hybrid+fallback is unnecessary here. Returns SolrResult /
dict shapes compatible with the existing pipeline. Thread-local read-only connections so the
32-64 worker threads don't contend on one handle.

Enabled when env OA_LOCAL_INDEX points at the db (default path below). Same exact-match
semantics as the Solr path: DOI exact, title_norm equality, (journal_norm,year,volume)+
page/author with a numFound==1 uniqueness guard. Journal names are resolved via the SAME
authority the Crossref biblio path uses, so lookups line up.
"""
import os, re, sqlite3, threading

_DB = os.environ.get("OA_LOCAL_INDEX", "/space/rwang/oa_index/oa_index.db")
_tls = threading.local()
_COLS = "oa_id,doi,journal_norm,year,volume,first_page,author1,title_norm"


def available():
    return bool(_DB) and os.path.exists(_DB)


def _c():
    c = getattr(_tls, "c", None)
    if c is None:
        c = sqlite3.connect("file:%s?mode=ro" % _DB, uri=True, check_same_thread=False)
        c.execute("PRAGMA query_only=ON")
        _tls.c = c
    return c


def _rec(row):
    oa_id, doi, jn, yr, vol, fp, a1, tn = row
    return {"id": oa_id, "openalex_id": oa_id, "doi": (doi or None), "title": tn,
            "publication_year": yr, "year": yr, "venue_name": jn, "journal": jn,
            "volume": vol, "first_page": fp, "author_names": ([a1] if a1 else [])}


def _surname(s):
    t = [x for x in re.split(r"[^A-Za-z]+", str(s or "")) if len(x) >= 3]
    return max(t, key=len).lower() if t else ""


def by_doi(doi):
    doi = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', (doi or '').strip(), flags=re.I).lower()
    if not doi:
        return None
    r = _c().execute("SELECT %s FROM oa WHERE doi=? LIMIT 1" % _COLS, (doi,)).fetchone()
    return _rec(r) if r else None


def by_title(title_norm, year=None):
    """Exact normalized-title match; year only disambiguates (prefer DOI-bearing, closest year)."""
    if not title_norm or len(title_norm.split()) < 4:
        return None
    rows = _c().execute("SELECT %s FROM oa WHERE title_norm=? LIMIT 25" % _COLS,
                        (title_norm,)).fetchall()
    if not rows:
        return None
    rows.sort(key=lambda r: (0 if r[1] else 1, abs((r[3] or 0) - (year or r[3] or 0))))
    return _rec(rows[0])


def by_metadata(journal_norms, year, volume, page=None, author=None):
    """(journal_norm,year,volume) + a page/author discriminator, accepted only on a unique
    single hit (mirrors oa_by_metadata's numFound==1 guard). journal_norms = iterable of
    already-authority-normalized journal keys."""
    if not (year and volume):
        return None
    try:
        yi = int(str(year)[:4]); vs = str(volume).strip()
    except Exception:
        return None
    cand = []
    seen = set()
    for jn in journal_norms:
        if not jn or jn in seen:
            continue
        seen.add(jn)
        cand += _c().execute(
            "SELECT %s FROM oa WHERE journal_norm=? AND year=? AND volume=?" % _COLS,
            (jn, yi, vs)).fetchall()
    if not cand:
        return None
    # discriminate: page first (unique), else author surname; accept only if exactly one
    if page:
        _pg = re.sub(r"\s*\([A-Za-z]{1,3}\)\s*$", "", str(page)).strip().lower()
        m = [r for r in cand if (r[5] or "").strip().lower() == _pg]
        if len(m) == 1:
            return _rec(m[0])
    if author:
        sn = _surname(author)
        if sn:
            m = [r for r in cand if _surname(r[6]) == sn]
            if len(m) == 1:
                return _rec(m[0])
    if len(cand) == 1:
        return _rec(cand[0])
    return None


if __name__ == "__main__":
    import time, sys
    sys.path.insert(0, "/space/rwang/fake-citation-detector/scripts")
    from journal_authority import _norm as jn, canonical_name
    print("db:", _DB, "available:", available())
    def t(lbl, fn):
        s = time.time(); r = fn(); print("  %-28s %5.2f ms -> %s" % (lbl, (time.time()-s)*1000, (r.get("id"), r.get("doi"), r.get("year")) if r else None))
    t("by_doi XGBoost", lambda: by_doi("10.48550/arxiv.1603.02754"))
    t("by_title attention", lambda: by_title("attention is all you need", 2017))
    # metadata: resolve a journal via authority variants
    norms = set(filter(None, [jn("Nucl. Phys. B"), jn(canonical_name("Nucl. Phys. B") or "")]))
    t("by_metadata NuclPhysB", lambda: by_metadata(norms, 1985, "250", author="Witten"))
