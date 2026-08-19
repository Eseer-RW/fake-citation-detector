#!/usr/bin/env python3
"""diag_authority.py — why does journal_authority.resolve() return nothing for some names?

resolve() is SHARED: venue_ids_for() calls it, and validate_metadata's journal check
calls it too. So a coverage hole here degrades BOTH matching and mismatch detection.

resolve() returns None in two very different situations, and telling them apart is the
whole point of this script:
    alias_rows == 0  -> the name is genuinely ABSENT from the authority
    alias_rows  > 1  -> the name is AMBIGUOUS and None is returned BY DESIGN
The fix differs completely: add data vs. disambiguate.
"""
import sys, os
sys.path.insert(0, "/space/rwang/fake-citation-detector/scripts")
os.chdir("/space/rwang/fake-citation-detector/scripts")
import journal_authority as JA

print("DB:", JA._DB_PATH)
print("size: %.1f MB" % (os.path.getsize(JA._DB_PATH) / 1e6))
con = JA._con()
print("tables:", [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()])
for t in ("alias", "journal", "acronym"):
    try:
        print("   %-8s rows=%d" % (t, con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]))
    except Exception as e:
        print("   %-8s ERR %s" % (t, e))

NAMES = [
    "Phys. Rev. D", "Physical Review D",
    "Phys. Rev. E", "Physical Review E",
    "Phys. Rev. C", "Physical Review C",
    "Phys. Rev. Lett", "Physical Review Letters",
    "Eur. Phys. J. A", "European Physical Journal A",
    "Comm. Math. Phys", "Communications in Mathematical Physics",
    "Annu. Rev. Nucl. Part. Sci", "Nuovo Cim", "Nucl. Phys",
    "Prog. Theor. Phys", "Int. J. Theor. Phys",
]

print("\n%-40s %-30s %6s %-12s %s" % ("input", "normalised", "alias", "verdict", "resolve()"))
absent = ambig = ok = 0
for nm in NAMES:
    k = JA._norm(nm)
    rows = con.execute("SELECT DISTINCT identity FROM alias WHERE name_norm=?", (k,)).fetchall()
    r = JA.resolve(nm)
    if r:
        verdict = "OK"; ok += 1
    elif len(rows) == 0:
        verdict = "ABSENT"; absent += 1
    else:
        verdict = "AMBIGUOUS(%d)" % len(rows); ambig += 1
    print("%-40s %-30s %6d %-12s %s" % (nm, k[:30], len(rows), verdict, r))

print("\nOK=%d  ABSENT=%d  AMBIGUOUS=%d" % (ok, absent, ambig))

# For the interesting failures, show what IS in the DB nearby.
print("\n--- what the authority DOES hold for 'physical review' ---")
try:
    rows = con.execute(
        "SELECT name_norm, identity FROM alias WHERE name_norm LIKE 'physical review%' "
        "ORDER BY name_norm LIMIT 25").fetchall()
    for a, b in rows:
        print("   %-42s %s" % (a, b))
    print("   (total matching 'physical review%%': %d)" % con.execute(
        "SELECT COUNT(*) FROM alias WHERE name_norm LIKE 'physical review%'").fetchone()[0])
except Exception as e:
    print("   query failed:", e)
