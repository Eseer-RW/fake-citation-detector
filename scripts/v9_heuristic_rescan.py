#!/usr/bin/env python3
"""
v9_heuristic_rescan.py — apply the heuristic non-academic filter to v9 papers.

For each paper in results_v9.jsonl:
  1. Fetch reference list via Crossref fast path (skip if unavailable)
  2. Run Phase 2.5 (local Crossref SQLite)
  3. Apply heuristic non-academic filter
  4. Write a patch record: {doi, not_found_academic, heuristic_filtered, ...}

Papers where Crossref has no reference list are skipped (can't re-verify
without Solr or re-running GROBID).

Output: results_v9_heuristic_patch.jsonl
Final: merge with results_v9.jsonl → results_v9_cleaned.jsonl

Usage:
    python3 v9_heuristic_rescan.py [--workers N] [--out results_v9_heuristic_patch.jsonl]
"""
from __future__ import annotations
import argparse, json, pathlib, re, sys, time, types
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, str(pathlib.Path(__file__).parent))

CROSSREF_API    = "https://api.crossref.org/works"
CROSSREF_EMAIL  = "rwang@insilicom.com"
CROSSREF_TIMEOUT = 15
V9_PATH   = pathlib.Path("/home/rwang/cross_year_study/results_v9.jsonl")
OUT_PATH  = pathlib.Path("/home/rwang/cross_year_study/results_v9_heuristic_patch.jsonl")
MERGE_OUT = pathlib.Path("/home/rwang/cross_year_study/results_v9_cleaned.jsonl")

# ── Heuristic filter (same as in batch_verify_years.py Phase 5) ──────────────
_H_URL_RE    = re.compile(r'https?://\S{10,}', re.I)
_H_ACCESS_RE = re.compile(
    r'\b(accessed|retrieved|last\s+visited|last\s+access|available\s+at'
    r'|available\s+from|online\s+at|available\s+online)\b', re.I)
_H_NONACAD_RE = re.compile(
    r'\b(wikipedia\.org|github\.com|github\.io|stackoverflow\.com'
    r'|medium\.com|twitter\.com|youtube\.com|reddit\.com'
    r'|cdc\.gov|who\.int|fda\.gov|cms\.gov|hhs\.gov'
    r'|ourworldindata\.org|statista\.com)\b', re.I)

def is_likely_nonacademic(ref) -> bool:
    if ref.doi: return False
    raw = ref.raw or ''
    if _H_URL_RE.search(raw) and _H_ACCESS_RE.search(raw) and not ref.title: return True
    if _H_NONACAD_RE.search(raw): return True
    if _H_ACCESS_RE.search(raw) and not ref.title: return True
    if len(raw.strip()) < 15 and not ref.title: return True
    return False

# ── Crossref fast path ────────────────────────────────────────────────────────
def crossref_refs(doi: str):
    """Return list of ref SimpleNamespace objects or None if no ref list."""
    url = f"{CROSSREF_API}/{doi}"
    try:
        resp = requests.get(url,
            headers={"User-Agent": f"FakeCitationDetector/1.0 (mailto:{CROSSREF_EMAIL})"},
            timeout=CROSSREF_TIMEOUT)
        if resp.status_code != 200:
            return None
        data = resp.json().get("message", {})
        raw_refs = data.get("reference", [])
        if not raw_refs:
            return None
    except Exception:
        return None

    refs = []
    for r in raw_refs:
        obj = types.SimpleNamespace()
        obj.doi   = r.get("DOI", "").strip().lower() or None
        obj.title = (r.get("article-title") or r.get("volume-title") or "").strip() or None
        obj.year  = None
        yr = r.get("year", "")
        m  = re.search(r'\b(1[89]\d{2}|20[012]\d)\b', str(yr))
        if m: obj.year = int(m.group(1))
        obj.raw   = r.get("unstructured", "") or ""
        refs.append(obj)
    return refs if refs else None

# ── Phase 2.5: Crossref SQLite ────────────────────────────────────────────────
def phase25_verify(refs):
    """Try local Crossref SQLite for all refs. Returns list of bools (found?)."""
    try:
        from crossref_lookup import batch_crossref
        results_cr = batch_crossref(refs)
        return [r.found for r in results_cr]
    except Exception:
        return [False] * len(refs)

