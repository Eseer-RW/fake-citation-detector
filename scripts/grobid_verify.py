"""
grobid_verify.py — verify GROBID-parsed citations against OpenAlex (Solr) or Crossref (MongoDB).

Reads all cited_sent JSON files produced by the GROBID pipeline (step2_tei_to_json)
and looks up each citation.

Default: uses the OpenAlex Solr index (492M works, no index build required) for both
DOI and title+year matching. Results are written incrementally after each paper.

Usage (from scripts/ directory):
    python3 grobid_verify.py                         # Solr, full matching (DOI + title)
    python3 grobid_verify.py --paper jama            # one paper (substring match)
    python3 grobid_verify.py --out results.txt       # save output (written per paper)
    python3 grobid_verify.py --show-found            # also print matched citations
    python3 grobid_verify.py --backend mongo         # use MongoDB (needs text index)
    python3 grobid_verify.py --backend mongo --doi-only  # MongoDB, DOI-only
    python3 grobid_verify.py --no-crossref               # skip Crossref fallback
"""
import argparse
import json
import pathlib
import re
import sys
import types
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from threading import Lock

sys.path.insert(0, str(pathlib.Path(__file__).parent))

CITED_SENT_DIR = pathlib.Path(__file__).parent.parent / \
    "grobid/grobid_pdf_to_json/step2_tei_to_json/out/cited_sent"

_DOI_RE = re.compile(
    r'(?:https?://(?:dx\.)?doi\.org/|doi:\s*)(10\.\d{4,}/\S+)',
    re.IGNORECASE,
)


def _normalize_doi(doi: str) -> str:
    doi = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', doi.strip(), flags=re.I)
    return doi.lower()


def extract_doi_from_raw(citation_str: str) -> str | None:
    m = _DOI_RE.search(citation_str)
    if m:
        doi = m.group(1).rstrip('.,)')
        return _normalize_doi(doi)
    return None


def make_citation_obj(entry: dict):
    obj = types.SimpleNamespace()
    doi = entry.get("doi") or extract_doi_from_raw(entry.get("citation", ""))
    obj.doi   = doi or None
    obj.title = entry.get("title") or None
    year_raw  = entry.get("year")
    try:
        obj.year = int(year_raw) if year_raw else None
    except (ValueError, TypeError):
        obj.year = None
    return obj


def validate_metadata(cited_year, cited_journal, db_year, db_journal) -> list[str]:
    """Compare cited year/journal against database values; return list of discrepancy strings."""
    def _clean(s) -> str:
        s = (s or "").strip()
        return "" if s == "—" else s

    issues = []

    # Year check: allow ±1 (online-first vs print year)
    if cited_year and db_year:
        try:
            diff = abs(int(cited_year) - int(db_year))
            if diff > 1:
                issues.append(f"year off by {diff} (cited {cited_year}, db {db_year})")
        except (ValueError, TypeError):
            pass

    # Journal check: skip if either side is blank
    cited_j = _clean(cited_journal)
    db_j    = _clean(db_journal)
    if cited_j and db_j:
        sim = SequenceMatcher(None, cited_j.lower(), db_j.lower()).ratio()
        if sim < 0.7:
            issues.append(
                f"journal mismatch (cited '{cited_j}', db '{db_j}', sim {sim:.2f})"
            )

    return issues


def _lookup_one(entry, lookup, doi_only, crossref, cache, cache_lock):
    """Look up a single citation entry. Thread-safe via cache_lock."""
    obj = make_citation_obj(entry)
    cache_key = (obj.doi, obj.title, obj.year)

    with cache_lock:
        if cache_key in cache:
            return cache[cache_key]

    if doi_only:
        from mongo_lookup import LookupResult, MatchMethod
        result = lookup.by_doi(obj.doi) if obj.doi else \
                 LookupResult(found=False, method=MatchMethod.NOT_FOUND)
    else:
        result = lookup.by_citation(obj)

    if not result.found and crossref is not None:
        result = crossref.by_citation(obj)

    with cache_lock:
        cache[cache_key] = result
    return result


