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


def verify_paper(json_path: pathlib.Path, lookup, doi_only: bool = False,
                 verbose: bool = True, crossref=None):
    """Verify all citations in one cited_sent JSON file. Returns list of result dicts."""
    citations = json.loads(json_path.read_text())
    paper_name = json_path.stem.replace(".tei", "")
    results = []

    for i, entry in enumerate(citations, 1):
        obj = make_citation_obj(entry)

        if doi_only:
            from mongo_lookup import LookupResult, MatchMethod
            if obj.doi:
                result = lookup.by_doi(obj.doi)
            else:
                result = LookupResult(found=False, method=MatchMethod.NOT_FOUND)
        else:
            result = lookup.by_citation(obj)

        # Crossref fallback: if Solr (or Mongo) did not find it, try Crossref
        if not result.found and crossref is not None:
            result = crossref.by_citation(obj)

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
            row["db_title"]   = raw_title or ""
            row["db_year"]    = (
                rec.get("publication_year") or
                rec.get("published-print", {}).get("date-parts", [[None]])[0][0]
            )
            pl = rec.get("primary_location")
            if isinstance(pl, dict):
                row["db_journal"] = pl.get("source", {}).get("display_name", "")
            else:
                ct = rec.get("container-title", [""])
                row["db_journal"] = ct[0] if isinstance(ct, list) else ct
        results.append(row)

        # live progress dot every 10 citations
        if verbose and i % 10 == 0:
            found_so_far = sum(1 for r in results if r["found"])
            print(f"    {i}/{len(citations)} citations checked  "
                  f"({found_so_far} found so far)", flush=True)

    if verbose:
        found     = sum(1 for r in results if r["found"])
        not_found = len(results) - found
        print(f"\n{'='*70}")
        print(f"Paper: {paper_name}  ({len(citations)} citations)")
        print(f"  Found: {found}  |  Not found: {not_found}")
        if not_found:
            print(f"\n  ⚠  NOT FOUND ({not_found}):")
            for r in results:
                if not r["found"]:
                    print(f"    [{r['year']}] {r['title'][:80]}")
                    if r["sentences"]:
                        print(f"         → \"{r['sentences'][0][:100]}\"")

    return results


def _format_rows(results: list, show_found: bool) -> list[str]:
    lines = []
    for r in results:
        status = "FOUND" if r["found"] else "NOT_FOUND"
        lines.append(
            f"{status:<10} [{r['method']:<12}] conf={r['confidence']:.2f} "
            f"| {r['paper'][:30]:<30} | [{r['year']}] {r['title'][:60]}"
        )
        if r["found"] and show_found:
            lines.append(
                f"           db:   {r.get('db_title','')[:60]}"
                f" ({r.get('db_year','')})"
            )
        elif not r["found"] and r["sentences"]:
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

    all_results = []
    for idx, path in enumerate(json_files, 1):
        print(f"\n[{idx}/{len(json_files)}] {path.stem[:60]}", flush=True)
        doi_only = args.doi_only if args.backend == "mongo" else False
        results = verify_paper(path, lookup, doi_only=doi_only, verbose=True, crossref=crossref)
        all_results.extend(results)

        # write this paper's results immediately
        if out_path:
            lines = _format_rows(results, args.show_found)
            with out_path.open("a") as f:
                f.write("\n".join(lines) + "\n")

    # ── summary ──────────────────────────────────────────────────────────
    total     = len(all_results)
    found     = sum(1 for r in all_results if r["found"])
    not_found = total - found
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
        f"  Not found        : {not_found}  ({100*not_found/total:.1f}%)",
        "",
        "  By match method:",
    ]
    for method, count in sorted(by_method.items()):
        summary.append(f"    {method:<15} {count:>5}  ({100*count/total:.1f}%)")

    print("\n".join(summary))

    if out_path:
        with out_path.open("a") as f:
            f.write("\n".join(summary) + "\n")
        print(f"\nFull results written to {out_path}")


if __name__ == "__main__":
    main()
