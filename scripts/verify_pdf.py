"""
verify_pdf.py — one-shot PDF → citation verification → recommendations.

Pipeline
--------
  1. POST the PDF to GROBID /api/processFulltextDocument → TEI XML
  2. Parse the TEI for all references (title, year, doi, raw text)
  3. Verify each citation against OpenAlex Solr (DOI → title+year → title → Crossref)
  4. For every NOT_FOUND citation, run vector re-ranking and show the top-3
     closest real papers with similarity scores.

Usage
-----
    python3 verify_pdf.py paper.pdf
    python3 verify_pdf.py paper.pdf --n 5           # top-5 suggestions
    python3 verify_pdf.py paper.pdf --no-vector     # skip Phase 4
    python3 verify_pdf.py paper.pdf --show-found    # also print matched citations
    python3 verify_pdf.py paper.pdf --out report.txt
"""

from __future__ import annotations

import argparse
import re
import sys
import pathlib
import types
import xml.etree.ElementTree as ET
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GROBID_URL = "http://localhost:8070/api/processFulltextDocument"
GROBID_TIMEOUT = 120   # seconds — full-text extraction can be slow

_NS = {"tei": "http://www.tei-c.org/ns/1.0"}

_DOI_RE = re.compile(
    r'(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(10\.\d{4,}/[^\s,;)\]>]+)',
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r'\b(1[89]\d{2}|20[012]\d)\b')

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

_COLOUR = sys.stdout.isatty()

def _c(t, code):  return f"\033[{code}m{t}\033[0m" if _COLOUR else t
def _bold(t):     return _c(t, "1")
def _green(t):    return _c(t, "32")
def _yellow(t):   return _c(t, "33")
def _red(t):      return _c(t, "31")
def _cyan(t):     return _c(t, "36")
def _dim(t):      return _c(t, "2")

def _sim_col(s):
    if s >= 0.90: return _green(f"{s:.4f}")
    if s >= 0.75: return _yellow(f"{s:.4f}")
    return _red(f"{s:.4f}")


# ---------------------------------------------------------------------------
# Step 1 – GROBID
# ---------------------------------------------------------------------------

def grobid_process(pdf_path: pathlib.Path) -> str:
    """POST PDF to GROBID, return TEI XML string."""
    print(f"  Sending to GROBID… ", end="", flush=True)
    with pdf_path.open("rb") as fh:
        resp = requests.post(
            GROBID_URL,
            files={"input": (pdf_path.name, fh, "application/pdf")},
            data={"consolidateHeader": "0", "consolidateCitations": "0",
              "includeRawCitations": "1"},
            timeout=GROBID_TIMEOUT,
        )
    resp.raise_for_status()
    print(f"done ({len(resp.content)//1024} KB TEI)")
    return resp.text


# ---------------------------------------------------------------------------
# Step 2 – Parse TEI references
# ---------------------------------------------------------------------------

def _tei_text(el, xpath: str) -> Optional[str]:
    found = el.find(xpath, _NS)
    return found.text.strip() if found is not None and found.text else None


def _tei_doi(ref) -> Optional[str]:
    for idno in ref.findall(".//tei:idno", _NS):
        if (idno.get("type") or "").upper() == "DOI" and idno.text:
            doi = idno.text.strip().lower()
            doi = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', doi)
            return doi.rstrip(".,;)")
    return None


def _tei_year(ref) -> Optional[int]:
    date = ref.find(".//tei:date[@type='published']", _NS)
    if date is not None:
        when = date.get("when") or date.text or ""
        m = _YEAR_RE.search(when)
        if m:
            return int(m.group(1))
    return None


def _tei_title(ref) -> Optional[str]:
    # Article title (analytic) preferred; fall back to monograph title
    for xpath in (
        ".//tei:analytic/tei:title[@level='a']",
        ".//tei:analytic/tei:title",
        ".//tei:monogr/tei:title[@level='j']",
        ".//tei:monogr/tei:title",
    ):
        t = _tei_text(ref, xpath)
        if t and len(t.split()) >= 2:
            return t
    return None


def parse_tei_references(tei_xml: str) -> list[types.SimpleNamespace]:
    """
    Return a list of SimpleNamespace objects with:
        .raw   (str)           raw reference string from GROBID
        .title (str|None)
        .year  (int|None)
        .doi   (str|None)
        .ref_id (str)          TEI xml:id attribute
    """
    try:
        root = ET.fromstring(tei_xml)
    except ET.ParseError as e:
        print(f"  ⚠  TEI parse error: {e}", file=sys.stderr)
        return []

    refs = []
    for bib in root.findall(".//tei:listBibl/tei:biblStruct", _NS):
        obj = types.SimpleNamespace()
        obj.ref_id = bib.get("{http://www.w3.org/XML/1998/namespace}id") or ""

        # Raw reference text
        raw_note = bib.find(".//tei:note[@type='raw_reference']", _NS)
        obj.raw = raw_note.text.strip() if raw_note is not None and raw_note.text else ""

        obj.doi   = _tei_doi(bib)
        obj.year  = _tei_year(bib)
        obj.title = _tei_title(bib)

        # If no DOI from TEI, try to find one in the raw string
        if not obj.doi and obj.raw:
            m = _DOI_RE.search(obj.raw)
            if m:
                obj.doi = m.group(1).rstrip(".,;)").lower()

        refs.append(obj)

    return refs


