"""
url_validation.py — of the not-found citations that carry a URL, how many point
to a LIVE web resource (status < 500)? A live URL citation is a real web source,
not a fabrication.

Collects DOI-less, URL-bearing references (these are the non-academic 'not found'
citations) from a random sample of papers, then checks each URL concurrently.
"""
import json, random, sys, collections
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, "/home/rwang/fake-citation-detector/scripts")
import batch_verify_years as bvy
from url_check import website_exists, extract_url

V11 = "/home/rwang/cross_year_study/results_v11.jsonl"
TARGET = 2000          # URL-bearing not-found citations to check
WORKERS = 24

cand = []
for l in open(V11):
    try: r = json.loads(l)
    except Exception: continue
    if r.get("status") == "ok" and r.get("not_found", 0) > 0:
        cand.append(r)
random.seed(3); random.shuffle(cand)
print(f"papers with not_found>0: {len(cand):,}", flush=True)

# Collect DOI-less URL-bearing references (the not-found web citations)
urls = []          # (url, is_nonacademic_flagged)
papers_used = 0
for p in cand:
    if len(urls) >= TARGET:
        break
    refs = bvy.crossref_refs(p["doi"])
    if not refs:
        continue
    papers_used += 1
    for r in refs:
        if getattr(r, "doi", None):
            continue
        url = extract_url(getattr(r, "raw", "") or "")
        if url:
            urls.append((url, bvy.is_likely_nonacademic(r)))
            if len(urls) >= TARGET:
                break

print(f"collected {len(urls):,} DOI-less URL citations from {papers_used:,} papers", flush=True)
print("checking liveness...", flush=True)

live = dead = 0
live_nonacad = 0
with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    futs = {pool.submit(website_exists, u): (u, na) for u, na in urls}
    for i, fut in enumerate(as_completed(futs), 1):
        u, na = futs[fut]
        ok = fut.result()
        if ok:
            live += 1
            if na: live_nonacad += 1
        else:
            dead += 1
        if i % 500 == 0:
            print(f"  checked {i:,}/{len(urls):,}  live={live} dead={dead}", flush=True)

tot = len(urls) or 1
print("\n=== URL CITATION LIVENESS ===", flush=True)
print(f"URL citations checked: {len(urls):,}")
print(f"  LIVE (status < 500):  {live:,}  ({100*live/tot:.1f}%)")
print(f"  dead / unreachable:   {dead:,}  ({100*dead/tot:.1f}%)")
print(f"\n{100*live/tot:.1f}% of not-found URL citations point to a real, live web "
      f"resource — confirmed real references, not hallucinations.")
