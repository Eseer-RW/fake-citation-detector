#!/usr/bin/env python3
"""
mismatch_bymethod.py — the DOI-hijacking channel, cleanly.

BACKGROUND. A fabricated title attached to a real, resolvable DOI is counted FOUND,
so it never enters not_found and the +-0.05pp bound is blind to it. It surfaces only
as a mismatch. The earlier paper-level mismatch analysis was (a) confounded by
extraction quality and (b) underpowered (48% for +0.39pp).

THIS FIXES BOTH by restricting to what actually carries the signature:
  * method == "doi"  -> matched via DOI, so parse quality is NOT a confound
  * issue type "title" -> the hallucination signature. Year off-by-one/two is
    preprint->published drift and author-order issues are formatting; the project's
    own diag_mm2.out showed those dominate, so pooling all issue types buries the
    signal in benign noise.

Primary metric: share of DOI-MATCHED references whose TITLE disagrees, by year,
with cluster-bootstrap CIs over papers and an onset sweep.

Requires v3+ per-ref rows (fields `mismatch`, `issue_types`); v2 lacks them.
"""
import json, sys, math, random, collections, argparse, glob

ap = argparse.ArgumentParser()
ap.add_argument("refs", nargs="+", help="refs_*.jsonl (globs ok)")
ap.add_argument("--boot", type=int, default=3000)
a = ap.parse_args()
rng = random.Random(8080)

files = []
for pat in a.refs:
    files.extend(glob.glob(pat) or [pat])

# month -> paper -> [doi_matched, title_mismatch, any_mismatch]
P = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0]))
issue_counts = collections.defaultdict(collections.Counter)   # year -> issue -> n
have_field = False
n_rows = 0
for fp in files:
    for ln in open(fp):
        try:
            d = json.loads(ln)
        except Exception:
            continue
        n_rows += 1
        if "mismatch" not in d:
            continue
        have_field = True
        if not d.get("found") or d.get("method") != "doi":
            continue
        mo, pid = d.get("month"), d.get("paper")
        if not mo or not pid:
            continue
        yr = 2000 + int(str(mo)[:2])
        its = d.get("issue_types") or []
        v = P[mo][pid]
        v[0] += 1
        if d.get("mismatch"):
            v[2] += 1
            for t in its:
                issue_counts[yr][t] += 1
            if "title" in its:
                v[1] += 1

if not have_field:
    sys.exit("ERROR: no `mismatch` field in these rows — this needs v3+ output, not v2.")

months = sorted(P)
dec = lambda m: 2000 + int(m[:2]) + (int(m[2:]) - 1) / 12.0
print("rows read: %d   files: %d   months with DOI-matched refs: %d (%s..%s)\n"
      % (n_rows, len(files), len(months), months[0], months[-1]))


def rate(ms, idx):
    num = den = 0
    for m in ms:
        for v in P[m].values():
            den += v[0]; num += v[idx]
    return num, den, (100.0 * num / den if den else float("nan"))


def boot(ms, B, idx):
    pool = []
    for m in ms:
        pool.extend(P[m].values())
    if not pool:
        return []
    N = len(pool)
    out = []
    for _ in range(B):
        num = den = 0
        for _ in range(N):
            v = pool[rng.randrange(N)]
            den += v[0]; num += v[idx]
        if den:
            out.append(100.0 * num / den)
    return out


by_year = collections.defaultdict(list)
for m in months:
    by_year[2000 + int(m[:2])].append(m)

for label, idx in (("TITLE-disagreement (hallucination signature)", 1),
                   ("ANY mismatch", 2)):
    print("%s — among DOI-MATCHED refs" % label)
    print("%-6s %10s %9s %9s   %s" % ("year", "doi_refs", "flagged", "rate", "95% CI"))
    for y in sorted(by_year):
        num, den, r = rate(by_year[y], idx)
        b = sorted(boot(by_year[y], 600, idx))
        ci = "[%.3f, %.3f]" % (b[int(.025 * len(b))], b[int(.975 * len(b))]) if b else "--"
        print("%-6d %10d %9d %8.3f%%   %s" % (y, den, num, r, ci))
    print()

print("ONSET SWEEP — title-disagreement among DOI-matched refs")
print("%-9s %7s %7s %9s %9s %11s %8s   %s" % (
    "onset", "mo_pre", "mo_post", "pre", "post", "difference", "SE", "95% CI"))
from math import erf
phi = lambda z: 0.5 * (1 + erf(z / math.sqrt(2)))
first_sd = None
for onset, lab in ((2023.0, "2023-01"), (2024.0, "2024-01"),
                   (2024.5, "2024-07"), (2025.0, "2025-01")):
    pre = [m for m in months if dec(m) < onset]
    post = [m for m in months if dec(m) >= onset]
    if len(pre) < 3 or len(post) < 3:
        print("%-9s   (insufficient months on one side: %d/%d)" % (lab, len(pre), len(post)))
        continue
    _, _, r0 = rate(pre, 1)
    _, _, r1 = rate(post, 1)
    b0, b1 = boot(pre, a.boot, 1), boot(post, a.boot, 1)
    k = min(len(b0), len(b1))
    dd = sorted(y - x for x, y in zip(b0[:k], b1[:k]))
    mu = sum(dd) / len(dd)
    sd = math.sqrt(sum((v - mu) ** 2 for v in dd) / (len(dd) - 1))
    lo, hi = dd[int(.025 * len(dd))], dd[min(len(dd) - 1, int(.975 * len(dd)))]
    first_sd = first_sd or sd
    # DEGENERATE CASE: when the rate is uniformly zero every bootstrap resample is
    # also zero, so sd==0 and the CI collapses to [0,0] -- which then trivially
    # "excludes" zero and printed SIGNIFICANT. That is an artefact of zero variance,
    # not a finding. Report it as such and give the rule-of-three upper bound instead.
    if sd == 0 and r0 == 0 and r1 == 0:
        n_post = sum(v[0] for m in post for v in P[m].values())
        ub = 300.0 / n_post if n_post else float("nan")
        print("%-9s %7d %7d %8.3f%% %8.3f%% %+10.4f %8.4f   ZERO IN BOTH PERIODS "
              "-> no variance, not a significance test; rule-of-three upper bound "
              "on the post rate = %.4f%% (n=%d)" % (
                  lab, len(pre), len(post), r0, r1, r1 - r0, sd, ub, n_post))
    else:
        print("%-9s %7d %7d %8.3f%% %8.3f%% %+10.4f %8.4f   [%+.4f,%+.4f] %s" % (
            lab, len(pre), len(post), r0, r1, r1 - r0, sd, lo, hi,
            "SIGNIFICANT" if not (lo < 0 < hi) else "n.s."))

if first_sd:
    print("\nPOWER (title-disagreement channel)")
    for eff in (0.39, 0.20, 0.10, 0.05):
        z = eff / first_sd
        print("   detect %+.2f pp : %3.0f%%" % (eff, 100 * (phi(z - 1.96) + phi(-z - 1.96))))

print("\nISSUE-TYPE COMPOSITION of DOI-matched mismatches, by year")
alli = sorted({t for c in issue_counts.values() for t in c})
if alli:
    print("%-6s %s" % ("year", "  ".join("%9s" % t[:9] for t in alli)))
    for y in sorted(issue_counts):
        tot = sum(issue_counts[y].values()) or 1
        print("%-6d %s" % (y, "  ".join("%8.1f%%" % (100.0 * issue_counts[y][t] / tot)
                                        for t in alli)))
    print("\n(a RISING title share with year is the hallucination signature;")
    print(" a stable mix dominated by year/author is benign drift)")
