#!/usr/bin/env python3
"""
analyze_power.py — cluster-robust rates, confidence intervals, and power for the
arXiv temporal sweep.

WHY THIS EXISTS
    The sweep reports point estimates with no error bars. References are clustered
    within papers, so treating them as independent Bernoulli draws understates the
    standard error substantially. Every CI here is produced by resampling PAPERS
    (the true sampling unit), not references.

WHAT IT PRODUCES
    1. Reference-level unmatched rate per month, with cluster-bootstrap CI.
    2. Paper-level rate: share of papers with >=1 unverifiable reference. This is
       the unit Zhao and the Lancet report ("1 in 277 papers"), so without it we
       are comparing incommensurable numbers.
    3. Design effect: observed variance / binomial variance. Tells you how badly
       naive CIs would mislead.
    4. Denominator cleaning: same rates excluding heuristically non-academic refs.
    5. Power: given the measured cluster-robust SE, the K (papers/month) needed to
       detect a target effect at 80% power.

Usage:
    python3 analyze_power.py <refs_jsonl> [--effect 0.39] [--boot 2000]
"""
import argparse, collections, json, math, random, sys


def load(path):
    """month -> paper -> list of ref dicts."""
    by = collections.defaultdict(lambda: collections.defaultdict(list))
    n = 0
    with open(path) as f:
        for ln in f:
            try:
                d = json.loads(ln)
            except Exception:
                continue
            mo, pid = d.get("month"), d.get("paper")
            if mo is None or pid is None:
                continue
            by[mo][pid].append(d)
            n += 1
    return by, n


def unmatched(r, clean):
    """Is this reference counted in the numerator?

    clean=False -> every unmatched reference (the sweep's not_found_academic uses
    the heuristic filter, so we mirror it by excluding flagged non-academic refs).
    clean=True  -> additionally drop refs with no usable title AND no DOI, which
    cannot be verified even in principle and only dilute the denominator.
    """
    if r.get("found"):
        return False
    if r.get("nonacademic"):
        return False
    return True


def in_denominator(r, clean):
    if not clean:
        return True
    # cleaned denominator: a reference must be verifiable in principle
    return bool(r.get("has_doi") or r.get("has_title"))


def month_stats(papers, clean=False):
    """Return (ref_rate, paper_rate, n_refs, n_papers, per_paper_tuples)."""
    tup = []
    for pid, refs in papers.items():
        den = [r for r in refs if in_denominator(r, clean)]
        num = [r for r in den if unmatched(r, clean)]
        tup.append((len(num), len(den)))
    n_refs = sum(d for _, d in tup)
    n_num = sum(x for x, _ in tup)
    n_papers = len(tup)
    ref_rate = (n_num / n_refs) if n_refs else float("nan")
    paper_rate = (sum(1 for x, d in tup if d and x > 0) / n_papers) if n_papers else float("nan")
    return ref_rate, paper_rate, n_refs, n_papers, tup


