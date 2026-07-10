#!/usr/bin/env python3
"""
temporal_regression.py — Zhao et al. (2026)-style temporal hallucination analysis.

Fits a regression with year fixed effects (reference = 2020) to per-paper
not-found rates, producing the equivalent of Zhao et al. Fig. 1a-d:
a time series showing estimated hallucination rate above pre-LLM baseline.

For each field, we estimate:
    not_found_rate_i = alpha + sum_y beta_y * I(year_i == y) + eps_i
                                     y != 2020

The baseline is the 2020-2022 mean fitted rate.
The "hallucination rate" for each year is beta_y (excess above 2020 reference).

Usage:
    python3 temporal_regression.py [results.jsonl] [--outdir DIR]
"""
import argparse, json, pathlib, collections
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick

BASELINE_YEARS = {2020, 2021, 2022}
POST_LLM_YEARS = {2023, 2024, 2025}

FIELD_LABELS = {
    "biology_medicine":  "Biology / Medicine",
    "clinical_medicine": "Clinical Medicine",
    "cs_engineering":    "CS / Engineering",
    "life_sciences":     "Life Sciences",
    "multidisciplinary": "Multidisciplinary",
    "psychology":        "Psychology",
}

COLORS = ["#2C3E70", "#3D6FAB", "#5B9BD5", "#4CAF50", "#E67E22", "#9B59B6"]


def ols_year_effects(papers, years):
    """
    OLS with year dummies; 2020 is the omitted reference category.
    Returns dict: year -> (coef, se, p_value).
    Uses numpy least-squares directly (no external dep on statsmodels at call time).
    """
    import statsmodels.formula.api as smf
    import pandas as pd

    df = pd.DataFrame(papers)
    df["rate"] = df["not_found"] / df["total"]
    df["year"] = df["year"].astype(str)

    model = smf.wls(
        "rate ~ C(year, Treatment('2020'))",
        data=df,
        weights=df["total"],   # weight by number of references (more refs = more reliable)
    ).fit()

    effects = {}
    for y in years:
        if y == 2020:
            effects[y] = (0.0, 0.0, 1.0)
            continue
        key = f"C(year, Treatment('2020'))[T.{y}]"
        if key in model.params:
            effects[y] = (model.params[key], model.bse[key], model.pvalues[key])
        else:
            effects[y] = (np.nan, np.nan, np.nan)
    return effects, model.params.get("Intercept", np.nan)