# ---------------------------------------------------------------------------
# Step 3 – Verify against Solr (reuse solr_lookup logic)
# ---------------------------------------------------------------------------

def _load_solr():
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from solr_lookup import SolrLookup
    return SolrLookup()


def _load_vector(threshold: float = 0.82):
    from vector_lookup import VectorLookup
    return VectorLookup(threshold=threshold)


def verify_references(refs: list, solr, vector_lookup=None,
                      n_suggest: int = 3) -> list[dict]:
    """
    Verify all refs using a fast batch pass first, then individual fallbacks.

    Phase 1: DOI lookups for refs that have a DOI.
    Phase 2: Batch Solr OR-query for all titled refs (15 at a time).
    Phase 3: Individual Solr fallback (with year filter) + Crossref for still-NOT_FOUND.
    Phase 4: Vector re-ranking for still-NOT_FOUND (auto-accepts above threshold;
             otherwise returns top-N suggestions for the human).
    """
    from solr_lookup import MatchMethod, SolrResult

    n = len(refs)
    results = [None] * n

    # ── Phase 1: DOI lookups ─────────────────────────────────────────────────
    doi_count = 0
    for i, ref in enumerate(refs):
        if ref.doi:
            r = solr.by_doi(ref.doi)
            if r.found:
                results[i] = r
                doi_count += 1
    if doi_count:
        print(f"    Phase 1 (DOI):   {doi_count} found", flush=True)

    # ── Phase 2: batch title search ──────────────────────────────────────────
    not_yet  = [i for i in range(n) if results[i] is None]
    titled   = [i for i in not_yet if refs[i].title]
    if titled:
        batch_in = [refs[i] for i in titled]
        batch_out = solr.by_title_batch(batch_in)
        batch_found = 0
        for j, r in enumerate(batch_out):
            i = titled[j]
            if r.found:
                results[i] = r
                batch_found += 1
        print(f"    Phase 2 (batch): {batch_found}/{len(titled)} found", flush=True)

    # ── Phase 3: individual fallback + Crossref ──────────────────────────────
    still_missing = [i for i in range(n) if results[i] is None]
    ph3_found = 0
    for i in still_missing:
        ref = refs[i]
        r = solr.by_citation(ref)   # tries title+year, title-only, variants
        if r.found:
            results[i] = r
            ph3_found += 1
            continue
        # Crossref fallback
        if ref.doi or ref.title:
            try:
                from crossref_lookup import CrossrefLookup
                xr = CrossrefLookup()
                r = xr.by_citation(ref)
                if r.found:
                    results[i] = r
                    ph3_found += 1
                    continue
            except Exception:
                pass
    if ph3_found:
        print(f"    Phase 3 (indiv+Crossref): {ph3_found} found", flush=True)

    # ── Phase 4: vector ──────────────────────────────────────────────────────
    suggestions_map: dict[int, list] = {}
    if vector_lookup:
        vec_candidates = [i for i in range(n) if results[i] is None and refs[i].title]
        vec_found = 0
        for i in vec_candidates:
            ref = refs[i]
            r_vec = vector_lookup.by_title(ref.title, year=ref.year)
            if r_vec.found:
                results[i] = r_vec
                vec_found += 1
            else:
                suggestions_map[i] = vector_lookup.recommend(
                    ref.title, year=ref.year, n=n_suggest
                )
        if vec_candidates:
            print(f"    Phase 4 (vector):  {vec_found}/{len(vec_candidates)} recovered",
                  flush=True)

    # ── Collect final results ────────────────────────────────────────────────
    not_found_sentinel = SolrResult(found=False, method=MatchMethod.NOT_FOUND)
    out = []
    for i, ref in enumerate(refs):
        r = results[i] or not_found_sentinel
        out.append({
            "raw":         ref.raw,
            "title":       ref.title,
            "year":        ref.year,
            "doi":         ref.doi,
            "status":      "FOUND" if r.found else "NOT_FOUND",
            "method":      r.method,
            "confidence":  r.confidence,
            "record":      r.record,
            "suggestions": suggestions_map.get(i, []),
        })
    return out


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _short(s, n=70):
    if not s: return "(none)"
    return s if len(s) <= n else s[:n-1] + "…"