def cluster_boot(tup, B, rng, kind="ref"):
    """Bootstrap over PAPERS. Returns (lo, hi, sd) for the chosen estimator."""
    if not tup:
        return (float("nan"),) * 3
    n = len(tup)
    vals = []
    for _ in range(B):
        samp = [tup[rng.randrange(n)] for _ in range(n)]
        if kind == "ref":
            den = sum(d for _, d in samp)
            if not den:
                continue
            vals.append(sum(x for x, _ in samp) / den)
        else:
            vals.append(sum(1 for x, d in samp if d and x > 0) / n)
    if not vals:
        return (float("nan"),) * 3
    vals.sort()
    lo = vals[int(0.025 * len(vals))]
    hi = vals[min(len(vals) - 1, int(0.975 * len(vals)))]
    m = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / max(1, len(vals) - 1))
    return lo, hi, sd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("refs")
    ap.add_argument("--effect", type=float, default=0.39,
                    help="target effect in percentage points (Zhao arXiv = 0.39)")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--split", type=float, default=2023.0)
    a = ap.parse_args()
    rng = random.Random(12345)

    by, n_rows = load(a.refs)
    if not by:
        print("no per-ref rows found in", a.refs); return
    months = sorted(by)
    print("per-ref rows: %d   months: %d (%s..%s)\n" % (n_rows, len(months), months[0], months[-1]))

    print("%-6s %8s %8s %-22s %-22s %8s" %
          ("month", "papers", "refs", "ref-level rate [95% CI]",
           "paper-level rate [95% CI]", "design"))
    rows = []
    for mo in months:
        rr, pr, nref, npap, tup = month_stats(by[mo])
        rlo, rhi, rsd = cluster_boot(tup, a.boot, rng, "ref")
        plo, phi, _ = cluster_boot(tup, a.boot, rng, "paper")
        # design effect: cluster-bootstrap variance vs binomial variance
        binom_sd = math.sqrt(rr * (1 - rr) / nref) if nref and 0 < rr < 1 else float("nan")
        deff = (rsd / binom_sd) ** 2 if binom_sd and binom_sd == binom_sd and binom_sd > 0 else float("nan")
        rows.append((mo, rr, rsd, pr, nref, npap, deff))
        print("%-6s %8d %8d %6.2f%% [%5.2f,%5.2f]   %6.2f%% [%5.2f,%5.2f]   %6.1fx" %
              (mo, npap, nref, rr * 100, rlo * 100, rhi * 100,
               pr * 100, plo * 100, phi * 100, deff))

    # ---- pooled design effect ----
    deffs = [d for *_, d in rows if d == d]
    med_deff = sorted(deffs)[len(deffs) // 2] if deffs else float("nan")
    mean_sd = sum(r[2] for r in rows) / len(rows)
    print("\nmedian within-month design effect: %.1fx" % med_deff)
    print("  -> naive binomial CIs are ~%.1fx too narrow" % math.sqrt(med_deff))
    print("mean cluster-robust SE of a monthly rate: %.3f pp" % (mean_sd * 100))

    # ---- between-month excess variance ----
    if len(rows) > 2:
        rs = [r[1] for r in rows]
        m = sum(rs) / len(rs)
        obs_sd = math.sqrt(sum((x - m) ** 2 for x in rs) / (len(rs) - 1))
        print("observed between-month SD: %.3f pp (mean rate %.2f%%)" % (obs_sd * 100, m * 100))
        print("  within-month sampling alone would give ~%.3f pp" % (mean_sd * 100))
        if mean_sd > 0:
            print("  -> excess between-month variance: %.1fx (real month-to-month drift)"
                  % ((obs_sd / mean_sd) ** 2))

    # ---- power ----
    pre = [r for r in rows if (2000 + int(r[0][:2]) + (int(r[0][2:]) - 1) / 12.0) < a.split]
    post = [r for r in rows if (2000 + int(r[0][:2]) + (int(r[0][2:]) - 1) / 12.0) >= a.split]
    print("\n--- POWER for a %.2f pp effect ---" % a.effect)
    if len(rows) > 2:
        rs = [r[1] for r in rows]
        m = sum(rs) / len(rs)
        obs_sd = math.sqrt(sum((x - m) ** 2 for x in rs) / (len(rs) - 1)) * 100
        npre, npost = len(pre), len(post)
        print("months pre/post split %.1f: %d / %d" % (a.split, npre, npost))
        if npre == 0 or npost == 0:
            # Partial data: one side of the split is empty, so an observed
            # difference is undefined. Project onto the design the full sweep
            # will have (1901-2212 = 48 pre, 2301-2606 = 42 post) using the
            # between-month SD measured so far.
            npre, npost = 48, 42
            print("  (one side empty -- projecting onto the full sweep's 48/42 design)")
        se_diff = obs_sd * math.sqrt(1 / npre + 1 / npost)
        print("SE of pre-vs-post difference: %.3f pp" % se_diff)
        if se_diff > 0:
            z = a.effect / se_diff
            # two-sided alpha=0.05 power via normal approx
            from math import erf, sqrt as _s
            phi = lambda x: 0.5 * (1 + erf(x / _s(2)))
            power = phi(z - 1.96) + phi(-z - 1.96)
            print("power at alpha=0.05 (two-sided): %.0f%%" % (power * 100))
            need_se = a.effect / 2.80          # z for 80% power, two-sided
            factor = (se_diff / need_se) ** 2
            kcur = sum(r[5] for r in rows) / len(rows)
            print("SE needed for 80%% power: %.3f pp  -> %.1fx more data" % (need_se, factor))
            print("  i.e. K ~= %.0f papers/month (currently %.0f), OR %.0fx more months"
                  % (kcur * factor, kcur, factor))
            print("  NOTE: extra K only shrinks the WITHIN-month term. If between-month")
            print("  drift dominates, more K per month cannot fix it -- you need more")
            print("  months, or to remove the drift (denominator cleaning, index pinning).")

    # ---- cleaned denominator ----
    print("\n--- DENOMINATOR CLEANING (drop refs with neither DOI nor title) ---")
    tot_r = tot_p = 0
    for label, clean in (("raw", False), ("cleaned", True)):
        num = den = 0
        for mo in months:
            rr, pr, nref, npap, tup = month_stats(by[mo], clean=clean)
            num += sum(x for x, _ in tup); den += sum(d for _, d in tup)
        print("  %-8s overall unmatched rate %.3f%%  (denominator %d refs)"
              % (label, 100.0 * num / den if den else float("nan"), den))


if __name__ == "__main__":
    main()
