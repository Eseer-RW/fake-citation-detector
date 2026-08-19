#!/usr/bin/env python3
"""
onset_test.py — does the DOI-bearing null survive a LATE-ONSET effect?

WHY. The earlier pre/post split (2019-22 vs 2023-26) averages over four post years.
Zhao's reported inflection is mid-2024, so if the effect starts then, that split is
~60% pre-effect and DILUTES the very signal the CI claimed to exclude. A null against
a 4-year average is NOT a null against a late-onset effect.

WHAT THIS DOES, on DOI-bearing refs only (the clean instrument, ~0.05pp precision):
  1. monthly series with cluster-bootstrap CIs, so a spike is visible
  2. pre/post at SEVERAL candidate onsets (2023-01, 2024-01, 2024-07, 2025-01)
  3. power at each onset -- a late onset means fewer post months, so less power;
     the honest report states power per onset rather than one global number
"""
import json, collections, math, random, argparse

ap = argparse.ArgumentParser()
ap.add_argument("refs")
ap.add_argument("--boot", type=int, default=3000)
a = ap.parse_args()
rng = random.Random(2024)

# paper -> [refs, unmatched], kept per month, DOI-bearing only
by_month = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
for ln in open(a.refs):
    try:
        d = json.loads(ln)
    except Exception:
        continue
    if not d.get("has_doi"):
        continue
    mo = str(d.get("month") or "")
    if len(mo) < 4:
        continue
    un = (not d.get("found")) and (not d.get("nonacademic"))
    p = by_month[mo][d.get("paper")]
    p[0] += 1
    p[1] += 1 if un else 0

months = sorted(by_month)
dec = lambda m: 2000 + int(m[:2]) + (int(m[2:]) - 1) / 12.0


def pooled(groups):
    """groups: list of paper->[n,u] dicts. Returns (u, n, rate%)."""
    u = n = 0
    for g in groups:
        for v in g.values():
            n += v[0]; u += v[1]

    return u, n, (100.0 * u / n if n else float("nan"))


def boot_rate(groups, B):
    """Cluster bootstrap over papers pooled across the given months."""
    papers = []
    for g in groups:
        papers.extend(g.values())
    if not papers:
        return []
    N = len(papers)
    out = []
    for _ in range(B):
        num = den = 0
        for _ in range(N):
            v = papers[rng.randrange(N)]
            den += v[0]; num += v[1]
        if den:
            out.append(100.0 * num / den)
    return out


print("DOI-bearing refs, %d months (%s..%s)\n" % (len(months), months[0], months[-1]))

# ---- 1. monthly series ----
print("MONTHLY (DOI-bearing unmatched rate, cluster-bootstrap 95%% CI)")
print("%-6s %8s %8s %9s   %s" % ("month", "refs", "unmatch", "rate", "95% CI"))
for m in months:
    u, n, r = pooled([by_month[m]])
    b = sorted(boot_rate([by_month[m]], 600))
    if b:
        lo, hi = b[int(.025 * len(b))], b[min(len(b) - 1, int(.975 * len(b)))]
        ci = "[%.3f, %.3f]" % (lo, hi)
    else:
        ci = "--"
    flag = ""
    print("%-6s %8d %8d %8.3f%%   %s%s" % (m, n, u, r, ci, flag))

# ---- 2 & 3. onset sweep ----
print("\nONSET SWEEP -- pre/post at each candidate LLM-effect onset")
print("%-9s %7s %7s %10s %10s %11s %9s %8s" % (
    "onset", "mo_pre", "mo_post", "rate_pre", "rate_post", "difference", "SE", "pw@.39"))

from math import erf
phi = lambda x: 0.5 * (1 + erf(x / math.sqrt(2)))

for onset, label in ((2023.0, "2023-01"), (2024.0, "2024-01"),
                     (2024.5, "2024-07"), (2025.0, "2025-01")):
    pre_m = [m for m in months if dec(m) < onset]
    post_m = [m for m in months if dec(m) >= onset]
    if len(pre_m) < 6 or len(post_m) < 3:
        print("%-9s   (insufficient months)" % label)
        continue
    _, _, r0 = pooled([by_month[m] for m in pre_m])
    _, _, r1 = pooled([by_month[m] for m in post_m])
    b0 = boot_rate([by_month[m] for m in pre_m], a.boot)
    b1 = boot_rate([by_month[m] for m in post_m], a.boot)
    k = min(len(b0), len(b1))
    diffs = sorted(y - x for x, y in zip(b0[:k], b1[:k]))
    mu = sum(diffs) / len(diffs)
    sd = math.sqrt(sum((x - mu) ** 2 for x in diffs) / (len(diffs) - 1))
    lo, hi = diffs[int(.025 * len(diffs))], diffs[min(len(diffs) - 1, int(.975 * len(diffs)))]
    z = 0.39 / sd if sd else float("inf")
    pw = phi(z - 1.96) + phi(-z - 1.96)
    excl = "excludes" if not (lo < 0.39 < hi) else "CONTAINS"
    print("%-9s %7d %7d %9.4f%% %9.4f%% %+10.4f %8.4f %7.0f%%   CI [%+.3f,%+.3f] %s +0.39" % (
        label, len(pre_m), len(post_m), r0, r1, r1 - r0, sd, 100 * pw, lo, hi, excl))

print("""
READING IT
  If every onset gives a difference near zero with a CI excluding +0.39pp, the null
  is robust to when the effect is assumed to start.
  If the latest onsets lose power (wide CI containing +0.39pp), then the null holds
  only for the earlier framing and the recent period is genuinely unresolved -- which
  must be stated, not glossed.
""")
