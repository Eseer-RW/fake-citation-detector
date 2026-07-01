#!/usr/bin/env python3
"""
make_tier_histogram.py — per-paper NOT-FOUND rate histograms by tier (fig10).

Plots pre- vs post-ChatGPT distributions for each journal tier as paired
histograms (5 pp bins), with per-panel median annotations.

Usage:
    python3 make_tier_histogram.py [results.jsonl] [--outdir DIR]
"""
import argparse, json, pathlib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

TIERS      = ["high", "standard", "megajournal"]
TIER_LABELS = {"high": "High quality", "standard": "Standard", "megajournal": "Megajournal"}
TIER_COLORS = {"high": "#2ca02c",      "standard": "#1f77b4",  "megajournal": "#d62728"}

JOURNAL_TIER = {
    "Nature Communications":   "high",
    "eLife":                   "high",
    "PLOS ONE":                "standard",
    "IEEE Access":             "standard",
    "JAMA Network Open":       "standard",
    "ACS Omega":               "standard",
    "Cureus":                  "megajournal",
    "F1000Research":          "megajournal",
    "Frontiers in Psychology": "megajournal",
}


def main():
    ap = argparse.ArgumentParser(description="Generate tier histogram (fig10).")
    ap.add_argument("results", nargs="?",
                    default="/home/rwang/cross_year_study/results_v2.jsonl",
                    help="path to results JSONL")
    ap.add_argument("--outdir", default="/home/rwang/cross_year_study/plots",
                    help="output directory for PNG files")
    args = ap.parse_args()

    out = pathlib.Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)

    tier_pre  = {t: [] for t in TIERS}
    tier_post = {t: [] for t in TIERS}

    with open(args.results) as f:
        for line in f:
            d = json.loads(line)
            tier  = JOURNAL_TIER.get(d.get("journal_name", ""), None)
            total = d.get("total", 0)
            if not tier or total == 0:
                continue
            rate = d.get("not_found", 0) / total * 100
            if d.get("year", 0) <= 2022:
                tier_pre[tier].append(rate)
            else:
                tier_post[tier].append(rate)

    bins = list(range(0, 51, 5)) + [100]
    bin_labels = [f"{b}–{bins[i+1]}%" if bins[i+1] <= 50 else "50%+"
                  for i, b in enumerate(bins[:-1])]

    fig, axes = plt.subplots(1, 3, figsize=(15, 5), sharey=False)
    fig.suptitle(
        "Per-Paper NOT-FOUND Rate Distribution by Journal Tier\n"
        "Pre- vs Post-ChatGPT (Nov 2022 cutoff)",
        fontsize=14, fontweight="bold",
    )

    for ax, tier in zip(axes, TIERS):
        pre   = np.array(tier_pre[tier])
        post  = np.array(tier_post[tier])
        color = TIER_COLORS[tier]

        pre_counts,  _ = np.histogram(pre,  bins=bins)
        post_counts, _ = np.histogram(post, bins=bins)

        x     = np.arange(len(bin_labels))
        width = 0.38
        ax.bar(x - width / 2, pre_counts,  width,
               label=f"Pre-ChatGPT 2020–22 (n={len(pre)})",
               color=color, alpha=0.4, edgecolor=color, linewidth=0.8)
        ax.bar(x + width / 2, post_counts, width,
               label=f"Post-ChatGPT 2023–25 (n={len(post)})",
               color=color, alpha=0.85, edgecolor=color, linewidth=0.8)

        ax.set_title(TIER_LABELS[tier], fontsize=13, fontweight="bold", color=color)
        ax.set_xlabel("NOT-FOUND rate per paper (%)", fontsize=10)
        ax.set_ylabel("Number of papers", fontsize=10)
        ax.set_xticks(x)
        ax.set_xticklabels(bin_labels, rotation=45, ha="right", fontsize=8)
        ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
        ax.legend(fontsize=8)
        ax.grid(axis="y", alpha=0.3)

        pre_med  = np.median(pre)  if len(pre)  else 0.0
        post_med = np.median(post) if len(post) else 0.0
        ax.text(0.97, 0.97,
                f"Pre median:  {pre_med:.1f}%\nPost median: {post_med:.1f}%",
                transform=ax.transAxes, fontsize=8, va="top", ha="right",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                          alpha=0.8, edgecolor=color))

    plt.tight_layout()
    dest = out / "fig10_tier_histogram.png"
    plt.savefig(dest, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved {dest}")


if __name__ == "__main__":
    main()
