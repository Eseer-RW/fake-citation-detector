#!/usr/bin/env python3
"""
fpr_dump.py — dump EVERY false alarm from the gate, with full metadata.

WHY THIS AND NOT revalidate_fakes. That script caps false_alarms at 12 and keeps only
(file, title, nonacademic). You cannot diagnose 176 failures from 12 truncated titles.
This runs the identical harness and writes all of them to JSONL so the expensive part
(the ~9 min eval) is paid once and every subsequent analysis is free.

IMPORTANT PROPERTY OF THIS EVAL. Refs are built from cited_sent JSON, which already
carries structured title/journal/volume/pages/authors. GROBID does not run. So a false
alarm here is NOT a parsing failure -- it is a lookup failure on clean metadata. That
makes this the right population for diagnosing the matcher, and the wrong one for
diagnosing extraction.
"""
import json, glob, re, sys, types, collections, os

sys.path.insert(0, "/space/rwang/fake-citation-detector/scripts")
os.chdir("/space/rwang/fake-citation-detector/scripts")
os.environ.setdefault("SKIP_OPENALEX_API", "1")
os.environ.setdefault("SKIP_CROSSREF_API", "1")
import batch_verify_years as bvy
import solr_lookup
from solr_lookup import SolrLookup

solr_lookup.SOLR_TIMEOUT = 60
EVAL = "/space/rwang/fake-citation-detector/eval/fake_injection"
OUT = "/space/rwang/_speedtest/fpr_false_alarms.jsonl"

import importlib
rv = importlib.import_module("revalidate_fakes") if False else None   # never import: no guard


def mkref(c):
    """Identical to revalidate_fakes.mkref (SYNTH off) -- copied, not imported, because
    revalidate_fakes runs its whole 9-minute eval at module level."""
    o = types.SimpleNamespace()
    o.raw = c.get("citation") or ""
    o.title = (c.get("title") or None)
    o.journal = (c.get("journal") or None)
    o.volume = (c.get("volume") or None)
    yr = c.get("year")
    try:
        o.year = int(str(yr)[:4]) if yr else None
    except Exception:
        o.year = None
    pg = str(c.get("pages") or "")
    o.first_page = (re.split(r"[-–]", pg)[0].strip() or None) if pg else None
    auth = c.get("authors") or ""
    if isinstance(auth, list):
        auth = ";".join(str(x) for x in auth)
    o.first_author = (auth.split(";")[0].strip() or None)
    o.doi = None
    try:
        m = bvy._DOI_RE.search(o.raw)
        if m:
            o.doi = m.group(1).rstrip(".,;)").lower()
    except Exception:
        pass
    return o


gt_files = sorted(glob.glob(os.path.join(EVAL, "cited_sent", "*.json")))
solr = SolrLookup()
tot = collections.Counter()
# CONTENTION GUARD. This runs alongside the v4 sweep, which saturates Solr. A timed-out
# query returns no hit, which is indistinguishable from "not indexed" and inflates FPR
# exactly where I am trying to measure it. Tally every error and refuse to trust the
# number if any appear -- per-paper, so I can also drop just the affected papers.
solr_err = collections.Counter()
err_papers = set()
out = open(OUT, "w")
n_papers = 0

for f in gt_files:
    cits = json.load(open(f))
    refs = [mkref(c) for c in cits]
    flags = [bool(c.get("_is_fake")) for c in cits]
    res = bvy.verify_refs(refs, solr)
    _e = res.get("solr_errors") or {}
    for _k, _v in _e.items():
        solr_err[_k] += _v
    if _e:
        err_papers.add(os.path.basename(f))
    tot["title_phase_errors"] += res.get("title_phase_errors") or 0
    per = {p["i"]: p for p in res.get("per_ref", [])}
    for i, (c, is_fake) in enumerate(zip(cits, flags)):
        p = per.get(i)
        if p is None:
            continue
        if is_fake:
            tot["fake"] += 1
            tot["fake_caught" if not p.get("found") else "fake_missed"] += 1
            continue
        tot["real"] += 1
        if p.get("found"):
            tot["real_ok"] += 1
            continue
        tot["real_flagged"] += 1
        r = refs[i]
        out.write(json.dumps({
            "paper": os.path.basename(f), "i": i,
            "paper_had_solr_err": os.path.basename(f) in err_papers,
            "title": r.title, "journal": r.journal, "volume": r.volume,
            "year": r.year, "first_page": r.first_page,
            "first_author": r.first_author, "doi": r.doi,
            "raw": (r.raw or "")[:600],
            "nonacademic": bool(p.get("nonacademic")),
            "method": p.get("method"),
        }, ensure_ascii=False) + "\n")
    n_papers += 1
    print("  %-38s refs=%4d  (%d/%d papers)" % (
        os.path.basename(f), len(cits), n_papers, len(gt_files)), flush=True)

out.close()
print("\n" + "=" * 62)
print("real=%d  ok=%d  flagged=%d  -> FPR %.1f%%" % (
    tot["real"], tot["real_ok"], tot["real_flagged"],
    100.0 * tot["real_flagged"] / tot["real"] if tot["real"] else 0))
print("fakes=%d caught=%d missed=%d -> recall %.1f%%" % (
    tot["fake"], tot["fake_caught"], tot["fake_missed"],
    100.0 * tot["fake_caught"] / tot["fake"] if tot["fake"] else 0))
print("title-phase errors    : %d" % tot["title_phase_errors"])
print("SOLR ERRORS           : %s" % (dict(solr_err) or "none"))
if solr_err:
    print("  !! %d of %d papers affected -- the FPR above is an UPPER bound, not a" % (
        len(err_papers), len(gt_files)))
    print("     measurement. Re-run when the v4 sweep is not competing for Solr.")
    print("     Affected: %s" % ", ".join(sorted(err_papers)))
print("wrote %s" % OUT)
