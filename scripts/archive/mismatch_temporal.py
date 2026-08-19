#!/usr/bin/env python3
"""
mismatch_temporal.py — the OTHER hallucination channel, over time.

WHY THIS EXISTS. Condition D of the fabrication test showed that a fabricated title
attached to a REAL, resolvable DOI is counted FOUND -- it never enters not_found, so
every temporal analysis so far (and the +-0.05pp bound) is structurally blind to it.
It surfaces only as FOUND_MISMATCH. That is precisely the "real DOI, garbled metadata"
signature the project report names as a hallucination pattern.

So this runs the same treatment on the mismatch channel: cluster-bootstrap CIs over
papers, an onset sweep, and power.

DENOMINATOR. Mismatch is only defined for references that MATCHED, so the rate is
found_mismatch / found, where found = total - not_found. Using `total` would blend in
the not-found trend we already know is an extraction artifact.

DATA. v2 only. v1's found_mismatch is invalid -- the validate_metadata title-slot fix
landed after that sweep, and its bias is age-dependent (inflates older months ~2x).
"""
import json, sys, math, random, collections, argparse

ap = argparse.ArgumentParser()
ap.add_argument("papers")
ap.add_argument("--boot", type=int, default=3000)
a = ap.parse_args()
rng = random.Random(777)

# month -> paper -> [found, mismatched]
by_month = collections.defaultdict(dict)
skipped = 0
for ln in open(a.papers):
    try:
        d = json.loads(ln)
    except Exception:
        continue
    if d.get("error") or "total" not in d:
        skipped += 1
        continue
    tot = d.get("total") or 0
    nf = d.get("not_found") or 0
    found = tot - nf
    if found <= 0:
        continue
    by_month[d.get("month")][d.get("id")] = [found, d.get("found_mismatch") or 0]

months = sorted(by_month)
dec = lambda m: 2000 + int(m[:2]) + (int(m[2:]) - 1) / 12.0


def rate(ms):
    f = x = 0
    for m in ms:
        for v in by_month[m].values():
            f += v[0]; x += v[1]
    return x, f, (100.0 * x / f if f else float("nan"))


def boot(ms, B):
    papers = []
    for m in ms:
        papers.extend(by_month[m].values())
    if not papers:
        return []
    N = len(papers)
    out = []
    for _ in range(B):
        f = x = 0
        for _ in range(N):
            v = papers[rng.randrange(N)]
            f += v[0]; x += v[1]
        if f:
            out.append(100.0 * x / f)
    return out


print("papers skipped (errors): %d   months: %d (%s..%s)\n"
      % (skipped, len(months), months[0], months[-1]))

print("ANNUAL mismatch rate  (found_mismatch / found)")
print("%-6s %10s %10s %9s   %s" % ("year", "found", "mismatch", "rate", "95% CI"))
by_year = collections.defaultdict(list)
for m in months:
    by_year[2000 + int(m[:2])].append(m)
for y in sorted(by_year):
    x, f, r = rate(by_year[y])
    b = sorted(boot(by_year[y], 800))
    ci = "[%.2f, %.2f]" % (b[int(.025 * len(b))], b[int(.975 * len(b))]) if b else "--"
    print("%-6d %10d %10d %8.2f%%   %s" % (y, f, x, r, ci))

print("\nONSET SWEEP")
print("%-9s %7s %7s %9s %9s %11s %8s   %s" % (
    "onset", "mo_pre", "mo_post", "pre", "post", "difference", "SE", "95% CI"))
from math import erf
phi = lambda z: 0.5 * (1 + erf(z / math.sqrt(2)))
rows = []
for onset, label in ((2023.0, "2023-01"), (2024.0, "2024-01"),
                     (2024.5, "2024-07"), (2025.0, "2025-01")):
    pre = [m for m in months if dec(m) < onset]
    post = [m for m in months if dec(m) >= onset]
    if len(pre) < 6 or len(post) < 3:
        continue
    _, _, r0 = rate(pre)
    _, _, r1 = rate(post)
    b0, b1 = boot(pre, a.boot), boot(post, a.boot)
    k = min(len(b0), len(b1))
    dd = sorted(y - x for x, y in zip(b0[:k], b1[:k]))
    mu = sum(dd) / len(dd)
    sd = math.sqrt(sum((v - mu) ** 2 for v in dd) / (len(dd) - 1))
    lo, hi = dd[int(.025 * len(dd))], dd[min(len(dd) - 1, int(.975 * len(dd)))]
    sig = "SIGNIFICANT" if not (lo < 0 < hi) else "n.s."
    rows.append((label, r1 - r0, sd, lo, hi, sig))
    print("%-9s %7d %7d %8.2f%% %8.2f%% %+10.3f %8.3f   [%+.3f,%+.3f] %s" % (
        label, len(pre), len(post), r0, r1, r1 - r0, sd, lo, hi, sig))

print("\nPOWER (against the pooled-onset SE)")
if rows:
    sd0 = rows[0][2]
    for eff in (1.0, 0.5, 0.39, 0.2):
        z = eff / sd0
        print("   detect %+.2f pp : %.0f%%" % (eff, 100 * (phi(z - 1.96) + phi(-z - 1.96))))

print("""
READING IT
  Flat + tight CI  -> the null now covers BOTH fabrication routes (unresolvable
                      identifiers AND hijacked-but-valid ones). Much stronger claim.
  Post-2022 RISE   -> first positive signal in the project, in exactly the channel
                      theory predicts. Would need the mismatch-composition audit next
                      (year/author/volume/journal/title) before claiming hallucination,
                      since benign causes (preprint->published year drift) live here too.
""")
