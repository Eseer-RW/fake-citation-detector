#!/usr/bin/env python3
"""
tier_analysis.py — summarise NOT-FOUND rates by journal tier and year.

Reads a batch_verify_years results JSONL and prints:
  • Year × tier table
  • Pre- vs post-ChatGPT comparison (Nov 2022 cutoff)

Usage:
    python3 tier_analysis.py [results.jsonl]
    python3 tier_analysis.py /home/rwang/cross_year_study/results_v2.jsonl
"""
import argparse, json, sys
from collections import defaultdict

TIERS = {
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
    ap = argparse.ArgumentParser(description="Tier-level NOT-FOUND rate analysis.")
    ap.add_argument("results", nargs="?",
                    default="/home/rwang/cross_year_study/results_v2.jsonl",
                    help="path to results JSONL (default: cross_year_study/results_v2.jsonl)")
    args = ap.parse_args()

    rows = []
    with open(args.results) as f:
        for line in f:
            d = json.loads(line)
            if d.get("status") == "ok" and d.get("total", 0) > 0:
                rows.append(d)

    by_tier_year = defaultdict(lambda: {"papers": 0, "total": 0, "not_found": 0})
    for r in rows:
        tier = TIERS.get(r["journal_name"], "standard")
        key  = (tier, r["year"])
        by_tier_year[key]["papers"]    += 1
        by_tier_year[key]["total"]     += r["total"]
        by_tier_year[key]["not_found"] += r["not_found"]

    tiers = ["high", "standard", "megajournal"]
    years = sorted({y for _, y in by_tier_year})

    print(f"{'Year':<6}", end="")
    for t in tiers:
        print(f"  {t:>14}", end="")
    print()
    print("-" * 52)
    for y in years:
        print(f"{y:<6}", end="")
        for t in tiers:
            d = by_tier_year.get((t, y))
            if d and d["total"] > 0:
                rate = 100 * d["not_found"] / d["total"]
                print(f"  {rate:>13.1f}%", end="")
            else:
                print(f"  {'—':>14}", end="")
        print()

    print("\nPre-ChatGPT (2020-2022) vs Post (2023-2025):")
    for t in tiers:
        pre  = {"total": 0, "not_found": 0}
        post = {"total": 0, "not_found": 0}
        for (tier, y), d in by_tier_year.items():
            if tier != t:
                continue
            target = pre if y <= 2022 else post
            target["total"]     += d["total"]
            target["not_found"] += d["not_found"]
        pre_rate  = 100 * pre["not_found"]  / pre["total"]  if pre["total"]  else 0
        post_rate = 100 * post["not_found"] / post["total"] if post["total"] else 0
        delta = post_rate - pre_rate
        arrow = "↑" if delta > 0.5 else ("↓" if delta < -0.5 else "→")
        print(f"  {t:<14}  pre={pre_rate:.1f}%  post={post_rate:.1f}%  "
              f"delta={delta:+.1f}pp  {arrow}")

if __name__ == "__main__":
    main()
