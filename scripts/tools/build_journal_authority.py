"""
build_journal_authority.py — build a local journal-name authority DB from the
OpenAlex sources snapshot, so journal names (canonical, ISO-4 abbreviation,
alternate titles, acronyms) resolve to a single journal identity (ISSN-L or
venue_id). Enables exact journal matching across shortened / alternate names.

Input : /home/rwang/journal_authority/raw/**/*.gz   (OpenAlex sources jsonl.gz)
Output: /home/rwang/journal_authority/journal_authority.sqlite

Tables:
    alias(name_norm TEXT, identity TEXT)   -- canonical + abbrev + alternate titles
    acronym(acr TEXT, identity TEXT)       -- generated initialisms (fallback only)
    journal(identity TEXT PRIMARY KEY, display_name TEXT, venue_id TEXT, issn_l TEXT)
"""
import glob, gzip, json, re, sqlite3, pathlib, sys

RAW  = "/home/rwang/journal_authority/raw"
OUT  = "/home/rwang/journal_authority/journal_authority.sqlite"

_STOP = {"of","the","and","for","in","on","at","to","a","an","&","de","la","le",
         "der","die","das","und","el","los","las"}
_PUNCT = re.compile(r"[^\w\s]", re.U)
_WS    = re.compile(r"\s+")

def norm(name: str) -> str:
    if not name:
        return ""
    s = name.lower().replace("&", " and ")
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    if s.startswith("the "):
        s = s[4:]
    return s

def acronym(name: str) -> str:
    """Initialism from significant words: 'Journal of the American Medical Assoc' -> 'jama'."""
    if not name:
        return ""
    words = [w for w in _PUNCT.sub(" ", name.lower()).split() if w and w not in _STOP]
    if len(words) < 2:
        return ""
    return "".join(w[0] for w in words)

def main():
    files = sorted(glob.glob(f"{RAW}/**/*.gz", recursive=True))
    if not files:
        print("No source files found", file=sys.stderr); sys.exit(1)
    print(f"Parsing {len(files)} source files...", flush=True)

    con = sqlite3.connect(OUT)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    con.execute("CREATE TABLE IF NOT EXISTS alias(name_norm TEXT, identity TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS acronym(acr TEXT, identity TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS journal(identity TEXT PRIMARY KEY, "
                "display_name TEXT, venue_id TEXT, issn_l TEXT)")

    n_src = n_alias = n_acr = 0
    alias_rows, acr_rows, jrows = [], [], []
    for fp in files:
        with gzip.open(fp, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    s = json.loads(line)
                except Exception:
                    continue
                stype = s.get("type")
                # Keep journals + conference series + repositories that carry a name
                vid = (s.get("id") or "").rsplit("/", 1)[-1]
                if not vid:
                    continue
                issn_l = s.get("issn_l")
                identity = issn_l or vid
                disp = s.get("display_name") or ""
                if not disp:
                    continue
                n_src += 1
                jrows.append((identity, disp, vid, issn_l))

                names = [disp]
                if s.get("abbreviated_title"):
                    names.append(s["abbreviated_title"])
                for alt in (s.get("alternate_titles") or []):
                    if alt:
                        names.append(alt)
                seen = set()
                for nm in names:
                    k = norm(nm)
                    if k and k not in seen:
                        seen.add(k)
                        alias_rows.append((k, identity)); n_alias += 1
                acr = acronym(disp)
                if acr and len(acr) >= 2:
                    acr_rows.append((acr, identity)); n_acr += 1

                if len(alias_rows) >= 50000:
                    con.executemany("INSERT INTO alias VALUES(?,?)", alias_rows); alias_rows.clear()
                    con.executemany("INSERT INTO acronym VALUES(?,?)", acr_rows); acr_rows.clear()
                    con.executemany("INSERT OR IGNORE INTO journal VALUES(?,?,?,?)", jrows); jrows.clear()

    if alias_rows: con.executemany("INSERT INTO alias VALUES(?,?)", alias_rows)
    if acr_rows:   con.executemany("INSERT INTO acronym VALUES(?,?)", acr_rows)
    if jrows:      con.executemany("INSERT OR IGNORE INTO journal VALUES(?,?,?,?)", jrows)

    print("Building indexes...", flush=True)
    con.execute("CREATE INDEX idx_alias ON alias(name_norm)")
    con.execute("CREATE INDEX idx_acr ON acronym(acr)")
    con.commit()
    print(f"Done. sources={n_src:,} alias_rows={n_alias:,} acronym_rows={n_acr:,}", flush=True)
    print(f"DB: {OUT}", flush=True)
    con.close()

if __name__ == "__main__":
    main()
