#!/usr/bin/env python3
"""
s2_backstop.py — Semantic Scholar existence backstop for the citation-verification AUDIT.

Purpose: clear "real-but-uncovered" references (CS conference papers, recent preprints,
non-English works) that OpenAlex + Crossref miss but Semantic Scholar (~220M papers,
strong CS/arXiv coverage) indexes. This shrinks the not-found pool toward the true
fabrication floor — the same role Zhao's Google-Scholar backstop plays.

IMPORTANT: this is an AUDIT/LABELING layer only, NOT the exact-match detector
(the detector stays exact-match per directive). Use it to decide, for a not-found ref,
whether the work exists *somewhere* before calling it a fabrication candidate.

API: https://api.semanticscholar.org/graph/v1  (unauth ~1 req/s shared; set S2_API_KEY for more).

CLI:  python3 s2_backstop.py IN.tsv OUT_residual.tsv [title_col=2] [--cap N] [--pace 0.4]
      IN.tsv must have a header; title column index given by title_col (default 2).
      Writes rows whose title is NOT found in S2 (the fabrication-candidate residual).
"""
import sys, os, time, re, csv, requests

S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
KEY = os.environ.get("S2_API_KEY")

def _toks(s):
    s = re.sub(r"(\w)-\s*(\w)", r"\1\2", s or "")
    return set(w for w in re.split(r"[^a-z0-9]+", s.lower()) if len(w) > 2)

def _ov(a, b):
    ta, tb = _toks(a), _toks(b)
    return (len(ta & tb) / len(ta | tb)) if (ta | tb) else 0.0

def exists_in_s2(title, min_overlap=0.72, retries=4, pace=0.4):
    """Return (bool_or_None, matched_title). True = a matching paper exists in S2;
    False = searched, no match; None = unusable title / could not check."""
    if not title or len(_toks(title)) < 3:
        return (None, None)
    hdr = {"x-api-key": KEY} if KEY else {}
    for att in range(retries):
        try:
            r = requests.get(S2_SEARCH,
                             params={"query": title, "limit": 5, "fields": "title,year"},
                             headers=hdr, timeout=25)
            if r.status_code == 429:
                time.sleep((2 ** att) + 1); continue
            if r.status_code != 200:
                time.sleep(1); continue
            data = r.json().get("data", []) or []
            best = max(((_ov(title, d.get("title", "")), d.get("title", "")) for d in data),
                       default=(0.0, ""))
            time.sleep(pace)
            return (best[0] >= min_overlap, best[1] if best[0] >= min_overlap else None)
        except Exception:
            time.sleep(1)
    return (None, None)

def _main():
    inp, outp = sys.argv[1], sys.argv[2]
    tcol = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 2
    cap = None; pace = 0.4
    for a in sys.argv[3:]:
        if a.startswith("--cap"): cap = int(a.split("=")[1]) if "=" in a else int(sys.argv[sys.argv.index(a)+1])
        if a.startswith("--pace"): pace = float(a.split("=")[1]) if "=" in a else float(sys.argv[sys.argv.index(a)+1])
    rows = list(csv.reader(open(inp), delimiter="\t"))
    header, data = rows[0], rows[1:]
    if cap: data = data[:cap]
    cleared = residual = unchecked = 0
    out = open(outp, "w")
    out.write("\t".join(header) + "\ts2_status\n")
    for i, r in enumerate(data):
        if len(r) <= tcol:
            continue
        title = r[tcol]
        found, mt = exists_in_s2(title, pace=pace)
        if found is True:
            cleared += 1
        elif found is False:
            residual += 1
            out.write("\t".join(r) + "\tS2_NOT_FOUND\n")
        else:
            unchecked += 1
        if (i + 1) % 50 == 0:
            print("...%d checked  cleared=%d residual=%d unchecked=%d"
                  % (i + 1, cleared, residual, unchecked), flush=True)
    out.close()
    tot = cleared + residual
    print("\n=== S2 backstop ===")
    print("checked=%d  cleared_as_real=%d (%.1f%%)  RESIDUAL=%d  unchecked=%d"
          % (cleared + residual + unchecked, cleared, 100 * cleared / max(tot, 1), residual, unchecked))
    print("residual (S2 also can't find → strongest fabrication candidates) -> %s" % outp)
    print("S2_BACKSTOP_DONE")

if __name__ == "__main__":
    _main()
