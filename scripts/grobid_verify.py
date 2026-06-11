"""
grobid_verify.py — verify GROBID-parsed citations against OpenAlex (Solr) or Crossref (MongoDB).

Reads all cited_sent JSON files produced by the GROBID pipeline (step2_tei_to_json)
and looks up each citation.

Default: uses the OpenAlex Solr index (492M works, no index build required) for both
DOI and title+year matching.

Fallback: pass --backend mongo to use the Crossref MongoDB instead (requires the text
index to be finished building first; use --doi-only to skip title search).

Usage (from scripts/ directory):
    python3 grobid_verify.py                         # Solr, full matching (DOI + title)
    python3 grobid_verify.py --paper jama            # one paper (substring match)
    python3 grobid_verify.py --out results.txt       # save output
    python3 grobid_verify.py --show-found            # also print matched citations
    python3 grobid_verify.py --backend mongo         # use MongoDB (needs text index)
    python3 grobid_verify.py --backend mongo --doi-only  # MongoDB, DOI-only
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

# Matches DOIs embedded in raw citation strings, e.g. "doi:10.1056/..." or
# "https://doi.org/10.1056/..."
_DOI_RE = re.compile(
    r'(?:https?://(?:dx\.)?doi\.org/|doi:\s*)(10\.\d{4,}/\S+)',
    re.IGNORECASE,
)


def _normalize_doi(doi: str) -> str:
    doi = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', doi.strip(), flags=re.I)
    return doi.lower()


def extract_doi_from_raw(citation_str: str) -> str | None:
    """Pull a DOI out of a raw citation string if one is present."""
    m = _DOI_RE.search(citation_str)
    if m:
        doi = m.group(1).rstrip('.,)')
        return _normalize_doi(doi)
    return None


def make_citation_obj(entry: dict):
    """Wrap a cited_sent dict in a simple object with .doi/.title/.year attributes.
    DOI is taken from the 'doi' field if present, otherwise extracted from 'citation'."""
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
                 verbose: bool = True):
    """Verify all citations in one cited_sent JSON file. Returns list of result dicts."""
    citations = json.loads(json_path.read_text())
    paper_name = json_path.stem.replace(".tei", "")
    results = []

    for entry in citations:
        obj = make_citation_obj(entry)

        if doi_only:
            # Only attempt DOI lookup — skip title search entirely
            from mongo_lookup import LookupResult, MatchMethod
            if obj.doi:
                result = lookup.by_doi(obj.doi)
            else:
                result = LookupResult(found=False, method=MatchMethod.NOT_FOUND)
        else:
            result = lookup.by_citation(obj)

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
            # Handle both Solr (OpenAlex) and MongoDB (Crossref) record shapes
            raw_title = rec.get("title") or rec.get("title_s")
            if isinstance(raw_title, list):
                raw_title = raw_title[0] if raw_title else ""
            row["db_title"]   = raw_title or ""
            row["db_year"]    = rec.get("publication_year") or rec.get("published-print", {}).get("date-parts", [[None]])[0][0]
            row["db_journal"] = rec.get("primary_location", {}).get("source", {}).get("display_name", "") if isinstance(rec.get("primary_location"), dict) else (rec.get("container-title", [""])[0] if isinstance(rec.get("container-title"), list) else "")
        results.append(row)

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


def main():
    ap = argparse.ArgumentParser(
        description="Verify GROBID citations via OpenAlex (Solr) or Crossref (MongoDB)")
    ap.add_argument("--paper", default=None,
                    help="filter to papers whose filename contains this substring")
    ap.add_argument("--out", default=None,
                    help="write full results to this text file")
    ap.add_argument("--show-found", action="store_true",
                    help="also print successfully matched citations")
    ap.add_argument("--backend", choices=["solr", "mongo"], default="solr",
                    help="lookup backend: 'solr' (default, OpenAlex) or 'mongo' (Crossref)")
    ap.add_argument("--doi-only", action="store_true",
                    help="only use DOI lookup (mongo backend only; skips title search)")
    args = ap.parse_args()

    json_files = sorted(CITED_SENT_DIR.glob("*.json"))
    if not json_files:
        sys.exit(f"No cited_sent JSON files found under {CITED_SENT_DIR}")

    if args.paper:
        json_files = [f for f in json_files if args.paper in f.name]
        if not json_files:
            sys.exit(f"No files matching --paper '{args.paper}'")

    print(f"Verifying {len(json_files)} paper(s)...")

    if args.backend == "solr":
        from solr_lookup import SolrLookup
        print("Backend: OpenAlex Solr (DOI + title matching)")
        ctx = SolrLookup()
    else:
        from mongo_lookup import MongoLookup
        mode = "DOI-only" if args.doi_only else "DOI + title (requires text index)"
        print(f"Backend: Crossref MongoDB ({mode})")
        ctx = MongoLookup()

    all_results = []
    with ctx if hasattr(ctx, '__enter__') else _nullctx(ctx) as lookup:
        for path in json_files:
            doi_only = args.doi_only if args.backend == "mongo" else False
            results = verify_paper(path, lookup, doi_only=doi_only, verbose=True)
            all_results.extend(results)

    # ── summary ──────────────────────────────────────────────────────────
    total     = len(all_results)
    found     = sum(1 for r in all_results if r["found"])
    not_found = total - found
    by_method = {}
    for r in all_results:
        by_method[r["method"]] = by_method.get(r["method"], 0) + 1

    print(f"\n{'='*70}")
    print(f"SUMMARY")
    print(f"{'='*70}")
    print(f"  Papers processed : {len(json_files)}")
    print(f"  Total citations  : {total}")
    print(f"  Found            : {found}  ({100*found/total:.1f}%)")
    print(f"  Not found        : {not_found}  ({100*not_found/total:.1f}%)")
    print(f"\n  By match method:")
    for method, count in sorted(by_method.items()):
        print(f"    {method:<15} {count:>5}  ({100*count/total:.1f}%)")

    # ── optional file output ──────────────────────────────────────────────
    if args.out:
        out_path = pathlib.Path(args.out)
        lines = []
        for r in all_results:
            status = "FOUND" if r["found"] else "NOT_FOUND"
            lines.append(
                f"{status:<10} [{r['method']:<12}] conf={r['confidence']:.2f} "
                f"| {r['paper'][:30]:<30} | [{r['year']}] {r['title'][:60]}"
            )
            if r["found"] and args.show_found:
                lines.append(
                    f"           db:   {r.get('db_title','')[:60]}"
                    f" ({r.get('db_year','')})"
                )
            elif not r["found"] and r["sentences"]:
                lines.append(
                    f"           cited in: \"{r['sentences'][0][:80]}\""
                )
        out_path.write_text("\n".join(lines) + "\n")
        print(f"\nFull results written to {out_path}")


class _nullctx:
    """Trivial context manager for objects that don't implement __enter__/__exit__."""
    def __init__(self, obj): self._obj = obj
    def __enter__(self): return self._obj
    def __exit__(self, *_): pass


if __name__ == "__main__":
    main()
