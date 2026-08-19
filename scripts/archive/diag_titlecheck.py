#!/usr/bin/env python3
"""
diag_titlecheck.py — is the title-disagreement check ELIGIBLE to fire, or suppressed?

Pass 1 found 0 title disagreements in ~34,700 DOI-matched references. That is either
(a) genuinely no DOI-hijacking in the wild, or (b) the check is structurally suppressed
for real-world reference shapes -- in which case 0% is uninformative and the hijacking
channel is NOT actually being measured.

validate_metadata flags a title disagreement only when ALL of:
    len(cited_title_tokens) >= 4
    the cited title does NOT resolve as a journal name (journal_authority)
    Jaccard(cited, record) < 0.3
Condition D of the fabrication test fired 102/114 -- but those were long fabricated
titles against unrelated papers. Real citations in this corpus often carry a journal
ABBREVIATION in the title slot, which the guard deliberately skips.

This re-extracts a few papers, keeps the DOI-matched refs, and counts how many clear
each gate. If most fail the >=4-token or journal-ish gate, the 0% means "not measured".
"""
import sys, os, json, glob, random, collections, tempfile, shutil, tarfile, pathlib

sys.path.insert(0, "/space/rwang/fake-citation-detector/scripts")
os.chdir("/space/rwang/fake-citation-detector/scripts")
os.environ.setdefault("SKIP_OPENALEX_API", "1")
os.environ.setdefault("SKIP_CROSSREF_API", "1")
import batch_verify_years as bvy
import solr_lookup
from solr_lookup import SolrLookup
from title_normalize import normalize_title_key

solr_lookup.SOLR_TIMEOUT = 60
bvy.GROBID_HOSTS = ["http://localhost:8070", "http://localhost:8071",
                    "http://localhost:8072", "http://localhost:8073"]
bvy.GROBID_ENDPOINT = "processReferences"
bvy.GROBID_TIMEOUT = 120

BASE = "/space/eric/citation_data/arxiv/pdf/new"
MONTH = os.environ.get("TC_MONTH", "2507")
K = int(os.environ.get("TC_K", "8"))

try:
    from journal_authority import resolve as jresolve
except Exception:
    jresolve = lambda x: None

rng = random.Random(11)
tmp = tempfile.mkdtemp(prefix="tc_")
paths = []
with tarfile.open("%s/%s.tar" % (BASE, MONTH)) as tf:
    v1 = [m for m in tf if m.isfile() and m.name.endswith("v1.pdf")]
    for m in rng.sample(v1, min(K, len(v1))):
        try:
            tf.extract(m, tmp)
            paths.append(pathlib.Path(tmp) / m.name.lstrip("./"))
        except Exception:
            pass
print("papers: %d from %s" % (len(paths), MONTH), flush=True)

solr = SolrLookup()
g = collections.Counter()
examples = []

for p in paths:
    tei = bvy.grobid_process(p)
    refs = bvy.parse_tei_refs(tei) if tei else []
    if not refs:
        continue
    res = bvy.verify_refs(refs, solr)
    for pr in res.get("per_ref", []):
        if not pr.get("found") or pr.get("method") != "doi":
            continue
        r = refs[pr["i"]]
        g["doi_matched"] += 1
        ct = getattr(r, "title", None)
        if not ct:
            g["no_title"] += 1
            continue
        g["has_title"] += 1
        toks = normalize_title_key(ct).split()
        if len(toks) < 4:
            g["fails_min4_tokens"] += 1
            if len(examples) < 8:
                examples.append(("<4 tokens", ct[:60]))
            continue
        g["passes_min4"] += 1
        if jresolve(ct):
            g["skipped_journalish"] += 1
            if len(examples) < 8:
                examples.append(("journal-like", ct[:60]))
            continue
        g["ELIGIBLE_for_title_check"] += 1

print()
print("DOI-matched refs examined      : %d" % g["doi_matched"])
print("  no title extracted           : %d" % g["no_title"])
print("  has a title                  : %d" % g["has_title"])
print("    fails the >=4-token gate   : %d" % g["fails_min4_tokens"])
print("    passes >=4 tokens          : %d" % g["passes_min4"])
print("      skipped as journal-like  : %d" % g["skipped_journalish"])
print("  ==> ELIGIBLE for title check : %d" % g["ELIGIBLE_for_title_check"])
d = g["doi_matched"] or 1
print()
print("ELIGIBILITY RATE: %.1f%% of DOI-matched refs can ever raise a title mismatch"
      % (100.0 * g["ELIGIBLE_for_title_check"] / d))
print()
print("VERDICT: if this rate is high, the 0%% in Pass 1 is a real finding.")
print("         if it is low, the hijacking channel is NOT being measured.")
if examples:
    print("\nsuppressed examples:")
    for why, t in examples:
        print("   [%-12s] %s" % (why, t))
shutil.rmtree(tmp, ignore_errors=True)
