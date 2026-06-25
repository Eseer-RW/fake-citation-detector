"""
recommend_citation.py — find the closest real paper(s) for a suspicious or wrong citation.

Given a raw citation string (copy-pasted from a paper, possibly mis-cited, truncated,
or with OCR artifacts), this tool:
  1. Parses the citation to extract a clean title and year.
  2. Runs a broad Solr edismax search with phrase boost to retrieve ~40 candidates.
  3. Re-ranks candidates with a lightweight sentence-transformer (all-MiniLM-L6-v2).
  4. Prints the top-N most similar papers with their similarity scores.

Intended for two workflows:
  A. Interactive: paste a single suspicious citation, get ranked suggestions.
  B. Batch (pipe): feed many citations from stdin or a file.

Usage (interactive):
    python3 recommend_citation.py
    python3 recommend_citation.py --n 5
    python3 recommend_citation.py --raw "Smith J et al. COVID-19... JAMA 2020."

Usage (batch / piped):
    cat suspicious_citations.txt | python3 recommend_citation.py --batch
    python3 recommend_citation.py --batch --file citations.txt --n 5
    python3 recommend_citation.py --batch --file citations.txt --json > output.jsonl

Options:
    --raw TEXT       Single raw citation to look up (skips interactive prompt).
    --n INT          Number of top matches to return (default 3).
    --min-sim FLOAT  Minimum similarity to display (default 0.0).
    --no-year        Ignore parsed year when searching (search across all years).
    --batch          Read one citation per line from stdin or --file.
    --file PATH      Input file for --batch mode (default: stdin).
    --json           Output machine-readable JSONL (one JSON object per citation).
    --threshold FLOAT  Cosine similarity threshold for pipeline use (not shown in
                       interactive mode; only affects exit code in --check mode).
"""

from __future__ import annotations

import argparse
import json
import sys
import pathlib
import textwrap

# ---------------------------------------------------------------------------
# Colour helpers (degrade gracefully when stdout is not a terminal)
# ---------------------------------------------------------------------------

_USE_COLOUR = sys.stdout.isatty()

def _c(text: str, code: str) -> str:
    if not _USE_COLOUR:
        return text
    return f"\033[{code}m{text}\033[0m"

def _bold(t):    return _c(t, "1")
def _green(t):   return _c(t, "32")
def _yellow(t):  return _c(t, "33")
def _cyan(t):    return _c(t, "36")
def _dim(t):     return _c(t, "2")
def _red(t):     return _c(t, "31")


def _sim_colour(sim: float) -> str:
    if sim >= 0.90:
        return _green(f"{sim:.4f}")
    if sim >= 0.75:
        return _yellow(f"{sim:.4f}")
    return _red(f"{sim:.4f}")


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

_WIDTH = 80


def _print_parsed(parsed: dict) -> None:
    """Print the parsed citation fields."""
    print()
    print(_bold("Parsed citation"))
    print("  " + "─" * (_WIDTH - 2))
    title = parsed.get("title") or "(no title extracted)"
    year  = parsed.get("year")  or "unknown"
    doi   = parsed.get("doi")   or "none"
    src   = parsed.get("source") or "?"
    print(f"  title  : {_cyan(textwrap.shorten(title, 70))}")
    print(f"  year   : {year}    doi: {doi}    source: {_dim(src)}")
    print("  " + "─" * (_WIDTH - 2))


def _print_recommendations(recs: list[dict], n: int) -> None:
    """Pretty-print ranked recommendations."""
    if not recs:
        print(_yellow("  No candidates found in OpenAlex for this query."))
        return

    print(_bold(f"\nTop {len(recs)} recommendation(s)"))
    for rank, r in enumerate(recs, 1):
        sim    = r.get("similarity", 0.0)
        title  = r.get("title") or "(no title)"
        year   = r.get("year")  or "—"
        doi    = r.get("doi")   or "—"
        jrnl   = r.get("journal") or "—"
        oa_id  = r.get("openalex_id") or "—"

        # Short DOI / URL
        doi_display = doi if doi == "—" else f"https://doi.org/{doi.lstrip('https://doi.org/')}"

        print()
        print(f"  {_bold(f'#{rank}')}  sim={_sim_colour(sim)}")
        print(f"      title   : {_cyan(textwrap.shorten(title, 72))}")
        print(f"      year    : {year}    journal: {jrnl}")
        print(f"      doi     : {doi_display}")
        print(f"      openalex: {_dim(oa_id)}")


# ---------------------------------------------------------------------------
# Core lookup
# ---------------------------------------------------------------------------

def _load_lookup(threshold: float = 0.82, candidates: int = 40):
    """Import VectorLookup (deferred so we see errors early)."""
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from vector_lookup import VectorLookup
    return VectorLookup(threshold=threshold, candidates=candidates)