# ── Per-paper processing ──────────────────────────────────────────────────────
def process_paper(paper: dict) -> dict | None:
    doi = paper["doi"]

    refs = crossref_refs(doi)
    if refs is None:
        return None   # No Crossref reference list — skip

    n = len(refs)
    if n == 0:
        return None

    # Phase 2.5: Crossref SQLite verification
    found_flags = phase25_verify(refs)

    # Phase 5: Heuristic filter on still-unmatched refs
    unmatched_indices = [i for i, f in enumerate(found_flags) if not f]
    found_count       = sum(found_flags)
    not_found_count   = n - found_count
    heuristic_filtered = sum(
        1 for i in unmatched_indices if is_likely_nonacademic(refs[i])
    )
    not_found_academic = not_found_count - heuristic_filtered

    return {
        "doi":                doi,
        "year":               paper["year"],
        "field":              paper["field"],
        "total_refs":         n,
        "found_phase25":      found_count,
        "not_found":          not_found_count,
        "heuristic_filtered": heuristic_filtered,
        "not_found_academic": not_found_academic,
    }


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=str(OUT_PATH))
    ap.add_argument("--merge", action="store_true",
                    help="After scan, merge patch into v9 and write results_v9_cleaned.jsonl")
    args = ap.parse_args()

    out_path = pathlib.Path(args.out)

    # Load v9 papers
    v9_rows = [json.loads(l) for l in V9_PATH.open()
               if json.loads(l).get("status") == "ok"
               and json.loads(l).get("total", 0) > 0]
    print(f"Loaded {len(v9_rows):,} v9 papers to re-scan")

    # Skip already-done DOIs
    done = set()
    if out_path.exists():
        for l in out_path.open():
            try: done.add(json.loads(l)["doi"])
            except Exception: pass
    pending = [r for r in v9_rows if r["doi"] not in done]
    print(f"Already done: {len(done):,}  |  Remaining: {len(pending):,}")

    # Process
    skipped = found_cr = 0
    start = time.time()

    with out_path.open("a") as fh, ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process_paper, p): p for p in pending}
        from concurrent.futures import as_completed
        for i, fut in enumerate(as_completed(futures), 1):
            result = fut.result()
            if result is None:
                skipped += 1
            else:
                found_cr += 1
                fh.write(json.dumps(result) + "\n")
                fh.flush()

            if i % 500 == 0:
                elapsed = time.time() - start
                rate = i / elapsed
                eta  = (len(pending) - i) / rate if rate > 0 else 0
                print(f"  [{i:>6}/{len(pending):>6}] "
                      f"found_crossref={found_cr} skipped={skipped}  "
                      f"{rate:.1f}/s  ETA {eta/60:.0f}m", flush=True)

    print(f"\nDone. Crossref refs found: {found_cr:,}  |  Skipped (no ref list): {skipped:,}")
    elapsed = time.time() - start
    print(f"Elapsed: {elapsed/60:.1f} min")

    if args.merge:
        _merge(out_path)


def _merge(patch_path: pathlib.Path):
    """Merge heuristic patch into v9 to produce results_v9_cleaned.jsonl."""
    # Build patch lookup: doi -> patch dict
    patch = {}
    for l in patch_path.open():
        r = json.loads(l)
        patch[r["doi"]] = r

    out_rows = []
    patched_count = 0
    for l in V9_PATH.open():
        r = json.loads(l)
        doi = r.get("doi", "")
        if doi in patch:
            p = patch[doi]
            r["heuristic_filtered"]  = p["heuristic_filtered"]
            r["not_found_academic"]  = p["not_found_academic"]
            r["rescan_total"]        = p["total_refs"]
            r["rescan_found_phase25"]= p["found_phase25"]
            patched_count += 1
        else:
            # Fallback: not_found_academic = not_found (no heuristic applied)
            r.setdefault("heuristic_filtered", 0)
            r.setdefault("not_found_academic", r.get("not_found", 0))
        out_rows.append(r)

    MERGE_OUT.write_text("\n".join(json.dumps(r) for r in out_rows) + "\n")
    print(f"\nMerged: {patched_count:,} papers patched → {MERGE_OUT}")
    print(f"  {len(out_rows) - patched_count:,} papers kept original not_found as fallback")


if __name__ == "__main__":
    main()