def run(results_path, outdir):
    outdir = pathlib.Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # Load data
    papers = []
    for line in open(results_path):
        r = json.loads(line)
        if r.get("status") == "ok" and r.get("total", 0) >= 5:
            papers.append(r)

    fields = sorted({r["field"] for r in papers})
    years  = sorted({r["year"]  for r in papers})

    print(f"Loaded {len(papers):,} papers across {len(fields)} fields, years {min(years)}-{max(years)}")

    # ── Aggregate rates ──────────────────────────────────────────────────────
    # not_found_academic falls back to not_found for pre-heuristic results (v9)
    agg = collections.defaultdict(lambda: {"papers": 0, "total": 0, "not_found": 0})
    for r in papers:
        k = (r["field"], r["year"])
        agg[k]["papers"]    += 1
        agg[k]["total"]     += r["total"]
        # Use not_found_academic if available (v10+), else not_found (v9)
        agg[k]["not_found"] += r.get("not_found_academic", r["not_found"])

    # ── Regression per field ─────────────────────────────────────────────────
    all_effects = {}  # field -> year -> (coef, se, p)
    baselines   = {}  # field -> baseline rate (mean of 2020-2022)

    print("\nRegression results (year effects relative to 2020 baseline):")
    print(f"{'Field':<25} {'Baseline':>10} " +
          " ".join(f"{y:>12}" for y in years if y != 2020))
    print("-" * 100)

    for field in fields:
        field_papers = [r for r in papers if r["field"] == field]
        effects, intercept = ols_year_effects(field_papers, years)
        all_effects[field] = effects

        # Baseline = intercept (= fitted rate for 2020)
        # Actual baseline = mean aggregate rate over 2020-2022
        bl_total = sum(agg[(field, y)]["total"]    for y in BASELINE_YEARS if (field,y) in agg)
        bl_nf    = sum(agg[(field, y)]["not_found"] for y in BASELINE_YEARS if (field,y) in agg)
        baseline_rate = 100 * bl_nf / bl_total if bl_total else 0
        baselines[field] = baseline_rate

        row = f"{FIELD_LABELS.get(field, field):<25} {baseline_rate:>9.2f}% "
        for y in years:
            if y == 2020:
                continue
            coef, se, pval = effects[y]
            stars = "***" if pval < 0.001 else "**" if pval < 0.01 else "*" if pval < 0.05 else ""
            row += f" {100*coef:>+8.2f}pp{stars:<3}"
        print(row)

    # ── Figure 1-style plot ──────────────────────────────────────────────────
    n_fields = len(fields)
    ncols = 3
    nrows = (n_fields + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(14, 4 * nrows), sharey=False)
    axes = np.array(axes).flatten()

    for ax, field, color in zip(axes, fields, COLORS):
        effects = all_effects[field]
        baseline = baselines[field]

        ys    = sorted(years)
        coefs = [100 * effects[y][0] for y in ys]
        ses   = [100 * effects[y][1] for y in ys]
        pvals = [effects[y][2] for y in ys]

        # Raw aggregate rate per year
        raw_rates = []
        for y in ys:
            d = agg.get((field, y))
            raw_rates.append(100 * d["not_found"] / d["total"] if d and d["total"] else np.nan)

        # Plot raw rates (bars, semi-transparent)
        ax.bar(ys, raw_rates, color=color, alpha=0.25, label="Raw unmatched rate")

        # Baseline band
        ax.axhline(baseline, color="gray", linewidth=1.2, linestyle="--", alpha=0.8,
                   label=f"Baseline {baseline:.1f}%")
        ax.axvspan(min(BASELINE_YEARS) - 0.4, max(BASELINE_YEARS) + 0.4,
                   alpha=0.06, color="gray", label="Pre-LLM baseline window")

        # Regression coefficients as excess above baseline
        excess = [baseline + c for c in coefs]
        ax.plot(ys, excess, "o-", color=color, linewidth=2, markersize=6,
                label="Baseline + regression excess", zorder=3)

        # Error bars
        lower = [e - 1.96 * s for e, s in zip(excess, ses)]
        upper = [e + 1.96 * s for e, s in zip(excess, ses)]
        ax.fill_between(ys, lower, upper, alpha=0.15, color=color)

        # Mark post-2022 years with significance stars
        for y, p, e in zip(ys, pvals, excess):
            if y >= 2023 and p < 0.05:
                ax.text(y, e + 0.3, "*" if p < 0.05 else "", ha="center",
                        fontsize=10, color=color)

        ax.set_title(FIELD_LABELS.get(field, field), fontweight="bold")
        ax.set_xlabel("Year")
        ax.set_ylabel("Not-found rate (%)")
        ax.set_xticks(ys)
        ax.yaxis.set_major_formatter(mtick.PercentFormatter(decimals=1))
        ax.legend(fontsize=7, loc="upper right")
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=0.25)

    # Hide unused subplots
    for ax in axes[n_fields:]:
        ax.set_visible(False)

    fig.suptitle(
        "Estimated Hallucination Rate by Field (Zhao et al. 2026 design)\n"
        "Bars = raw unmatched rate; line = baseline + regression excess above 2020–2022 mean",
        fontsize=12, fontweight="bold", y=1.01,
    )
    plt.tight_layout()
    dest = outdir / "fig_temporal_regression.png"
    plt.savefig(dest, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"\nFigure saved: {dest}")

    # ── Summary table: excess rates ──────────────────────────────────────────
    print("\n" + "=" * 80)
    print("ESTIMATED HALLUCINATION EXCESS (pp above 2020-2022 baseline)")
    print("Positive = more unmatched refs than baseline; negative = improvement")
    print("=" * 80)
    print(f"{'Field':<25} {'Baseline':>10} " +
          " ".join(f"{y:>10}" for y in years if y >= 2023))
    print("-" * 80)
    for field in fields:
        effects = all_effects[field]
        row = f"{FIELD_LABELS.get(field, field):<25} {baselines[field]:>9.2f}% "
        for y in sorted(years):
            if y < 2023: continue
            coef, se, pval = effects[y]
            row += f" {100*coef:>+8.2f}pp"
        print(row)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="?",
                    default="/home/rwang/cross_year_study/results_v9.jsonl")
    ap.add_argument("--outdir", default="/home/rwang/cross_year_study/figures_v10")
    args = ap.parse_args()
    run(args.results, args.outdir)


if __name__ == "__main__":
    main()