def _do_lookup(vl, raw: str, n: int, min_sim: float, use_year: bool) -> tuple[dict, list[dict]]:
    """Run recommend_from_raw and return (parsed_dict, recs)."""
    from vector_lookup import VectorLookup
    parsed, recs = vl.recommend_from_raw(raw, n=n, min_sim=min_sim)
    if not use_year and parsed.get("year"):
        # Re-run without year so we search all years
        from citation_parser import parse_citation
        p = parse_citation(raw)
        recs = vl.recommend(p.title or raw, year=None, n=n, min_sim=min_sim)
    return parsed, recs


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

BANNER = """\
╔══════════════════════════════════════════════════════════════╗
║          Citation Recommendation  (vector similarity)        ║
║  Paste a suspicious / wrong citation and get the closest     ║
║  matching real papers from OpenAlex (492M works).            ║
║  Type 'quit' or press Ctrl-C to exit.                        ║
╚══════════════════════════════════════════════════════════════╝
"""


def run_interactive(vl, n: int, min_sim: float, use_year: bool) -> None:
    print(_bold(BANNER))
    while True:
        try:
            raw = input(_bold("Citation> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not raw:
            continue
        if raw.lower() in {"quit", "exit", "q"}:
            print("Bye.")
            break

        _run_one(vl, raw, n, min_sim, use_year, as_json=False)


def _run_one(vl, raw: str, n: int, min_sim: float, use_year: bool, as_json: bool) -> None:
    """Look up a single raw citation and print the result."""
    try:
        parsed, recs = _do_lookup(vl, raw, n, min_sim, use_year)
    except Exception as e:
        if as_json:
            print(json.dumps({"error": str(e), "raw": raw}))
        else:
            print(f"  Error: {e}", file=sys.stderr)
        return

    if as_json:
        print(json.dumps({
            "parsed":          parsed,
            "recommendations": recs,
        }))
    else:
        _print_parsed(parsed)
        _print_recommendations(recs, n)
        print()


# ---------------------------------------------------------------------------
# Batch mode
# ---------------------------------------------------------------------------

def run_batch(vl, src, n: int, min_sim: float, use_year: bool, as_json: bool) -> None:
    """Process one citation per line from `src` (file-like)."""
    for lineno, line in enumerate(src, 1):
        raw = line.rstrip("\n")
        if not raw or raw.startswith("#"):
            continue

        if not as_json:
            print(_bold(f"\n─── Citation #{lineno} ─────────────────────────────────"))
            print(_dim(textwrap.shorten(raw, 78)))

        _run_one(vl, raw, n, min_sim, use_year, as_json=as_json)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="recommend_citation",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--raw",       metavar="TEXT",  default="",
                    help="Single raw citation string to look up.")
    ap.add_argument("--n",         metavar="INT",   type=int, default=3,
                    help="Number of top matches to return (default 3).")
    ap.add_argument("--min-sim",   metavar="FLOAT", type=float, default=0.0,
                    help="Minimum similarity to display (default 0.0).")
    ap.add_argument("--no-year",   action="store_true",
                    help="Ignore parsed year; search across all years.")
    ap.add_argument("--batch",     action="store_true",
                    help="Batch mode: read one citation per line.")
    ap.add_argument("--file",      metavar="PATH",  default="",
                    help="Input file for --batch mode (default: stdin).")
    ap.add_argument("--json",      action="store_true",
                    help="Output machine-readable JSONL.")
    ap.add_argument("--candidates", metavar="INT",  type=int, default=40,
                    help="Solr candidate pool size (default 40).")
    ap.add_argument("--threshold", metavar="FLOAT", type=float, default=0.82,
                    help="Cosine threshold for VectorLookup.by_title (default 0.82).")
    return ap


def main() -> None:
    ap = build_parser()
    args = ap.parse_args()
    use_year = not args.no_year

    # ── Load model (this triggers the ~80 MB download on first run) ─────────
    if not args.json:
        print(f"Loading model (all-MiniLM-L6-v2)…", end=" ", flush=True)
    try:
        vl = _load_lookup(threshold=args.threshold, candidates=args.candidates)
        # warm up
        import vector_lookup as _vl_mod
        _vl_mod._get_model()
    except ImportError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        print("Install sentence-transformers:  pip install sentence-transformers",
              file=sys.stderr)
        sys.exit(1)
    if not args.json:
        print("ready.\n")

    # ── Single citation from --raw flag ──────────────────────────────────────
    if args.raw:
        _run_one(vl, args.raw, args.n, args.min_sim, use_year, as_json=args.json)
        return

    # ── Batch mode ───────────────────────────────────────────────────────────
    if args.batch:
        if args.file:
            with open(args.file) as fh:
                run_batch(vl, fh, args.n, args.min_sim, use_year, as_json=args.json)
        else:
            if not args.json:
                print(_dim("Reading citations from stdin (one per line, Ctrl-D to finish)…"))
            run_batch(vl, sys.stdin, args.n, args.min_sim, use_year, as_json=args.json)
        return

    # ── Interactive mode ─────────────────────────────────────────────────────
    run_interactive(vl, args.n, args.min_sim, use_year)


if __name__ == "__main__":
    main()
