"""
sample_papers.py — sample N OA papers per journal per year from OpenAlex.

For each (journal, year) pair, queries OpenAlex for OA works, picks N papers
that have a usable PDF URL, and writes a JSONL manifest.

Usage:
    python3 sample_papers.py                          # all journals, 2020–2025, n=10
    python3 sample_papers.py --years 2023 2024 2025
    python3 sample_papers.py --n 112
    python3 sample_papers.py --n 112 --exclude-jsonl /path/to/results_v3.jsonl
    python3 sample_papers.py --out manifest.jsonl

Output JSONL fields per line:
    journal_name, journal_id, field, tier, year, doi, title, oa_url, cited_by_count

--exclude-jsonl: path to a JSONL results file; any DOI already present in that
    file will be excluded from output (so you only get NEW papers).
"""

from __future__ import annotations
import argparse, json, pathlib, random, sys, time
import requests

EMAIL = "rwang@university.edu"

# ── Journals to sample ────────────────────────────────────────────────────────
JOURNALS = [
    # ── High-quality / established journals ───────────────────────────────
    {"name": "PLOS ONE",              "id": "S202381698",   "field": "biology_medicine",    "tier": "standard"},
    {"name": "Nature Communications", "id": "S64187185",    "field": "multidisciplinary",   "tier": "high"},
    {"name": "eLife",                 "id": "S1336409049",  "field": "life_sciences",        "tier": "high"},
    {"name": "JAMA Network Open",     "id": "S4210217848",  "field": "clinical_medicine",   "tier": "standard"},
    {"name": "IEEE Access",           "id": "S2485537415",  "field": "cs_engineering",       "tier": "standard"},
    {"name": "ACS Omega",             "id": "S4210239500",  "field": "chemistry",             "tier": "standard"},
    # ── High-volume / open-access megajournals (comparison group) ─────────
    {"name": "Cureus",                "id": "S2738950867",  "field": "clinical_medicine",   "tier": "megajournal"},
    {"name": "F1000Research",        "id": "S4210239046",  "field": "multidisciplinary", "tier": "megajournal"},
    {"name": "Frontiers in Psychology","id": "S9692511",    "field": "psychology",            "tier": "megajournal"},
]

YEARS = list(range(2020, 2026))   # 2020–2025 inclusive

# How many OpenAlex pages to fetch to build the candidate pool.
# Each page returns up to 200 results, so 3 pages = up to 600 candidates.
MAX_PAGES = 3


def _oa_url(work: dict) -> str | None:
    """Extract the best OA PDF URL from an OpenAlex work record."""
    def _is_pdf_url(u: str) -> bool:
        ul = u.lower()
        return ul.endswith(".pdf") or ul.endswith("/pdf")

    oa = work.get("open_access") or {}
    url = oa.get("oa_url") or ""
    if url and _is_pdf_url(url):
        return url
    # Prefer location pdf_urls that look like actual PDFs
    for loc in (work.get("locations") or []):
        u = loc.get("pdf_url") or ""
        if u and _is_pdf_url(u):
            return u
    # Fall back to any oa_url (may be landing page — we'll try it)
    return url or None


