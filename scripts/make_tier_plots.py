#!/usr/bin/env python3
"""
make_tier_plots.py — generate tier comparison figures (fig7, fig8, fig9).

Reads a batch_verify_years results JSONL and writes three PNG figures:
  fig7_tier_trends.png  — NOT-FOUND rate by tier across years
  fig8_pre_post_bars.png — pre- vs post-ChatGPT grouped bar chart
  fig9_tier_strip.png   — per-paper strip + box plots by tier and period

Usage:
    python3 make_tier_plots.py [results.jsonl] [--outdir DIR]
"""
import argparse, json, pathlib
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np

TIERS = {
    "Nature Communications":   "High quality",
    "eLife":                   "High quality",
    "PLOS ONE":                "Standard",
    "IEEE Access":             "Standard",
    "JAMA Network Open":       "Standard",
    "ACS Omega":               "Standard",
    "Cureus":                  "Megajournal",
    "F1000Research":          "Megajournal",
    "Frontiers in Psychology": "Megajournal",
}
TIER_COLORS = {
    "High quality": "#27AE60",
    "Standard":     "#5B9BD5",
    "Megajournal":  "#E07B54",
}
TIER_ORDER = ["High quality", "Standard", "Megajournal"]


def load_rows(path: str) -> list[dict]:
    rows = []
    with open(path) as f:
        for line in f:
            d = json.loads(line)
            if d.get("status") == "ok" and d.get("total", 0) > 0:
                rows.append(d)
    return rows


def aggregate(rows):
    by_tier_year = defaultdict(lambda: {"papers": 0, "total": 0, "not_found": 0})
    for r in rows:
        tier = TIERS.get(r["journal_name"], "Standard")
        key  = (tier, r["year"])
        by_tier_year[key]["papers"]    += 1
        by_tier_year[key]["total"]     += r["total"]
        by_tier_year[key]["not_found"] += r["not_found"]
    return by_tier_year


def fig7(by_tier_year, years, out: pathlib.Path):
    all_rates = [
        100 * by_tier_year[(t, y)]["not_found"] / by_tier_year[(t, y)]["total"]
        for t in TIER_ORDER for y in years
        if by_tier_year.get((t, y)) and by_tier_year[(t, y)]["total"] > 30
    ]
    ymax = max(all_rates) * 1.3

    fig, ax = plt.subplots(figsize=(10, 5.5))
    for tier in TIER_ORDER:
        ys, rs, ns = [], [], []
        for y in years:
            d = by_tier_year.get((tier, y))
            if d and d["total"] > 30:
                ys.append(y)
                rs.append(100 * d["not_found"] / d["total"])
                ns.append(d["papers"])
        color = TIER_COLORS[tier]
        ax.plot(ys, rs, "o-", color=color, linewidth=2.5, markersize=8,
                label=tier, zorder=3)
        for y, r, n in zip(ys, rs, ns):
            ax.annotate(f"{r:.1f}%\n({n}p)", (y, r),
                        textcoords="offset points", xytext=(0, 10),
                        ha="center", fontsize=7.5, color=color)

    ax.axvline(2022.92, color="#aaa", linewidth=1.5, linestyle="--", zorder=2)
    ax.text(2023.0, ymax * 0.97, "ChatGPT\nreleased\nNov 2022",
            fontsize=8, color="#666", va="top", ha="left")
    ax.set_xlabel("Publication year", fontsize=11)
    ax.set_ylabel("NOT-FOUND rate (%)", fontsize=11)
    ax.set_title("Citation NOT-FOUND Rate by Journal Tier (2020–2025)\n"
                 "High quality vs Standard vs Megajournal", fontsize=12)
    ax.yaxis.set_major_formatter(mtick.PercentFormatter())
    ax.set_xticks(years)
    ax.set_ylim(0, ymax)
    ax.legend(fontsize=10, framealpha=0.9)
    ax.grid(alpha=0.3)
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    plt.savefig(out / "fig7_tier_trends.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved fig7_tier_trends.png")


