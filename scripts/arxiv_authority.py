"""arxiv_authority.py — authoritative arXiv id->title, cached in SQLite, backed by the
arXiv API, with an optional bulk seed from the arXiv metadata snapshot.

WHY: our OpenAlex index has WRONG or MISSING titles for arXiv DataCite DOIs
(10.48550/arXiv.<id>) — misattributed to a different work, or absent where a published
version exists. arXiv itself is authoritative for arXiv titles, so DOI/title resolution
for arXiv DOIs must consult this authority and override OpenAlex.

Usage:
  title_by_id('2402.03300')       -> 'DeepSeekMath: Pushing the Limits ...'  (cached; API on miss)
  id_by_title_norm(norm)          -> ['2402.03300', ...]  (title-path recovery)
  bulk_seed_from_snapshot(path)   -> seed all rows from arxiv-metadata-oai-snapshot.json
"""
import os, re, time, sqlite3, threading, requests

DB = os.environ.get("ARXIV_TITLES_DB", "/space/rwang/arxiv_titles.db")
ARXIV_API = "https://export.arxiv.org/api/query"
_UA = {"User-Agent": "insilicom-citation-audit (rwang@insilicom.com)"}
_lock = threading.Lock()
_conn = None

def _db():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB, check_same_thread=False, timeout=30)
        _conn.execute("CREATE TABLE IF NOT EXISTS arxiv("
                      "arxiv_id TEXT PRIMARY KEY, title TEXT, title_norm TEXT, year INTEGER, fetched INTEGER)")
        _conn.execute("CREATE INDEX IF NOT EXISTS ix_tn ON arxiv(title_norm)")
        _conn.commit()
    return _conn

def _norm(s):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split())

def _strip_ver(aid):
    return re.sub(r"v\d+$", "", (aid or "").strip())

def _store(aid, title, year):
    with _lock:
        c = _db()
        c.execute("INSERT OR REPLACE INTO arxiv VALUES(?,?,?,?,?)",
                  (aid, title, _norm(title), year, int(time.time())))
        c.commit()

def _fetch_api(ids):
    out = {}
    ids = [i for i in ids if i]
    if not ids:
        return out
    for att in range(3):
        try:
            r = requests.get(ARXIV_API, params={"id_list": ",".join(ids), "max_results": len(ids)},
                             headers=_UA, timeout=30)
            if r.status_code != 200:
                time.sleep(2 + att); continue
            for ent in re.findall(r"<entry>(.*?)</entry>", r.text, re.S):
                mid = re.search(r"<id>[^<]*?abs/([^<\s]+)", ent)
                mt = re.search(r"<title>(.*?)</title>", ent, re.S)
                my = re.search(r"<published>(\d{4})", ent)
                if mid and mt:
                    aid = _strip_ver(mid.group(1))
                    title = " ".join(mt.group(1).split())
                    if title and title.lower() != "error":
                        out[aid] = title
                        _store(aid, title, int(my.group(1)) if my else None)
            time.sleep(0.4)  # arXiv API politeness
            break
        except Exception:
            time.sleep(1)
    return out

def title_by_id(arxiv_id):
    """Authoritative arXiv title for an id (version-agnostic). Cache -> LOCAL OpenAlex
    index (doi 10.48550/arxiv.<id>; normalized title, fine for token comparisons) ->
    live API only if ARXIV_AUTHORITY_API=1. The API path serialized whole v8 shards:
    arXiv throttles hard, and at workers=1 every cache-miss cost seconds-to-minutes."""
    aid = _strip_ver(arxiv_id)
    if not aid:
        return None
    try:
        r = _db().execute("SELECT title FROM arxiv WHERE arxiv_id=?", (aid,)).fetchone()
        if r:
            return r[0] or None
    except Exception:
        pass
    try:
        import oa_local
        if oa_local.available():
            rec = oa_local.by_doi("10.48550/arxiv." + aid)
            t = (rec or {}).get("title") or (rec or {}).get("title_norm")
            if t:
                _store(aid, t, None)
                return t
    except Exception:
        pass
    if os.environ.get("ARXIV_AUTHORITY_API") == "1":
        return _fetch_api([aid]).get(aid)
    return None

def id_by_title_norm(title):
    try:
        rows = _db().execute("SELECT arxiv_id FROM arxiv WHERE title_norm=?", (_norm(title),)).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []

def bulk_seed_from_snapshot(path):
    """Seed from arxiv-metadata-oai-snapshot.json (one JSON object per line)."""
    import json
    c = _db(); n = 0
    with open(path) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            aid = _strip_ver((d.get("id") or "").strip())
            title = " ".join((d.get("title") or "").split())
            if not aid or not title:
                continue
            yr = None
            ud = d.get("update_date") or ""
            m = re.match(r"(\d{4})", ud)
            if m:
                yr = int(m.group(1))
            c.execute("INSERT OR REPLACE INTO arxiv VALUES(?,?,?,?,?)",
                      (aid, title, _norm(title), yr, int(time.time())))
            n += 1
            if n % 100000 == 0:
                c.commit()
    c.commit()
    return n
