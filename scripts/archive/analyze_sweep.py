#!/usr/bin/env python3
"""
analyze_sweep.py — Zhao-estimand analysis of the arXiv temporal sweep.

Reads results/arxiv_sweep/months_*.csv and computes:
  * per-month unmatched (academic) rate = sum_nf_academic / sum_refs   (ref-weighted)
  * pre-LLM baseline = ref-weighted mean rate over baseline months (< 2023-01)
  * post-LLM EXCESS = rate - baseline  (Zhao's headline quantity)
  * OLS trend of excess over the post-2022 window
Writes a summary table + a Figure-1-style plot (baseline line + monthly rate + excess).
"""
import sys, csv, math, argparse, statistics as st

def load(csv_path):
    rows = []
    with open(csv_path) as f:
        for d in csv.DictReader(f):
            try:
                mo = d["month"]                       # YYMM
                yy, mm = 2000 + int(mo[:2]), int(mo[2:])
                refs = int(d["sum_refs"]); nfa = int(d["sum_nf_academic"])
                if refs < 200:      # too few refs -> skip noisy month
                    continue
                rows.append({"ym": yy + (mm - 1) / 12.0, "yy": yy, "mm": mm, "mo": mo,
                             "refs": refs, "nfa": nfa, "rate": nfa / refs,
                             "papers": int(d["n_papers"]), "mism": int(d["sum_mismatch"])})
            except Exception:
                continue
    rows.sort(key=lambda r: r["ym"])
    return rows

def ols(xs, ys):
    n = len(xs)
    if n < 3: return float("nan"), float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0: return float("nan"), float("nan")
    slope = sxy / sxx
    intercept = my - slope * mx
    return slope, intercept

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--baseline-end", type=float, default=2023.0,
                    help="months with decimal-year < this form the pre-LLM baseline")
    ap.add_argument("--fig", default=None)
    a = ap.parse_args()

    rows = load(a.csv)
    if not rows:
        print("no rows"); return
    base = [r for r in rows if r["ym"] < a.baseline_end]
    post = [r for r in rows if r["ym"] >= a.baseline_end]
    # ref-weighted baseline rate
    b_refs = sum(r["refs"] for r in base); b_nfa = sum(r["nfa"] for r in base)
    baseline = b_nfa / b_refs if b_refs else float("nan")

    print(f"months={len(rows)}  ({rows[0]['mo']}..{rows[-1]['mo']})  "
          f"baseline_months={len(base)}  post_months={len(post)}")
    print(f"pre-LLM baseline unmatched rate = {baseline*100:.2f}%  (ref-weighted, n_refs={b_refs})")
    print("\nyear  mean_rate%  mean_excess(pp)")
    by_year = {}
    for r in rows: by_year.setdefault(r["yy"], []).append(r)
    for yy in sorted(by_year):
        rs = by_year[yy]
        rr = sum(x["nfa"] for x in rs) / sum(x["refs"] for x in rs)
        print(f"{yy}   {rr*100:6.2f}    {(rr-baseline)*100:+6.2f}")

    if post:
        xs = [r["ym"] for r in post]; ys = [(r["rate"] - baseline) * 100 for r in post]
        slope, _ = ols(xs, ys)
        last = post[-1]
        print(f"\npost-2022 EXCESS trend: {slope:+.3f} pp/yr")
        print(f"latest month {last['mo']} excess = {(last['rate']-baseline)*100:+.2f} pp")
        print("(Zhao reports arXiv +0.39pp excess by Aug-2025; compare sign & magnitude.)")

    # optional plot
    if a.fig:
        try:
            import matplotlib; matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            xs = [r["ym"] for r in rows]; ys = [r["rate"] * 100 for r in rows]
            plt.figure(figsize=(11, 5))
            plt.plot(xs, ys, marker=".", lw=1, label="monthly unmatched rate")
            plt.axhline(baseline * 100, color="gray", ls="--", label=f"pre-LLM baseline {baseline*100:.2f}%")
            plt.axvline(2022.92, color="red", ls=":", alpha=.6, label="ChatGPT (2022-11)")
            plt.xlabel("year"); plt.ylabel("unmatched (academic) rate %")
            plt.title("arXiv raw-reference unmatched rate over time (Zhao estimand)")
            plt.legend(); plt.tight_layout(); plt.savefig(a.fig, dpi=130)
            print(f"\nfigure -> {a.fig}")
        except Exception as e:
            print("plot skipped:", e)

if __name__ == "__main__":
    main()
