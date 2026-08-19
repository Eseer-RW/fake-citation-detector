#!/usr/bin/env python3
"""
refquality.py — did the INSTRUMENT get better over time?

The unmatched rate declines within every cited-year band, so it is not vintage
composition. The remaining explanation is that newer citing papers carry
better-quality references (more DOIs, cleaner born-digital PDFs), which makes
verification succeed more often regardless of what is cited.

That matters because it runs OPPOSITE to hallucination: an improving instrument
pushes the measured rate DOWN and would MASK a real increase rather than fake one.

Three diagnostics, all from per-ref rows, no new I/O:
  1. share of references carrying a parsed DOI, by citing year
  2. share carrying a parsed title, by citing year
  3. match-method mix by citing year (doi vs title_exact vs meta vs not_found)

If (1) rises while title-based matching falls, the instrument improved.
Also reports the unmatched rate restricted to DOI-bearing refs -- a subset whose
verifiability barely depends on parse quality, so a trend THERE is harder to
explain away as an instrument effect.
"""
import json, sys, collections

PATH = sys.argv[1] if len(sys.argv) > 1 else "refs_1901_2606_k100.jsonl"

per_year = collections.defaultdict(lambda: collections.Counter())
method_by_year = collections.defaultdict(lambda: collections.Counter())

for ln in open(PATH):
    try:
        d = json.loads(ln)
    except Exception:
        continue
    mo = d.get("month")
    if not mo:
        continue
    cy = 2000 + int(str(mo)[:2])
    s = per_year[cy]
    s["n"] += 1
    if d.get("has_doi"):
        s["doi"] += 1
    if d.get("has_title"):
        s["title"] += 1
    if not d.get("has_doi") and not d.get("has_title"):
        s["neither"] += 1
    un = (not d.get("found")) and (not d.get("nonacademic"))
    if un:
        s["unmatched"] += 1
    # DOI-bearing subset: verifiability is far less parse-dependent here
    if d.get("has_doi"):
        s["doi_n"] += 1
        if un:
            s["doi_unmatched"] += 1
    method_by_year[cy][d.get("method") or "?"] += 1

years = sorted(per_year)

print("1) REFERENCE QUALITY by citing year")
print("%-6s %8s %10s %10s %10s" % ("year", "n_refs", "has_DOI", "has_title", "neither"))
for y in years:
    s = per_year[y]
    n = s["n"]
    print("%-6d %8d %9.1f%% %9.1f%% %9.1f%%" % (
        y, n, 100.0 * s["doi"] / n, 100.0 * s["title"] / n, 100.0 * s["neither"] / n))

print("\n2) UNMATCHED RATE: all refs vs DOI-BEARING refs only")
print("%-6s %12s %14s %12s" % ("year", "all_refs", "doi_bearing", "difference"))
allr, doir = [], []
for y in years:
    s = per_year[y]
    a = 100.0 * s["unmatched"] / s["n"] if s["n"] else float("nan")
    dn = s["doi_n"]
    dd = 100.0 * s["doi_unmatched"] / dn if dn else float("nan")
    allr.append((y, a)); doir.append((y, dd))
    print("%-6d %11.2f%% %13.2f%% %11s" % (
        y, a, dd, ("%+.2f pp" % (dd - a)) if dd == dd else "--"))

print("\n3) MATCH-METHOD MIX by citing year (share of all refs)")
meths = [m for m, _ in sum(method_by_year.values(), collections.Counter()).most_common(6)]
print("%-6s %s" % ("year", "  ".join("%14s" % m[:14] for m in meths)))
for y in years:
    c = method_by_year[y]
    tot = sum(c.values())
    print("%-6d %s" % (y, "  ".join("%13.1f%%" % (100.0 * c[m] / tot if tot else 0)
                                    for m in meths)))


def ols(pts):
    pts = [(x, y) for x, y in pts if y == y]
    if len(pts) < 3:
        return float("nan")
    n = len(pts)
    mx = sum(x for x, _ in pts) / n
    my = sum(y for _, y in pts) / n
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    sxy = sum((x - mx) * (y - my) for x, y in pts)
    return sxy / sxx if sxx else float("nan")


doi_share = [(y, 100.0 * per_year[y]["doi"] / per_year[y]["n"]) for y in years]
print("\nTRENDS")
print("  has_DOI share          : %+.3f pp/yr" % ols(doi_share))
print("  unmatched, all refs    : %+.3f pp/yr" % ols(allr))
print("  unmatched, DOI-bearing : %+.3f pp/yr" % ols(doir))
print("""
READING IT
  DOI share RISING + unmatched falling  -> instrument improved (masks hallucination)
  DOI-bearing trend ~flat               -> the decline is mostly a parse/quality effect
  DOI-bearing trend still falling       -> something beyond parse quality is moving
""")
