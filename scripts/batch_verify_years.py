"""
batch_verify_years.py — download sampled papers and run citation verification.

Reads the manifest produced by sample_papers.py, downloads each PDF,
runs it through GROBID + 4-phase citation verification, and writes per-paper
results to a JSONL file. A separate aggregation step produces the comparison
table by year and field.

Usage:
    # Step 1: generate manifest
    python3 sample_papers.py --n 10 --out manifest.jsonl

    # Step 2: download + verify (can be restarted — skips already-done papers)
    python3 batch_verify_years.py manifest.jsonl --out results.jsonl

    # Step 3: print comparison table
    python3 batch_verify_years.py --summarise results.jsonl
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
import types
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import numpy as np
import requests

# ── Config ────────────────────────────────────────────────────────────────────
GROBID_URL     = "http://localhost:8070/api/processFulltextDocument"
DOWNLOAD_DIR   = pathlib.Path("/home/rwang/cross_year_study/pdfs")
GROBID_TIMEOUT = 120
DOWNLOAD_TIMEOUT = 60

_NS = {"tei": "http://www.tei-c.org/ns/1.0"}
_DOI_RE  = re.compile(r'(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,}/[^\s,;)\]>]+)', re.I)
_YEAR_RE = re.compile(r'\b(1[89]\d{2}|20[012]\d)\b')

DL_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0.0.0 Safari/537.36"),
    "Accept": "application/pdf,*/*",
}

# ── TEI parsing (copied from verify_pdf.py) ──────────────────────────────────

def _tei_text(el, xpath):
    found = el.find(xpath, _NS)
    return found.text.strip() if found is not None and found.text else None

def _tei_doi(ref):
    for idno in ref.findall(".//tei:idno", _NS):
        if (idno.get("type") or "").upper() == "DOI" and idno.text:
            doi = idno.text.strip().lower()
            doi = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', doi)
            return doi.rstrip(".,;)")
    return None

def _tei_year(ref):
    date = ref.find(".//tei:date[@type='published']", _NS)
    if date is not None:
        when = date.get("when") or date.text or ""
        m = _YEAR_RE.search(when)
        if m: return int(m.group(1))
    return None

def _tei_title(ref):
    for xpath in (".//tei:analytic/tei:title[@level='a']",
                  ".//tei:analytic/tei:title",
                  ".//tei:monogr/tei:title[@level='j']",
                  ".//tei:monogr/tei:title"):
        t = _tei_text(ref, xpath)
        if t and len(t.split()) >= 2:
            return t
    return None

def parse_tei_refs(tei_xml: str) -> list:
    try:
        root = ET.fromstring(tei_xml)
    except ET.ParseError:
        return []
    refs = []
    for bib in root.findall(".//tei:listBibl/tei:biblStruct", _NS):
        obj = types.SimpleNamespace()
        raw_note = bib.find(".//tei:note[@type='raw_reference']", _NS)
        obj.raw   = raw_note.text.strip() if raw_note is not None and raw_note.text else ""
        obj.doi   = _tei_doi(bib)
        obj.year  = _tei_year(bib)
        obj.title = _tei_title(bib)
        if not obj.doi and obj.raw:
            m = _DOI_RE.search(obj.raw)
            if m: obj.doi = m.group(1).rstrip(".,;)").lower()
        refs.append(obj)
    return refs


# ── Download ─────────────────────────────────────────────────────────────────

_ELIFE_ID_RE   = re.compile(r'elife[./](\d+)', re.I)
_IEEE_ARNO_RE  = re.compile(r'/0*(\d{6,})(?:\.pdf)?$')

def _candidate_urls(doi: str, url: str) -> list[str]:
    """Return ordered list of URLs to try, with publisher-specific fixes applied."""
    # PLOS ONE: always use the printable PDF endpoint
    if "10.1371/" in doi:
        return [f"https://journals.plos.org/plosone/article/file?id={doi}&type=printable"]

    # eLife: CDN versioned PDF (try v1 → v4)
    if "10.7554/" in doi:
        m = _ELIFE_ID_RE.search(doi)
        if m:
            aid = m.group(1)
            return [f"https://cdn.elifesciences.org/articles/{aid}/elife-{aid}-v{v}.pdf"
                    for v in range(1, 5)]

    # IEEE Access: strip the leading zero from the article number
    if "ieeexplore.ieee.org" in url:
        m = _IEEE_ARNO_RE.search(url)
        if m:
            return [f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={m.group(1)}"]

    return [url]


def download_pdf(doi: str, url: str, dest: pathlib.Path) -> Optional[pathlib.Path]:
    """Download a PDF; return path on success, None on failure. Skips if exists."""
    # Only treat an existing PDF as "done"; HTML/XML from a failed first attempt
    # should not block a retry with a better URL.
    pdf_candidate = dest.with_suffix(".pdf")
    if pdf_candidate.exists() and pdf_candidate.stat().st_size > 5_000:
        return pdf_candidate

    for try_url in _candidate_urls(doi, url):
        try:
            resp = requests.get(try_url, headers=DL_HEADERS, timeout=DOWNLOAD_TIMEOUT,
                                allow_redirects=True)
            if resp.status_code == 200 and len(resp.content) > 5_000:
                ct = resp.headers.get("Content-Type", "")
                ext = ".pdf" if "pdf" in ct else ".html" if "html" in ct else ".pdf"
                fpath = dest.with_suffix(ext)
                fpath.write_bytes(resp.content)
                return fpath
        except Exception:
            pass
    return None


# ── GROBID ───────────────────────────────────────────────────────────────────

def grobid_process(pdf_path: pathlib.Path) -> Optional[str]:
    try:
        with pdf_path.open("rb") as fh:
            resp = requests.post(
                GROBID_URL,
                files={"input": (pdf_path.name, fh, "application/pdf")},
                data={"consolidateHeader": "0", "consolidateCitations": "0",
                      "includeRawCitations": "1"},
                timeout=GROBID_TIMEOUT,
            )
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None


# ── Verification ─────────────────────────────────────────────────────────────

def verify_refs(refs: list, solr, vector_lookup=None) -> dict:
    """Run 4-phase verification; return summary counts dict."""
    from solr_lookup import MatchMethod, SolrResult

    n = len(refs)
    results = [None] * n

    # Phase 1: DOI
    for i, ref in enumerate(refs):
        if ref.doi:
            r = solr.by_doi(ref.doi)
            if r.found: results[i] = r

    # Phase 2: batch title
    not_yet = [i for i in range(n) if results[i] is None]
    titled  = [i for i in not_yet if refs[i].title]
    if titled:
        batch_out = solr.by_title_batch([refs[i] for i in titled])
        for j, r in enumerate(batch_out):
            if r.found: results[titled[j]] = r

    # Phase 3: concurrent Solr individual fallback, then concurrent Crossref burst
    phase3_idx = [i for i in range(n) if results[i] is None]
    if phase3_idx:
        with ThreadPoolExecutor(max_workers=min(4, len(phase3_idx))) as pool:
            p3_sol = list(pool.map(solr.by_citation, [refs[i] for i in phase3_idx]))
        for i, r in zip(phase3_idx, p3_sol):
            if r.found:
                results[i] = r

    crossref_idx = [
        i for i in range(n)
        if results[i] is None and (refs[i].doi or refs[i].title)
    ]
    if crossref_idx:
        try:
            from crossref_lookup import batch_crossref
            xr_results = batch_crossref([refs[i] for i in crossref_idx])
            for i, r in zip(crossref_idx, xr_results):
                if r.found:
                    results[i] = r
        except Exception as exc:
            print(f"    [crossref-batch] failed: {exc}")

    # Phase 4: vector
    vec_found = vec_total = 0
    if vector_lookup:
        vec_candidates = [i for i in range(n) if results[i] is None and refs[i].title]
        vec_total = len(vec_candidates)
        for i in vec_candidates:
            r = vector_lookup.by_title(refs[i].title, year=refs[i].year)
            if r.found:
                results[i] = r
                vec_found += 1

    not_found_sentinel = SolrResult(found=False, method=MatchMethod.NOT_FOUND)
    final = [results[i] or not_found_sentinel for i in range(n)]

    found = sum(1 for r in final if r.found)
    by_method: dict[str, int] = {}
    for r in final:
        k = r.method.value
        by_method[k] = by_method.get(k, 0) + 1

    return {
        "total":      n,
        "found":      found,
        "not_found":  n - found,
        "vec_tried":  vec_total,
        "vec_found":  vec_found,
        "by_method":  by_method,
    }


# ── Main loop ────────────────────────────────────────────────────────────────

def run_pipeline(manifest_path: str, out_path: str, no_vector: bool = False, workers: int = 1):
    manifest = [json.loads(l) for l in pathlib.Path(manifest_path).open()]
    out_file  = pathlib.Path(out_path)
    done_dois = set()
    if out_file.exists():
        for l in out_file.open():
            try: done_dois.add(json.loads(l)["doi"])
            except Exception: pass

    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from solr_lookup import SolrLookup
    solr = SolrLookup()

    vector_lookup = None
    if not no_vector:
        try:
            from vector_lookup import VectorLookup, _get_model
            vector_lookup = VectorLookup()
            _get_model()  # warm up
            print("Vector model loaded.")
        except ImportError:
            print("sentence-transformers not available — skipping Phase 4.")

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    total = len(manifest)
    pending = [(idx, p) for idx, p in enumerate(manifest, 1)
               if p["doi"] not in done_dois]

    def _process_paper(args):
        idx, paper = args
        doi   = paper["doi"]
        url   = paper["oa_url"]
        field = paper["field"]
        year  = paper["year"]

        print(f"[{idx}/{total}] {paper['journal_name']} {year}  {doi[:50]}", flush=True)

        safe_doi = doi.replace("/", "_").replace(".", "_")
        dest = DOWNLOAD_DIR / field / str(year) / safe_doi
        dest.parent.mkdir(parents=True, exist_ok=True)

        pdf_path = download_pdf(doi, url, dest)
        if pdf_path is None:
            print(f"  ✗ download failed")
            return {**paper, "status": "download_failed", "total": 0, "found": 0, "not_found": 0}

        if pdf_path.suffix != ".pdf":
            print(f"  ✗ not a PDF ({pdf_path.suffix}) — skipping GROBID")
            return {**paper, "status": "not_pdf", "total": 0, "found": 0, "not_found": 0}

        tei = grobid_process(pdf_path)
        if not tei:
            print(f"  ✗ GROBID failed")
            return {**paper, "status": "grobid_failed", "total": 0, "found": 0, "not_found": 0}

        refs = parse_tei_refs(tei)
        if not refs:
            print(f"  ✗ no references extracted")
            return {**paper, "status": "no_refs", "total": 0, "found": 0, "not_found": 0}

        counts = verify_refs(refs, solr, vector_lookup)
        pct = 100 * counts["found"] / counts["total"] if counts["total"] else 0
        print(f"  ✓ {counts['total']} refs  found={counts['found']} ({pct:.0f}%)  "
              f"not_found={counts['not_found']}")
        return {**paper, "status": "ok", **counts}

    with out_file.open("a") as out_fh:
        if workers == 1:
            for args in pending:
                row = _process_paper(args)
                out_fh.write(json.dumps(row) + "\n"); out_fh.flush()
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for row in pool.map(_process_paper, pending):
                    out_fh.write(json.dumps(row) + "\n"); out_fh.flush()


# ── Summarise ────────────────────────────────────────────────────────────────

def summarise(results_path: str):
    rows = [json.loads(l) for l in pathlib.Path(results_path).open()
            if json.loads(l).get("status") == "ok" and json.loads(l).get("total", 0) > 0]

    # Aggregate by year and field
    from collections import defaultdict
    by_year:  dict = defaultdict(lambda: {"papers": 0, "total": 0, "not_found": 0})
    by_field: dict = defaultdict(lambda: {"papers": 0, "total": 0, "not_found": 0})
    matrix:   dict = defaultdict(lambda: {"papers": 0, "total": 0, "not_found": 0})

    for r in rows:
        year  = r["year"]
        field = r["field"]
        total = r["total"]
        nf    = r["not_found"]

        for d in (by_year[year], by_field[field], matrix[(year, field)]):
            d["papers"] += 1; d["total"] += total; d["not_found"] += nf

    # ── By year ──
    print("\n" + "=" * 60)
    print("NOT-FOUND RATE BY YEAR (all fields combined)")
    print("=" * 60)
    print(f"  {'Year':<6} {'Papers':>7} {'Citations':>10} {'Not found':>10} {'Rate':>7}")
    print("  " + "-" * 45)
    for year in sorted(by_year):
        d = by_year[year]
        rate = 100 * d["not_found"] / d["total"] if d["total"] else 0
        print(f"  {year:<6} {d['papers']:>7} {d['total']:>10,} {d['not_found']:>10,} {rate:>6.1f}%")

    # ── By field ──
    print("\n" + "=" * 60)
    print("NOT-FOUND RATE BY FIELD (all years combined)")
    print("=" * 60)
    print(f"  {'Field':<25} {'Papers':>7} {'Citations':>10} {'Not found':>10} {'Rate':>7}")
    print("  " + "-" * 55)
    for field in sorted(by_field):
        d = by_field[field]
        rate = 100 * d["not_found"] / d["total"] if d["total"] else 0
        print(f"  {field:<25} {d['papers']:>7} {d['total']:>10,} {d['not_found']:>10,} {rate:>6.1f}%")

    # ── Matrix: year × field ──
    fields = sorted(by_field.keys())
    years  = sorted(by_year.keys())
    print("\n" + "=" * 70)
    print("NOT-FOUND RATE MATRIX: year (rows) × field (columns)")
    print("=" * 70)
    header = f"  {'Year':<6}" + "".join(f" {f[:12]:>13}" for f in fields)
    print(header)
    print("  " + "-" * (6 + 13 * len(fields)))
    for year in years:
        row = f"  {year:<6}"
        for field in fields:
            d = matrix.get((year, field))
            if d and d["total"] > 0:
                rate = 100 * d["not_found"] / d["total"]
                row += f" {rate:>12.1f}%"
            else:
                row += f" {'—':>13}"
        print(row)

    print(f"\n  Total papers in analysis: {sum(d['papers'] for d in by_year.values())}")
    print(f"  Total citations:          {sum(d['total'] for d in by_year.values()):,}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest", nargs="?", default="",
                    help="JSONL manifest from sample_papers.py")
    ap.add_argument("--out",        default="cross_year_results.jsonl")
    ap.add_argument("--no-vector",  action="store_true")
    ap.add_argument("--workers",   type=int, default=1,
                    help="Papers to process concurrently (default 1)")
    ap.add_argument("--summarise",  metavar="RESULTS_JSONL",
                    help="Skip download/verify; just print summary table from existing results.")
    args = ap.parse_args()

    if args.summarise:
        summarise(args.summarise)
        return

    if not args.manifest:
        ap.print_help()
        sys.exit(1)

    run_pipeline(args.manifest, args.out, no_vector=args.no_vector), workers=args.workers)
    print("\nDone. Run with --summarise to print comparison tables.")


if __name__ == "__main__":
    main()