def fetch_sample(journal: dict, year: int, n: int,
                 seed: int | None = None,
                 exclude_dois: set[str] | None = None,
                 max_pages: int = MAX_PAGES) -> list[dict]:
    """
    Return up to `n` sampled papers from `journal` in `year`.

    Fetches up to MAX_PAGES pages from OpenAlex (up to 600 candidates),
    filters for valid OA PDF URLs and non-excluded DOIs, then picks n at random.

    Parameters
    ----------
    exclude_dois : set of DOI strings to skip (already verified papers)
    """
    if exclude_dois is None:
        exclude_dois = set()

    all_candidates: list[dict] = []

    for page in range(1, max_pages + 1):
        params = {
            "filter":   (f"primary_location.source.id:{journal['id']},"
                         f"publication_year:{year},"
                         f"open_access.is_oa:true"),
            "select":   "id,doi,title,publication_year,cited_by_count,open_access,locations",
            "per_page": "200",
            "page":     str(page),
            "sort":     "cited_by_count:desc",
            "mailto":   EMAIL,
        }

        try:
            r = requests.get("https://api.openalex.org/works", params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            results = data.get("results", [])
        except Exception as e:
            print(f"    ⚠  OpenAlex request failed (page {page}): {e}", file=sys.stderr)
            break

        if not results:
            break   # no more pages

        for w in results:
            doi = (w.get("doi") or "").replace("https://doi.org/", "").strip().lower()
            if not doi:
                continue
            if doi in exclude_dois:
                continue
            url = _oa_url(w)
            if not url:
                continue
            all_candidates.append({
                "journal_name":   journal["name"],
                "journal_id":     journal["id"],
                "field":          journal["field"],
                "tier":           journal.get("tier", "standard"),
                "year":           year,
                "doi":            doi,
                "title":          (w.get("title") or "").strip(),
                "oa_url":         url,
                "cited_by_count": w.get("cited_by_count", 0),
            })

        # Stop early if we already have plenty of candidates
        if len(all_candidates) >= n * 4:
            break

        time.sleep(0.2)  # be polite to OpenAlex between pages

    if not all_candidates:
        return []

    # Reproducible random sample (seed = year * 1000 + journal index)
    rng = random.Random(seed if seed is not None else year * 1000)
    rng.shuffle(all_candidates)
    return all_candidates[:n]


def main():
    ap = argparse.ArgumentParser(description="Sample OA papers from OpenAlex by journal and year.")
    ap.add_argument("--years", nargs="+", type=int, default=YEARS)
    ap.add_argument("--n",    type=int, default=10, help="papers per journal per year")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES, help="OpenAlex pages to fetch per (journal, year) pair (200 results/page)")
    ap.add_argument("--out",  default="", help="output JSONL path (default: print to stdout)")
    ap.add_argument("--journals", nargs="+", default=[],
                    help="subset of journal names to include (default: all)")
    ap.add_argument("--exclude-jsonl", default="",
                    help="path to existing results JSONL; DOIs in this file are excluded from output")
    args = ap.parse_args()

    # Load DOIs to exclude (already verified papers)
    exclude_dois: set[str] = set()
    if args.exclude_jsonl and pathlib.Path(args.exclude_jsonl).exists():
        with open(args.exclude_jsonl) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    doi = (row.get("doi") or "").lower().strip()
                    if doi:
                        exclude_dois.add(doi)
                except json.JSONDecodeError:
                    pass
        print(f"Excluding {len(exclude_dois):,} already-verified DOIs", file=sys.stderr)

    journals = JOURNALS
    if args.journals:
        journals = [j for j in JOURNALS if j["name"] in args.journals]

    lines = []
    total_expected = len(journals) * len(args.years) * args.n

    print(f"Sampling up to {args.n} papers × {len(journals)} journals × {len(args.years)} years "
          f"= up to {total_expected} papers", file=sys.stderr)
    print(f"Years: {sorted(args.years)}", file=sys.stderr)
    if exclude_dois:
        print(f"Excluding: {len(exclude_dois):,} existing DOIs", file=sys.stderr)
    print(file=sys.stderr)

    for j in journals:
        for year in sorted(args.years):
            print(f"  {j['name']} {year}… ", end="", flush=True, file=sys.stderr)
            papers = fetch_sample(j, year, args.n, exclude_dois=exclude_dois, max_pages=args.max_pages)
            print(f"{len(papers)} new papers", file=sys.stderr)
            lines.extend(papers)
            time.sleep(0.3)   # be polite to OpenAlex

    print(f"\nTotal new papers sampled: {len(lines)}", file=sys.stderr)

    out_text = "\n".join(json.dumps(p) for p in lines) + "\n"
    if args.out:
        pathlib.Path(args.out).write_text(out_text)
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(out_text)


if __name__ == "__main__":
    main()
