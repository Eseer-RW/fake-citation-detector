"""
fetch_openalex.py — download papers from specified journals via OpenAlex.

Usage:
    python3 fetch_openalex.py                          # uses built-in journal list
    python3 fetch_openalex.py "Nature" "Cell" "PLOS ONE"   # custom journals
    python3 fetch_openalex.py --per-journal 5          # 5 papers per journal (default 10)

Papers are saved to samples/openalex_pdfs/<JournalName>/<doi>.pdf
A metadata CSV is written to samples/openalex_pdfs/metadata.csv
"""
import sys
import os
import re
import time
import csv
import argparse
import requests

# ── config ────────────────────────────────────────────────────────────────

OPENALEX_API = "https://api.openalex.org"
MAILTO       = "rwang@example.com"   # lets OpenAlex put you in the "polite" pool (faster)
HEADERS      = {"User-Agent": f"FakeCitationDetector/1.0 (mailto:{MAILTO})"}
DELAY        = 0.15   # seconds between requests (polite pool allows up to ~10/s)

DEFAULT_JOURNALS = [
    "Nature",
    "Science",
    "Cell",
    "PLOS ONE",
    "The Lancet",
    "JAMA",
    "New England Journal of Medicine",
    "PLOS Medicine",
    "eLife",
    "BMJ",
]

# ── helpers ───────────────────────────────────────────────────────────────

def _get(url, params=None):
    """GET with retries and polite delay."""
    params = params or {}
    params["mailto"] = MAILTO
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    return {}


def resolve_source_id(journal_name: str) -> tuple[str, str] | tuple[None, None]:
    """Return (openalex_source_id, canonical_name) for a journal name, or (None, None)."""
    data = _get(f"{OPENALEX_API}/sources", {"search": journal_name, "per-page": 5})
    results = data.get("results", [])
    if not results:
        return None, None
    # Prefer exact match on display_name, otherwise take the first result
    for r in results:
        if r["display_name"].lower() == journal_name.lower():
            return r["id"].split("/")[-1], r["display_name"]
    return results[0]["id"].split("/")[-1], results[0]["display_name"]


def fetch_works(source_id: str, n: int) -> list[dict]:
    """Return up to n works from the given source that have a PDF URL."""
    data = _get(
        f"{OPENALEX_API}/works",
        {
            "filter":   f"primary_location.source.id:{source_id},has_pdf_url:true",
            "per-page": min(n, 50),
            "sort":     "cited_by_count:desc",
            "select":   "id,doi,title,best_oa_location,locations,publication_year,"
                        "authorships,biblio,primary_location",
        },
    )
    return data.get("results", [])[:n]


def safe_filename(s: str) -> str:
    """Turn a string into a safe filename component."""
    return re.sub(r'[^\w\-]', '_', s)[:60]


def download_pdf(pdf_url: str, dest_path: str) -> bool:
    """Download a PDF and save it.  Returns True on success."""
    try:
        r = requests.get(pdf_url, headers=HEADERS, timeout=30, allow_redirects=True)
        if r.status_code == 200 and r.content[:4] in (b"%PDF", b"\x25\x50\x44\x46"):
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, "wb") as f:
                f.write(r.content)
            return True
        return False
    except Exception:
        return False


# ── main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("journals",       nargs="*",      help="Journal names to fetch")
    parser.add_argument("--per-journal",  type=int, default=10,
                        help="Max papers to download per journal (default 10)")
    parser.add_argument("--out-dir",      default="samples/openalex_pdfs",
                        help="Output directory")
    args = parser.parse_args()

    journals     = args.journals or DEFAULT_JOURNALS
    per_journal  = args.per_journal
    out_dir      = args.out_dir

    os.makedirs(out_dir, exist_ok=True)

    csv_path = os.path.join(out_dir, "metadata.csv")
    csv_file  = open(csv_path, "w", newline="", encoding="utf-8")
    writer    = csv.DictWriter(csv_file, fieldnames=[
        "journal", "openalex_id", "doi", "year", "title", "authors",
        "volume", "issue", "pages", "pdf_url", "local_path", "downloaded",
    ])
    writer.writeheader()

    total_downloaded = 0

    for journal_name in journals:
        print(f"\n{'─' * 60}")
        print(f"Journal: {journal_name}")

        source_id, canonical = resolve_source_id(journal_name)
        if not source_id:
            print(f"  ✗ Could not find '{journal_name}' in OpenAlex — skipping.")
            continue
        print(f"  Source ID: {source_id}  ({canonical})")
        time.sleep(DELAY)

        works = fetch_works(source_id, per_journal)
        print(f"  {len(works)} papers with PDF URLs found.")
        time.sleep(DELAY)

        journal_dir = os.path.join(out_dir, safe_filename(canonical))
        os.makedirs(journal_dir, exist_ok=True)

        for work in works:
            doi       = (work.get("doi") or "").replace("https://doi.org/", "")
            title     = work.get("title") or ""
            year      = work.get("publication_year")
            # prefer best_oa_location, fall back to any location with a pdf_url
            oa_loc    = work.get("best_oa_location") or {}
            pdf_url   = oa_loc.get("pdf_url") or ""
            if not pdf_url:
                for loc in (work.get("locations") or []):
                    if loc.get("pdf_url"):
                        pdf_url = loc["pdf_url"]
                        break
            biblio    = work.get("biblio") or {}
            volume    = biblio.get("volume")
            issue     = biblio.get("issue")
            fpage     = biblio.get("first_page")
            lpage     = biblio.get("last_page")
            pages     = f"{fpage}-{lpage}" if fpage and lpage else fpage

            authors = []
            for a in (work.get("authorships") or [])[:5]:
                name = (a.get("author") or {}).get("display_name", "")
                if name:
                    authors.append(name)
            authors_str = "; ".join(authors)

            # filename: doi with slashes replaced
            fname       = safe_filename(doi or work["id"].split("/")[-1]) + ".pdf"
            local_path  = os.path.join(journal_dir, fname)
            openalex_id = work.get("id", "").split("/")[-1]

            downloaded = False
            if pdf_url and not os.path.exists(local_path):
                downloaded = download_pdf(pdf_url, local_path)
                time.sleep(DELAY)
            elif os.path.exists(local_path):
                downloaded = True   # already have it

            status = "✓" if downloaded else "✗"
            print(f"  [{status}] {title[:65]}{'…' if len(title) > 65 else ''}")
            if downloaded:
                total_downloaded += 1

            writer.writerow({
                "journal":     canonical,
                "openalex_id": openalex_id,
                "doi":         doi,
                "year":        year,
                "title":       title,
                "authors":     authors_str,
                "volume":      volume,
                "issue":       issue,
                "pages":       pages,
                "pdf_url":     pdf_url,
                "local_path":  local_path if downloaded else "",
                "downloaded":  downloaded,
            })

    csv_file.close()
    print(f"\n{'═' * 60}")
    print(f"Done.  {total_downloaded} PDFs downloaded to {out_dir}/")
    print(f"Metadata saved to {csv_path}")


if __name__ == "__main__":
    main()