def print_report(results: list[dict], show_found: bool = False,
                 out_fh=None) -> None:
    fhs = [sys.stdout] + ([out_fh] if out_fh else [])

    def _print(*args, **kwargs):
        for fh in fhs:
            print(*args, **kwargs, file=fh)

    total     = len(results)
    found     = sum(1 for r in results if r["status"] == "FOUND")
    not_found = total - found

    _print()
    _print(_bold("=" * 70))
    _print(_bold("RESULTS"))
    _print(_bold("=" * 70))
    _print(f"  Total citations  : {total}")
    _print(f"  Found            : {_green(str(found))}  ({100*found//total if total else 0}%)")
    _print(f"  Not found        : {(_red if not_found else _green)(str(not_found))}")
    _print()

    if show_found:
        for i, r in enumerate(results, 1):
            if r["status"] != "FOUND":
                continue
            _print(f"  [{i:03d}] ✓ {_dim(r['method'].value)}  conf={r['confidence']:.3f}")
            _print(f"        {_dim(_short(r['raw'], 68))}")

    if not_found:
        _print(_bold("─" * 70))
        _print(_bold(f"NOT FOUND ({not_found}) — top-3 closest matches:"))
        _print(_bold("─" * 70))

        nf_idx = 0
        for i, r in enumerate(results, 1):
            if r["status"] != "NOT_FOUND":
                continue
            nf_idx += 1
            _print()
            _print(f"  {_bold(f'[{nf_idx}]')} Citation #{i}")
            # Show raw if available, otherwise reconstruct from parsed fields
            display_raw = r["raw"] or ""
            if not display_raw:
                parts = []
                if r["title"]: parts.append(r["title"])
                if r["year"]:  parts.append(f"({r['year']})")
                if r["doi"]:   parts.append(r["doi"])
                display_raw = "  ".join(parts) if parts else "(no text extracted)"
            _print(f"       raw   : {_yellow(_short(display_raw, 66))}")
            if r["title"] and r["title"] not in display_raw:
                _print(f"       title : {_short(r['title'], 66)}")
            if r["year"] and str(r["year"]) not in display_raw:
                _print(f"       year  : {r['year']}")

            sug = r.get("suggestions") or []
            if not sug:
                _print(f"       {_dim('(no candidates above similarity floor — paper likely not in OpenAlex)')}")
            else:
                for rank, s in enumerate(sug, 1):
                    sim = s.get("similarity", 0.0)
                    t   = s.get("title") or "(no title)"
                    yr  = s.get("year") or "—"
                    doi = s.get("doi") or "—"
                    doi_url = f"https://doi.org/{doi}" if doi != "—" else "—"
                    _print(f"       #{rank}  sim={_sim_col(sim)}")
                    _print(f"           title : {_cyan(_short(t, 62))}")
                    _print(f"           year  : {yr}    doi: {doi_url}")

    _print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Verify all citations in a PDF and recommend matches for unfound ones.")
    ap.add_argument("pdf", help="Path to the PDF to analyse.")
    ap.add_argument("--n", type=int, default=3,
                    help="Number of recommendations per NOT_FOUND citation (default 3).")
    ap.add_argument("--show-found", action="store_true",
                    help="Also print successfully matched citations.")
    ap.add_argument("--no-vector", action="store_true",
                    help="Skip vector re-ranking (Phase 4).")
    ap.add_argument("--vector-threshold", type=float, default=0.82,
                    help="Cosine threshold for auto-accepting a vector match (default 0.82).")
    ap.add_argument("--out", default="",
                    help="Also write the report to this file.")
    args = ap.parse_args()

    pdf = pathlib.Path(args.pdf)
    if not pdf.exists():
        print(f"ERROR: file not found: {pdf}", file=sys.stderr)
        sys.exit(1)

    print(_bold(f"\nverify_pdf: {pdf.name}"))

    # ── Step 1: GROBID ──────────────────────────────────────────────────────
    try:
        tei = grobid_process(pdf)
    except Exception as e:
        print(f"  GROBID failed: {e}", file=sys.stderr)
        sys.exit(1)

    # ── Step 2: parse references ─────────────────────────────────────────────
    refs = parse_tei_references(tei)
    if not refs:
        print("  No references found in TEI output.")
        sys.exit(0)
    print(f"  Extracted {len(refs)} references from TEI")

    # ── Step 3: load backends ────────────────────────────────────────────────
    print("  Connecting to Solr… ", end="", flush=True)
    try:
        solr = _load_solr()
        print("ok")
    except Exception as e:
        print(f"\n  Solr unavailable: {e}", file=sys.stderr)
        sys.exit(1)

    vector_lookup = None
    if not args.no_vector:
        print("  Loading vector model… ", end="", flush=True)
        try:
            vector_lookup = _load_vector(threshold=args.vector_threshold)
            # warm up
            import vector_lookup as _vl_mod
            _vl_mod._get_model()
            print("ok")
        except ImportError:
            print("skipped (sentence-transformers not installed)")

    # ── Step 4: verify ───────────────────────────────────────────────────────
    print(f"  Verifying {len(refs)} citations… ", flush=True)
    results = verify_references(refs, solr, vector_lookup=vector_lookup)

    # ── Step 5: report ───────────────────────────────────────────────────────
    out_fh = open(args.out, "w") if args.out else None
    try:
        print_report(results, show_found=args.show_found, out_fh=out_fh)
    finally:
        if out_fh:
            out_fh.close()
            print(f"Report also written to: {args.out}")


if __name__ == "__main__":
    main()