def _build_row(entry, result, paper_name):
    """Build result dict from a citation entry and its lookup result."""
    row = {
        "paper":      paper_name,
        "title":      entry.get("title", "—"),
        "year":       entry.get("year", "—"),
        "journal":    entry.get("journal", "—"),
        "found":      result.found,
        "method":     result.method.value,
        "confidence": result.confidence,
        "sentences":  entry.get("sentences", []),
    }
    if result.found and result.record:
        rec = result.record
        raw_title = rec.get("title") or rec.get("title_s")
        if isinstance(raw_title, list):
            raw_title = raw_title[0] if raw_title else ""
        row["db_title"] = raw_title or ""
        db_year = (
            rec.get("publication_year") or
            rec.get("published-print", {}).get("date-parts", [[None]])[0][0]
        )
        if not db_year:
            pub_date = rec.get("publication_date") or rec.get("created", {}).get("date-time", "")
            if pub_date and len(str(pub_date)) >= 4:
                try:
                    db_year = int(str(pub_date)[:4])
                except ValueError:
                    pass
        row["db_year"] = db_year
        pl = rec.get("primary_location")
        if isinstance(pl, dict):
            row["db_journal"] = pl.get("source", {}).get("display_name", "")
        else:
            ct = rec.get("container-title", [""])
            row["db_journal"] = ct[0] if isinstance(ct, list) else ct

    discrepancies = []
    if result.found:
        discrepancies = validate_metadata(
            row.get("year"), row.get("journal"),
            row.get("db_year"), row.get("db_journal"),
        )
        row["status"] = "FOUND_MISMATCH" if discrepancies else "FOUND"
    else:
        row["status"] = "NOT_FOUND"
    row["discrepancies"] = discrepancies
    return row


def verify_paper(json_path: pathlib.Path, lookup, doi_only: bool = False,
                 verbose: bool = True, crossref=None, workers: int = 4,
                 cache: dict = None, cache_lock=None):
    """Verify all citations in one cited_sent JSON file. Returns list of result dicts.

    Citations are looked up in parallel (up to `workers` threads) since each lookup
    is an independent network request to Solr. Repeat citations are served from a
    cache (per-paper by default; pass a shared cache+lock for cross-paper reuse).
    """
    citations = json.loads(json_path.read_text())
    paper_name = json_path.stem.replace(".tei", "")

    if cache is None:
        cache = {}
    if cache_lock is None:
        cache_lock = Lock()
    results = [None] * len(citations)
    completed = [0]
    print_lock = Lock()

    def process(idx_entry):
        idx, entry = idx_entry
        result = _lookup_one(entry, lookup, doi_only, crossref, cache, cache_lock)
        row = _build_row(entry, result, paper_name)
        results[idx] = row
        with print_lock:
            completed[0] += 1
            if verbose and completed[0] % 10 == 0:
                found_so_far = sum(1 for r in results if r and r["status"] == "FOUND")
                print(f"    {completed[0]}/{len(citations)} citations checked  "
                      f"({found_so_far} found so far)", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(process, enumerate(citations)))

    if verbose:
        found     = sum(1 for r in results if r["status"] == "FOUND")
        mismatch  = sum(1 for r in results if r["status"] == "FOUND_MISMATCH")
        not_found = sum(1 for r in results if r["status"] == "NOT_FOUND")
        print(f"\n{'='*70}")
        print(f"Paper: {paper_name}  ({len(citations)} citations)")
        print(f"  Found: {found}  |  Mismatch: {mismatch}  |  Not found: {not_found}")
        if mismatch:
            print(f"\n  ⚠  FOUND_MISMATCH ({mismatch}):")
            for r in results:
                if r["status"] == "FOUND_MISMATCH":
                    print(f"    [{r['year']}] {r['title'][:80]}")
                    for issue in r["discrepancies"]:
                        print(f"         → {issue}")
        if not_found:
            print(f"\n  ⚠  NOT FOUND ({not_found}):")
            for r in results:
                if r["status"] == "NOT_FOUND":
                    print(f"    [{r['year']}] {r['title'][:80]}")
                    if r["sentences"]:
                        print(f"         → \"{r['sentences'][0][:100]}\"")

    return results


def _format_rows(results: list, show_found: bool) -> list[str]:
    lines = []
    for r in results:
        status = r["status"]
        lines.append(
            f"{status:<16} [{r['method']:<12}] conf={r['confidence']:.2f} "
            f"| {r['paper'][:30]:<30} | [{r['year']}] {r['title'][:60]}"
        )
        if status == "FOUND_MISMATCH":
            for issue in r.get("discrepancies", []):
                lines.append(f"           ⚠ {issue}")
        elif status == "FOUND" and show_found:
            lines.append(
                f"           db:   {r.get('db_title', '')[:60]}"
                f" ({r.get('db_year', '')})"
            )
        elif status == "NOT_FOUND" and r["sentences"]:
            lines.append(
                f"           cited in: \"{r['sentences'][0][:80]}\""
            )
    return lines