def fig8(by_tier_year, out: pathlib.Path):
    fig, ax = plt.subplots(figsize=(8, 5))
    x = np.arange(len(TIER_ORDER))
    width = 0.35

    pre_rates, post_rates = [], []
    for tier in TIER_ORDER:
        pre  = {"total": 0, "not_found": 0}
        post = {"total": 0, "not_found": 0}
        for (t, y), d in by_tier_year.items():
            if t != tier:
                continue
            target = pre if y <= 2022 else post
            target["total"]     += d["total"]
            target["not_found"] += d["not_found"]
        pre_rates.append(100 * pre["not_found"]  / pre["total"]  if pre["total"]  else 0)
        post_rates.append(100 * post["not_found"] / post["total"] if post["total"] else 0)

    bars1 = ax.bar(x - width / 2, pre_rates,  width, label="Pre-ChatGPT (2020–2022)",
                   color=[TIER_COLORS[t] for t in TIER_ORDER], alpha=0.5, edgecolor="white")
    bars2 = ax.bar(x + width / 2, post_rates, width, label="Post-ChatGPT (2023–2025)",
                   color=[TIER_COLORS[t] for t in TIER_ORDER], alpha=1.0, edgecolor="white")

    for bar, rate in list(zip(bars1, pre_rates)) + list(zip(bars2, post_rates)):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.1,
                f"{rate:.1f}%", ha="center", va="bottom", fontsize=9)
    for i, (pre, post) in enumerate(zip(pre_rates, post_rates)):
        delta = post - pre
        color = "#c0392b" if delta > 0 else "#27ae60"
        ax.annotate(f"{delta:+.1f}pp",
                    xy=(i + width / 2, post + 0.5), fontsize=9,
                    ha="center", color=color, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(TIER_ORDER, fontsize=11)
    ax.set_ylabel("NOT-FOUND rate (%)", fontsize=11)
    ax.set_title("Pre- vs Post-ChatGPT NOT-FOUND Rate by Journal Tier\n"
                 "(Nov 2022 cutoff)", fontsize=12)
    ax.yaxis.set_major_formatter(mtick.PercentFormatter())
    ax.legend(fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    plt.savefig(out / "fig8_pre_post_bars.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved fig8_pre_post_bars.png")


def fig9(rows, out: pathlib.Path):
    fig, axes = plt.subplots(1, 3, figsize=(13, 5.5), sharey=True)
    rng = np.random.default_rng(42)

    for ax, tier in zip(axes, TIER_ORDER):
        tier_rows = [r for r in rows if TIERS.get(r["journal_name"]) == tier]
        pre  = [100 * r["not_found"] / r["total"] for r in tier_rows if r["year"] <= 2022]
        post = [100 * r["not_found"] / r["total"] for r in tier_rows if r["year"] >= 2023]

        color = TIER_COLORS[tier]
        for i, (data, alpha) in enumerate([(pre, 0.45), (post, 0.9)]):
            if not data:
                continue
            jitter = rng.uniform(-0.08, 0.08, len(data))
            ax.scatter([i + j for j in jitter], data, color=color,
                       alpha=alpha, s=25, edgecolors="white", linewidths=0.3)
            ax.boxplot(data, positions=[i], widths=0.3, patch_artist=True,
                       boxprops=dict(facecolor="none", edgecolor=color, linewidth=1.5),
                       medianprops=dict(color=color, linewidth=2),
                       whiskerprops=dict(color=color, linewidth=1),
                       capprops=dict(color=color, linewidth=1.5),
                       flierprops=dict(marker=""))

        ax.set_xticks([0, 1])
        ax.set_xticklabels(["2020–22\n(pre)", "2023–25\n(post)"], fontsize=10)
        ax.set_title(tier, fontsize=11, fontweight="bold", color=color)
        ax.grid(axis="y", alpha=0.3)
        ax.spines[["top", "right"]].set_visible(False)
        if ax is axes[0]:
            ax.set_ylabel("NOT-FOUND rate per paper (%)", fontsize=10)
        ax.yaxis.set_major_formatter(mtick.PercentFormatter())

    fig.suptitle("Per-Paper NOT-FOUND Rate: Pre vs Post ChatGPT by Tier",
                 fontsize=12, fontweight="bold")
    plt.tight_layout()
    plt.savefig(out / "fig9_tier_strip.png", dpi=150, bbox_inches="tight")
    plt.close()
    print("Saved fig9_tier_strip.png")


def main():
    ap = argparse.ArgumentParser(description="Generate tier comparison plots.")
    ap.add_argument("results", nargs="?",
                    default="/home/rwang/cross_year_study/results_v2.jsonl",
                    help="path to results JSONL")
    ap.add_argument("--outdir", default="/home/rwang/cross_year_study/plots",
                    help="output directory for PNG files")
    args = ap.parse_args()

    out = pathlib.Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)

    rows = load_rows(args.results)
    print(f"Loaded {len(rows)} verified papers")

    by_tier_year = aggregate(rows)
    years = sorted({y for _, y in by_tier_year})

    fig7(by_tier_year, years, out)
    fig8(by_tier_year, out)
    fig9(rows, out)
    print("All tier plots done.")


if __name__ == "__main__":
    main()
