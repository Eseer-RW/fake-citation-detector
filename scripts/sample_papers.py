"""
sample_papers.py — sample N OA papers per journal per year from OpenAlex.

For each (journal, year) pair, queries OpenAlex for OA works, picks N papers
that have a usable PDF URL, and writes a JSONL manifest.

Usage:
    python3 sample_papers.py                          # all journals, 2020–2025, n=10
    python3 sample_papers.py --years 2023 2024 2025
    python3 sample_papers.py --n 20
    python3 sample_papers.py --out manifest.jsonl

Output JSONL fields per line:
    journal_name, journal_id, field, year, doi, title, oa_url, cited_by_count
"""

from __future__ import annotations
import argparse, json, pathlib, random, sys, time
import requests

EMAIL = "rwang@university.edu"

# ── Journals to sample ────────────────────────────────────────────────────────
JOURNALS = [
    {"name": "PLOS ONE",              "id": "S202381698",   "field": "biology_medicine"},
    {"name": "Nature Communications", "id": "S64187185",    "field": "multidisciplinary"},
    {"name": "eLife",                 "id": "S1336409049",  "field": "life_sciences"},
    {"name": "JAMA Network Open",     "id": "S4210217848",  "field": "clinical_medicine"},
    {"name": "IEEE Access",           "id": "S2485537415",  "field": "cs_engineering"},
    {"name": "ACS Omega",             "id": "S4210239500",  "field": "chemistry"},
]

YEARS = list(range(2020, 2026))   # 2020–2025 inclusive


def _oa_url(work: dict) -> str | None:
    """Extract the best OA PDF URL from an OpenAlex work record."""
    oa = work.get("open_access") or {}
    url = oa.get("oa_url") or ""
    if url and url.lower().endswith(".pdf"):
        return url
    # Also check locations
    for loc in (work.get("locations") or []):
        u = loc.get("pdf_url") or ""
        if u and u.lower().endswith(".pdf"):
            return u
    # Fall back to any oa_url (may be landing page, not PDF — we'll try it)
    return url or None


def fetch_sample(journal: dict, year: int, n: int,
                 seed: int | None = None) -> list[dict]:
    """
    Return up to `n` sampled papers from `journal` in `year`.
    Fetches a pool of candidates from OpenAlex and picks `n` at random (seeded).
    """
    pool_size = max(n * 6, 60)   # fetch more than needed so we have selection room

    params = {
        "filter":   f"primary_location.source.id:{journal['id']},publication_year:{year},open_access.is_oa:true",
        "select":   "id,doi,title,publication_year,cited_by_count,open_access,locations",
        "per_page": str(min(pool_size, 200)),
        "sort":     "cited_by_count:desc",
        "mailto":   EMAIL,
    }

    try:
        r = requests.get("https://api.openalex.org/works", params=params, timeout=30)
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception as e:
        print(f"    ⚠  OpenAlex request failed: {e}", file=sys.stderr)
        return []

    # Filter: must have DOI and a usable OA URL
    candidates = []
    for w in results:
        doi = (w.get("doi") or "").replace("https://doi.org/", "").strip()
        if not doi:
            continue
        url = _oa_url(w)
        if not url:
            continue
        candidates.append({
            "journal_name":   journal["name"],
            "journal_id":     journal["id"],
            "field":          journal["field"],
            "year":           year,
            "doi":            doi,
            "title":          (w.get("title") or "").strip(),
            "oa_url":         url,
            "cited_by_count": w.get("cited_by_count", 0),
        })

    if not candidates:
        return []

    # Reproducible random sample (seed = year * 1000 + journal index)
    rng = random.Random(seed if seed is not None else year * 1000)
    rng.shuffle(candidates)
    return candidates[:n]


def main():
    ap = argparse.ArgumentParser(description="Sample OA papers from OpenAlex by journal and year.")
    ap.add_argument("--years", nargs="+", type=int, default=YEARS)
    ap.add_argument("--n",    type=int, default=10, help="papers per journal per year")
    ap.add_argument("--out",  default="", help="output JSONL path (default: print to stdout)")
    ap.add_argument("--journals", nargs="+", default=[],
                    help="subset of journal names to include (default: all)")
    args = ap.parse_args()

    journals = JOURNALS
    if args.journals:
        journals = [j for j in JOURNALS if j["name"] in args.journals]

    lines = []
    total_expected = len(journals) * len(args.years) * args.n

    print(f"Sampling {args.n} papers × {len(journals)} journals × {len(args.years)} years "
          f"= up to {total_expected} papers", file=sys.stderr)
    print(f"Years: {args.years}", file=sys.stderr)
    print(file=sys.stderr)

    for j in journals:
        for year in sorted(args.years):
            print(f"  {j['name']} {year}… ", end="", flush=True, file=sys.stderr)
            papers = fetch_sample(j, year, args.n)
            print(f"{len(papers)} papers", file=sys.stderr)
            lines.extend(papers)
            time.sleep(0.3)   # be polite to OpenAlex

    print(f"\nTotal sampled: {len(lines)}", file=sys.stderr)

    out_text = "\n".join(json.dumps(p) for p in lines) + "\n"
    if args.out:
        pathlib.Path(args.out).write_text(out_text)
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(out_text)


if __name__ == "__main__":
    main()
