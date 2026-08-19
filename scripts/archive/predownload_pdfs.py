"""
predownload_pdfs.py — download all PDFs from one or more manifests in parallel.

Usage:
    python3 predownload_pdfs.py manifest1.jsonl [manifest2.jsonl ...]

Saves PDFs to the same DOWNLOAD_DIR used by batch_verify_years.py so the
verification jobs find them already cached and skip the download step.
"""
import json, pathlib, sys, time, requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import re

DOWNLOAD_DIR = pathlib.Path("/home/rwang/cross_year_study/pdfs")
DOWNLOAD_TIMEOUT = 30
MAX_WORKERS = 40

DL_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "application/pdf,*/*",
}

_ELIFE_ID_RE = re.compile(r'/elife[.-](\d+)', re.I)
_IEEE_ARNO_RE = re.compile(r'/0*(\d{6,})(?:\.pdf)?$')

def candidate_urls(doi: str, url: str) -> list:
    if "10.1371/" in doi:
        return [f"https://journals.plos.org/plosone/article/file?id={doi}&type=printable"]
    if "10.7554/" in doi:
        m = _ELIFE_ID_RE.search(doi)
        if m:
            aid = m.group(1)
            return [f"https://cdn.elifesciences.org/articles/{aid}/elife-{aid}-v{v}.pdf"
                    for v in range(1, 5)]
    if "ieeexplore.ieee.org" in url:
        m = _IEEE_ARNO_RE.search(url)
        if m:
            return [f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={m.group(1)}"]
    return [url]

def download_one(paper: dict) -> tuple:
    doi   = paper["doi"]
    url   = paper["oa_url"]
    field = paper["field"]
    year  = paper["year"]

    safe_doi = doi.replace("/", "_").replace(".", "_")
    dest = DOWNLOAD_DIR / field / str(year) / safe_doi
    pdf_candidate = dest.with_suffix(".pdf")

    if pdf_candidate.exists() and pdf_candidate.stat().st_size > 5_000:
        return (doi, "cached")

    dest.parent.mkdir(parents=True, exist_ok=True)

    for try_url in candidate_urls(doi, url):
        try:
            resp = requests.get(try_url, headers=DL_HEADERS,
                                timeout=DOWNLOAD_TIMEOUT, allow_redirects=True)
            if resp.status_code == 200 and len(resp.content) > 5_000:
                ct = resp.headers.get("Content-Type", "")
                ext = ".pdf" if "pdf" in ct else ".html" if "html" in ct else ".pdf"
                out = dest.with_suffix(ext)
                out.write_bytes(resp.content)
                return (doi, "downloaded")
        except Exception:
            pass

    return (doi, "failed")

def main(manifest_paths):
    papers = []
    for mp in manifest_paths:
        papers.extend(json.loads(l) for l in open(mp) if l.strip())

    # Deduplicate by DOI
    seen, unique = set(), []
    for p in papers:
        if p["doi"] not in seen:
            seen.add(p["doi"]); unique.append(p)

    # Check which are already cached
    to_download = [p for p in unique if not (
        DOWNLOAD_DIR / p["field"] / str(p["year"]) /
        (p["doi"].replace("/","_").replace(".","_") + ".pdf")
    ).exists()]

    print(f"Total: {len(unique)} papers  |  already cached: {len(unique)-len(to_download)}  |  to download: {len(to_download)}")

    if not to_download:
        print("All PDFs already cached!")
        return

    t0 = time.time()
    cached = downloaded = failed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(download_one, p): p for p in to_download}
        done = 0
        for fut in as_completed(futures):
            doi, status = fut.result()
            done += 1
            if status == "cached":    cached += 1
            elif status == "downloaded": downloaded += 1
            else:                     failed += 1
            if done % 50 == 0 or done == len(to_download):
                elapsed = time.time() - t0
                rate = done / elapsed
                remaining = (len(to_download) - done) / rate if rate > 0 else 0
                print(f"  [{done}/{len(to_download)}] downloaded={downloaded} failed={failed} "
                      f"rate={rate:.1f}/s  ETA={remaining/60:.1f}min", flush=True)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.0f}s: downloaded={downloaded}  failed={failed}  cached={cached}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 predownload_pdfs.py manifest1.jsonl [manifest2.jsonl ...]")
        sys.exit(1)
    main(sys.argv[1:])
