#!/usr/bin/env python3
"""
paper_and_field.py — two analyses the current data already supports.

1. PAPER-LEVEL RATE. The ref-weighted rate is dominated by individual papers: one
   682-reference survey supplied 61.5% of month 2411's unmatched total and moved that
   month 11pp. A paper-level rate (share of papers with >=1 unverifiable reference)
   gives every paper equal weight, so no single bibliography can dominate -- and it is
   the unit the literature reports ("1 in 277 papers", Lancet), so without it the
   comparison to published figures is apples-to-oranges.

   Reported on ALL refs and on DOI-BEARING refs only (the clean instrument).

2. FIELD STRATIFICATION. arXiv's composition shifted from physics-dominated toward
   CS/ML across this window, and citation conventions differ sharply by field
   (physics cites journal+volume+page with no title; CS cites arXiv preprints and
   conference papers that a works index matches poorly). If the pooled trend is field
   mix, within-field rates will be flatter than the pooled one.

   Field comes from the arXiv ID: pre-2007 IDs carry an explicit archive prefix
   (hep-th/9901001); the modern NNNN.NNNNN scheme does NOT encode it, so field is
   unavailable for this window and we say so rather than guessing.
"""
import json, sys, math, random, collections, argparse, re

ap = argparse.ArgumentParser()
ap.add_argument("refs")
ap.add_argument("--boot", type=int, default=2000)
a = ap.parse_args()
rng = random.Random(555)

# month -> paper -> [refs, unmatched, doi_refs, doi_unmatched]
P = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0, 0]))
ids = set()
for ln in open(a.refs):
    try:
        d = json.loads(ln)
    except Exception:
        continue
    mo, pid = d.get("month"), d.get("paper")
    if not mo or not pid:
        continue
    ids.add(pid)
    un = (not d.get("found")) and (not d.get("nonacademic"))
    v = P[mo][pid]
    v[0] += 1
    v[1] += 1 if un else 0
    if d.get("has_doi"):
        v[2] += 1
        v[3] += 1 if un else 0

months = sorted(P)
dec = lambda m: 2000 + int(m[:2]) + (int(m[2:]) - 1) / 12.0


def paper_rate(ms, doi_only=False):
    """share of papers with >=1 unverifiable reference"""
    tot = hit = 0
    for m in ms:
        for v in P[m].values():
            n, u = (v[2], v[3]) if doi_only else (v[0], v[1])
            if n <= 0:
                continue
            tot += 1
            hit += 1 if u > 0 else 0
    return hit, tot, (100.0 * hit / tot if tot else float("nan"))


def boot_paper(ms, B, doi_only=False):
    pool = []
    for m in ms:
        for v in P[m].values():
            n, u = (v[2], v[3]) if doi_only else (v[0], v[1])
            if n > 0:
                pool.append(1 if u > 0 else 0)
    if not pool:
        return []
    N = len(pool)
    return [100.0 * sum(pool[rng.randrange(N)] for _ in range(N)) / N for _ in range(B)]


print("papers: %d   months: %d\n" % (len(ids), len(months)))

for label, doi_only in (("ALL references", False), ("DOI-BEARING only", True)):
    print("PAPER-LEVEL RATE — %s   (share of papers with >=1 unverifiable ref)" % label)
    print("%-6s %8s %8s %9s   %s" % ("year", "papers", "hit", "rate", "95% CI"))
    by_year = collections.defaultdict(list)
    for m in months:
        by_year[2000 + int(m[:2])].append(m)
    for y in sorted(by_year):
        h, t, r = paper_rate(by_year[y], doi_only)
        b = sorted(boot_paper(by_year[y], 600, doi_only))
        ci = "[%.1f, %.1f]" % (b[int(.025 * len(b))], b[int(.975 * len(b))]) if b else "--"
        print("%-6d %8d %8d %8.1f%%   %s" % (y, t, h, r, ci))
    # pre/post
    pre = [m for m in months if dec(m) < 2023.0]
    post = [m for m in months if dec(m) >= 2023.0]
    _, _, r0 = paper_rate(pre, doi_only)
    _, _, r1 = paper_rate(post, doi_only)
    b0, b1 = boot_paper(pre, a.boot, doi_only), boot_paper(post, a.boot, doi_only)
    k = min(len(b0), len(b1))
    dd = sorted(y - x for x, y in zip(b0[:k], b1[:k]))
    mu = sum(dd) / len(dd)
    sd = math.sqrt(sum((v - mu) ** 2 for v in dd) / (len(dd) - 1))
    lo, hi = dd[int(.025 * len(dd))], dd[min(len(dd) - 1, int(.975 * len(dd)))]
    print("  pre %.1f%%  post %.1f%%  diff %+.2f pp  SE %.2f  CI [%+.2f,%+.2f]  %s\n"
          % (r0, r1, r1 - r0, sd, lo, hi,
             "SIGNIFICANT" if not (lo < 0 < hi) else "n.s."))

# ---- field ----
print("=" * 64)
print("FIELD STRATIFICATION")
old_style = [p for p in ids if re.match(r"^[a-zA-Z\-]+/\d", str(p))]
print("arXiv IDs carrying an explicit archive prefix (e.g. hep-th/9901001): %d of %d"
      % (len(old_style), len(ids)))
if not old_style:
    print("""
  NOT COMPUTABLE from this data. Every paper in the 2019-2026 window uses the modern
  NNNN.NNNNN identifier scheme, which does NOT encode the subject class -- that lives
  in the arXiv metadata (abs page / OAI / the Kaggle metadata dump), not in the ID.

  To do this properly, fetch primary_category per arXiv ID and join on `paper`. Until
  then the field-mix hypothesis stays untested; do not substitute a guess.""")
else:
    print("  (prefix-bearing IDs present -- per-field breakdown feasible for those)")