def main():
    ap = argparse.ArgumentParser(
        description="Verify GROBID citations via OpenAlex (Solr) or Crossref (MongoDB)")
    ap.add_argument("--paper", default=None,
                    help="filter to papers whose filename contains this substring")
    ap.add_argument("--out", default=None,
                    help="write results to this file (appended per paper as each finishes)")
    ap.add_argument("--show-found", action="store_true",
                    help="also print/write successfully matched citations")
    ap.add_argument("--backend", choices=["solr", "mongo"], default="solr",
                    help="lookup backend: 'solr' (default, OpenAlex) or 'mongo' (Crossref)")
    ap.add_argument("--doi-only", action="store_true",
                    help="only use DOI lookup (mongo backend only)")
    ap.add_argument("--cited-sent-dir", default=None,
                    help="path to cited_sent JSON directory (overrides default)")
    ap.add_argument("--no-crossref", action="store_true",
                    help="disable Crossref fallback (Solr backend only, fallback is on by default)")
    ap.add_argument("--workers", type=int, default=4,
                    help="parallel lookup threads per paper (default 4, set to 1 to disable)")
    args = ap.parse_args()

    cited_dir = pathlib.Path(args.cited_sent_dir) if args.cited_sent_dir else CITED_SENT_DIR
    json_files = sorted(cited_dir.glob("*.json"))
    if not json_files:
        sys.exit(f"No cited_sent JSON files found under {cited_dir}")

    if args.paper:
        json_files = [f for f in json_files if args.paper in f.name]
        if not json_files:
            sys.exit(f"No files matching --paper '{args.paper}'")

    print(f"Verifying {len(json_files)} paper(s)...")

    if args.backend == "solr":
        from solr_lookup import SolrLookup
        print("Backend: OpenAlex Solr (DOI + title matching)")
        lookup = SolrLookup()
    else:
        from mongo_lookup import MongoLookup
        mode = "DOI-only" if args.doi_only else "DOI + title (requires text index)"
        print(f"Backend: Crossref MongoDB ({mode})")
        lookup = MongoLookup()

    crossref = None
    if args.backend == "solr" and not args.no_crossref:
        from crossref_lookup import CrossrefLookup
        crossref = CrossrefLookup()
        print("Crossref fallback: enabled (citations not found in Solr will be tried against api.crossref.org)")

    # clear/create output file upfront so user knows where it is
    out_path = pathlib.Path(args.out) if args.out else None
    if out_path:
        out_path.write_text("")
        print(f"Results will be written incrementally to: {out_path}\n")

    # Shared cache across all papers — same citation cited in multiple papers
    # only hits Solr once (common for BWA, Cochrane handbook, Bradford Hill, etc.)
    global_cache: dict = {}
    global_cache_lock = Lock()

    all_results = []
    doi_only = args.doi_only if args.backend == "mongo" else False

    for idx, path in enumerate(json_files, 1):
        print(f"\n[{idx}/{len(json_files)}] {path.stem[:60]}", flush=True)
        results = verify_paper(
            path, lookup, doi_only=doi_only, verbose=True, crossref=crossref,
            workers=args.workers, cache=global_cache, cache_lock=global_cache_lock,
        )
        all_results.extend(results)

        if out_path:
            lines = _format_rows(results, args.show_found)
            with out_path.open("a") as f:
                f.write("\n".join(lines) + "\n")

    # ── summary ──────────────────────────────────────────────────────────
    total     = len(all_results)
    found     = sum(1 for r in all_results if r["status"] == "FOUND")
    mismatch  = sum(1 for r in all_results if r["status"] == "FOUND_MISMATCH")
    not_found = sum(1 for r in all_results if r["status"] == "NOT_FOUND")
    by_method: dict[str, int] = {}
    for r in all_results:
        by_method[r["method"]] = by_method.get(r["method"], 0) + 1

    summary = [
        "",
        "=" * 70,
        "SUMMARY",
        "=" * 70,
        f"  Papers processed : {len(json_files)}",
        f"  Total citations  : {total}",
        f"  Found            : {found}  ({100*found/total:.1f}%)",
        f"  Found (mismatch) : {mismatch}  ({100*mismatch/total:.1f}%)",
        f"  Not found        : {not_found}  ({100*not_found/total:.1f}%)",
        "",
        "  By match method:",
    ]
    for method, count in sorted(by_method.items()):
        summary.append(f"    {method:<15} {count:>5}  ({100*count/total:.1f}%)")

    if mismatch:
        summary += [
            "",
            f"  FOUND_MISMATCH detail ({mismatch}):",
        ]
        for r in all_results:
            if r["status"] == "FOUND_MISMATCH":
                summary.append(
                    f"    {r['paper'][:40]}  [{r['year']}] {r['title'][:60]}"
                )
                for issue in r.get("discrepancies", []):
                    summary.append(f"         → {issue}")

    print("\n".join(summary))

    if out_path:
        with out_path.open("a") as f:
            f.write("\n".join(summary) + "\n")
        print(f"\nFull results written to {out_path}")


if __name__ == "__main__":
    main()
