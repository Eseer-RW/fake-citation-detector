#!/usr/bin/env python3
"""
make_year_histogram.py — per-year NOT-FOUND rate histograms (fig6).

Reads a batch_verify_years results JSONL and plots the distribution of
per-paper NOT-FOUND rates for each year in a 2×3 grid.

Usage:
    python3 make_year_histogram.py [results.jsonl] [--outdir DIR]
"""
import argparse, json, pathlib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np

PALETTE = ["#2C3E70", "#3D6FAB", "#5B9BD5", "#7CBDDC", "#A8D8A8", "#4CAF50"]


def main():
    ap = argparse.ArgumentParser(description="Generate per-year histogram (fig6).")
    ap.add_argument("results", nargs="?",
                    default="/home/rwang/cross_year_study/results_v3.jsonl",
                    help="path to results JSONL")
    ap.add_argument("--outdir", default="/home/rwang/cross_year_study/plots",
                    help="output directory for PNG files")
    args = ap.parse_args()

    out = pathlib.Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    with open(args.results) as f:
        for line in f:
            d = json.loads(line)
            if d.get("status") == "ok" and d.get("total", 0) > 0:
                rows.append(d)

    years = sorted({r["year"] for r in rows})
    year_rates = {
        y: [100 * r["not_found"] / r["total"] for r in rows if r["year"] == y]
        for y in years
    }
    total_papers = len(rows)
    total_refs   = sum(r["total"] for r in rows)

    fig, axes = plt.subplots(2, 3, figsize=(13, 7), sharey=False)
    axes = axes.flatten()
    bins = np.arange(0, 55, 5)

    for ax, year, color in zip(axes, years, PALETTE):
        data   = year_rates[year]
        median = np.median(data)
        mean   = np.mean(data)

        ax.hist(data, bins=bins, color=color, edgecolor="white", linewidth=0.8, zorder=2)
        ax.axvline(median, color="#222", linewidth=1.8, linestyle="--",
                   label=f"Median {median:.1f}%")
        ax.axvline(mean,   color="#888", linewidth=1.2, linestyle=":",
                   label=f"Mean {mean:.1f}%")

        ax.set_title(f"{year}  (n={len(data)} papers)", fontsize=11, fontweight="bold")
        ax.set_xlabel("NOT-FOUND rate per paper", fontsize=9)
        ax.set_ylabel("Number of papers", fontsize=9)
        ax.xaxis.set_major_formatter(mtick.PercentFormatter())
        ax.set_xlim(-1, 52)
        ax.legend(fontsize=8, framealpha=0.85)
        ax.grid(axis="y", alpha=0.3)
        ax.spines[["top", "right"]].set_visible(False)

    fig.suptitle(
        f"Distribution of Per-Paper NOT-FOUND Rates by Year\n"
        f"({total_papers:,} papers, {total_refs:,} citations — 5% bin width)",
        fontsize=13, fontweight="bold", y=1.01,
    )
    plt.tight_layout()
    dest = out / "fig6_histogram.png"
    plt.savefig(dest, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Saved {dest}  ({total_papers} papers)")


if __name__ == "__main__":
    main()
