#!/usr/bin/env python3
"""
stratify_citedyear.py — is the temporal trend an index-coverage artifact?

MECHANISM UNDER TEST. A snapshot-vs-Solr audit found the OpenAlex Solr index is
missing 60-83% of pre-2000 works and 0% of post-2000 works -- a hard cliff at 2000.
So ANY reference to a pre-2000 paper fails verification for reasons unrelated to
fabrication. If the share of pre-2000 references falls as citing-year advances
(authors citing more recent work), the pooled unmatched rate MUST fall too --
manufacturing exactly the -0.94pp/yr "pre-LLM decline" the headline result is
measured against.

THE TEST. Stratify by CITED year, not citing year:
  (a) does the pre-2000-cited share decline across citing years?
  (b) within a fixed cited-year band, is the unmatched rate flat over citing years?
If (a) yes and (b) yes, the pooled trend is composition, not behaviour.
"""
import json, sys, collections, math

PATH = sys.argv[1] if len(sys.argv) > 1 else "refs_1901_2606_k100.jsonl"

BANDS = [(0, 1999, "pre-2000"), (2000, 2009, "2000s"),
         (2010, 2019, "2010s"), (2020, 2100, "2020s")]


def band(y):
    if not isinstance(y, int):
        return None
    for lo, hi, name in BANDS:
        if lo <= y <= hi:
            return name
    return None


# citing_year -> band -> [refs, unmatched]
agg = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
noyear = collections.defaultdict(lambda: [0, 0])

n = 0
for ln in open(PATH):
    try:
        d = json.loads(ln)
    except Exception:
        continue
    mo = d.get("month")
    if not mo:
        continue
    cy = 2000 + int(str(mo)[:2])
    n += 1
    # numerator mirrors not_found_academic: unmatched AND not heuristically non-academic
    un = (not d.get("found")) and (not d.get("nonacademic"))
    b = band(d.get("cited_year"))
    if b is None:
        noyear[cy][0] += 1
        noyear[cy][1] += 1 if un else 0
        continue
    agg[cy][b][0] += 1
    agg[cy][b][1] += 1 if un else 0

years = sorted(agg)
names = [nm for _, _, nm in BANDS]
print("references: %d   citing years: %s\n" % (n, "-".join(map(str, (years[0], years[-1])))))

print("(a) SHARE of each citing year's references that point at each cited-year band")
print("%-6s %8s   %s   %10s" % ("citing", "n_refs", "  ".join("%9s" % b for b in names), "no_year"))
for cy in years:
    tot = sum(agg[cy][b][0] for b in names) + noyear[cy][0]
    cells = ["%8.1f%%" % (100.0 * agg[cy][b][0] / tot if tot else 0) for b in names]
    print("%-6d %8d   %s   %9.1f%%" % (cy, tot, "  ".join(cells),
                                       100.0 * noyear[cy][0] / tot if tot else 0))

print("\n(b) UNMATCHED RATE WITHIN each cited-year band (flat => pooled trend is composition)")
print("%-6s   %s   %10s" % ("citing", "  ".join("%9s" % b for b in names), "no_year"))
for cy in years:
    cells = []
    for b in names:
        r, u = agg[cy][b]
        cells.append("%8.1f%%" % (100.0 * u / r) if r >= 30 else "       --")
    r0, u0 = noyear[cy]
    print("%-6d   %s   %9s" % (cy, "  ".join(cells),
                               ("%.1f%%" % (100.0 * u0 / r0)) if r0 >= 30 else "--"))

print("\n(c) POOLED vs COMPOSITION-HELD-CONSTANT")
# reweight every citing year to the OVERALL band mix -> removes composition shift
tot_band = {b: sum(agg[cy][b][0] for cy in years) for b in names}
tot_band["no_year"] = sum(noyear[cy][0] for cy in years)
grand = sum(tot_band.values())
wts = {k: v / grand for k, v in tot_band.items() if grand}

print("%-6s %10s %10s   %s" % ("citing", "pooled", "adjusted", "difference"))
pooled_series, adj_series = [], []
for cy in years:
    tot = sum(agg[cy][b][0] for b in names) + noyear[cy][0]
    un = sum(agg[cy][b][1] for b in names) + noyear[cy][1]
    pooled = 100.0 * un / tot if tot else float("nan")
    adj = 0.0
    ok = True
    for b in names:
        r, u = agg[cy][b]
        if r < 30:
            ok = False
            break
        adj += wts[b] * (u / r)
    r0, u0 = noyear[cy]
    if ok and r0 >= 30:
        adj += wts["no_year"] * (u0 / r0)
        adj *= 100.0
    else:
        adj = float("nan")
    pooled_series.append((cy, pooled))
    adj_series.append((cy, adj))
    print("%-6d %9.2f%% %9s   %s" % (
        cy, pooled, ("%.2f%%" % adj) if adj == adj else "--",
        ("%+.2f pp" % (adj - pooled)) if adj == adj else ""))


def ols(pts):
    pts = [(x, y) for x, y in pts if y == y]
    if len(pts) < 3:
        return float("nan")
    n_ = len(pts)
    mx = sum(x for x, _ in pts) / n_
    my = sum(y for _, y in pts) / n_
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    sxy = sum((x - mx) * (y - my) for x, y in pts)
    return sxy / sxx if sxx else float("nan")


sp, sa = ols(pooled_series), ols(adj_series)
print("\nTREND  pooled   : %+.3f pp/yr" % sp)
print("TREND  adjusted : %+.3f pp/yr" % sa)
if sp == sp and sa == sa and sp != 0:
    print("\n=> holding cited-year composition constant removes %.0f%% of the pooled trend."
          % (100.0 * (1 - sa / sp)))
