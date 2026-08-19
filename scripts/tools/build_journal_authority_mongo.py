"""
build_journal_authority_mongo.py — build the journal-name authority SQLite from the
boss's curated MongoDB `journal.journals` collection (multi-source synonyms:
OpenAlex + Crossref + NLM), instead of the OpenAlex-only S3 snapshot.

Keeps the fast local SQLite backend (journal_authority.py is unchanged); only the
source data improves. Normalization here MUST match journal_authority._norm().
"""
import sqlite3, re, sys, pathlib
from urllib.parse import quote_plus

OUT = pathlib.Path("/home/rwang/journal_authority/journal_authority.sqlite")
ENV = "/home/rwang/fake-citation-detector/.env"

# ── normalization: MUST be identical to journal_authority._norm/_acronym ──────
_STOP = {"of","the","and","for","in","on","at","to","a","an","&","de","la","le",
         "der","die","das","und","el","los","las"}
_PUNCT = re.compile(r"[^\w\s]", re.U)
_WS    = re.compile(r"\s+")

def norm(name):
    if not name: return ""
    s = name.lower().replace("&", " and ")
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    if s.startswith("the "): s = s[4:]
    return s

def acronym(name):
    if not name: return ""
    words = [w for w in _PUNCT.sub(" ", name.lower()).split() if w and w not in _STOP]
    if len(words) < 2: return ""
    return "".join(w[0] for w in words)

# ── connect ───────────────────────────────────────────────────────────────────
pw = None
for line in open(ENV):
    if line.strip().startswith("MONGO_JOURNAL_PASSWORD="):
        pw = line.strip().split("=", 1)[1].strip().strip('"').strip("'"); break
if not pw:
    print("MONGO_JOURNAL_PASSWORD not in .env"); sys.exit(1)
uri = f"mongodb://journal_dev_user:{quote_plus(pw)}@galaxy3:27017/journal?authSource=journal"

from pymongo import MongoClient
mdb = MongoClient(uri, serverSelectionTimeoutMS=8000)["journal"]

# ── build ─────────────────────────────────────────────────────────────────────
if OUT.exists(): OUT.unlink()
con = sqlite3.connect(OUT)
con.execute("PRAGMA journal_mode=OFF"); con.execute("PRAGMA synchronous=OFF")
con.execute("CREATE TABLE alias(name_norm TEXT, identity TEXT)")
con.execute("CREATE TABLE acronym(acr TEXT, identity TEXT)")
con.execute("CREATE TABLE journal(identity TEXT PRIMARY KEY, display_name TEXT, venue_id TEXT, issn_l TEXT)")

proj = {"_id":1,"issn_l":1,"display_name":1,"synonyms":1,"synonyms_detail":1,
        "abbreviated_title":1,"alternate_titles":1}
n_src = n_alias = n_acr = 0
alias_rows, acr_rows, jrows = [], [], []
for doc in mdb.journals.find({}, proj):
    vid = doc.get("_id")
    disp = doc.get("display_name") or ""
    if not vid or not disp: continue
    identity = doc.get("issn_l") or vid
    n_src += 1
    jrows.append((identity, disp, vid, doc.get("issn_l")))

    names = set()
    detail = doc.get("synonyms_detail") or []
    if detail:
        for d in detail:
            v = d.get("value") or d.get("norm")
            if v: names.add(v)
    for s in (doc.get("synonyms") or []):
        if s: names.add(s)
    names.add(disp)
    if doc.get("abbreviated_title"): names.add(doc["abbreviated_title"])
    for a in (doc.get("alternate_titles") or []):
        if a: names.add(a)

    seen = set()
    for nm in names:
        k = norm(nm)
        if k and k not in seen:
            seen.add(k); alias_rows.append((k, identity)); n_alias += 1
    acr = acronym(disp)
    if acr and len(acr) >= 2:
        acr_rows.append((acr, identity)); n_acr += 1

    if len(alias_rows) >= 50000:
        con.executemany("INSERT INTO alias VALUES(?,?)", alias_rows); alias_rows.clear()
        con.executemany("INSERT INTO acronym VALUES(?,?)", acr_rows); acr_rows.clear()
        con.executemany("INSERT OR IGNORE INTO journal VALUES(?,?,?,?)", jrows); jrows.clear()
        print(f"  ... {n_src:,} journals", flush=True)

if alias_rows: con.executemany("INSERT INTO alias VALUES(?,?)", alias_rows)
if acr_rows:   con.executemany("INSERT INTO acronym VALUES(?,?)", acr_rows)
if jrows:      con.executemany("INSERT OR IGNORE INTO journal VALUES(?,?,?,?)", jrows)
print("building indexes...", flush=True)
con.execute("CREATE INDEX idx_alias ON alias(name_norm)")
con.execute("CREATE INDEX idx_acr ON acronym(acr)")
con.commit(); con.close()
print(f"Done. journals={n_src:,} alias_rows={n_alias:,} acronym_rows={n_acr:,}", flush=True)
print(f"DB: {OUT}")
