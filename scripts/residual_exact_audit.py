#!/usr/bin/env python3
"""
residual_exact_audit.py — of the 162 that survive the fixes, how many can STILL be
recovered by an EXACT match, and how many are a genuine wall?

The earlier coverage_audit over-projected because its "in-index" test was loose word
overlap. This uses the pipeline's REAL bar in two ways:

  1. PIPELINE RE-TEST. Re-run the live matcher on each residual ref (title route with
     year=None, and the metadata route for title-less refs). Anything it now finds was a
     contention miss, not a real failure.

  2. EXACT-KEY PRESENCE. For refs the pipeline still misses, query Solr and accept only a
     doc whose normalize_title_key(stored) == normalize_title_key(cited) -- byte-identical
     canonical key, exactly what _oa_title_phrase requires. This is the honest test of
     "exact-matchable but missed" (a real bug) vs "not exact-matchable" (correctly rejected
     or genuinely absent).

Split the whole thing by the nonacademic flag, because the 46 nonacademic refs raise a
specific question: is their lookup being SUPPRESSED by the filter (unblock = free win),
or do they fail the matcher on their own merits (filter is irrelevant to their FPR)?
"""
import json, sys, os, re, collections

sys.path.insert(0, "/space/rwang/fake-citation-detector/scripts")
os.chdir("/space/rwang/fake-citation-detector/scripts")
os.environ.setdefault("SKIP_OPENALEX_API", "1")
os.environ.setdefault("SKIP_CROSSREF_API", "1")

import solr_lookup
solr_lookup.SOLR_TIMEOUT = 60
from solr_lookup import SolrLookup
import integrated_lookup as IL
from title_normalize import normalize_title_key
import urllib.parse, urllib.request

IN = "/space/rwang/_speedtest/fpr_false_alarms.jsonl"
SOLR = "http://galaxy:8983/solr/openalexWorks/select"
rows = [json.loads(l) for l in open(IN)]
solr = SolrLookup()
lk = IL.IntegratedLookup(solr=solr)


def esc(s):
    return re.sub(r'([+\-!(){}\[\]^"~*?:\\/])', r'\\\1', str(s))


def exact_key_in_index(title):
    """True iff a doc exists whose canonical key equals the cited title's key."""
    key = normalize_title_key(title)
    if not key or len(key.split()) < 4:
        return None                        # too generic to assert either way
    p = [("q", 'title:"%s"' % key.replace('"', " ")), ("rows", "10"),
         ("wt", "json"), ("facet", "false"), ("hl", "false"), ("fl", "title")]
    url = SOLR + "?" + urllib.parse.urlencode(p)
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            docs = json.load(r)["response"]["docs"]
    except Exception:
        return None
    for d in docs:
        t = d.get("title")
        t = t[0] if isinstance(t, list) and t else t
        if normalize_title_key(t or "") == key:
            return True
    return False


class R:  # minimal ref shape for the matcher
    pass


def mkref(r):
    o = R()
    o.title = r.get("title"); o.journal = r.get("journal"); o.volume = r.get("volume")
    o.year = r.get("year"); o.first_page = r.get("first_page")
    o.first_author = r.get("first_author"); o.doi = r.get("doi"); o.raw = r.get("raw") or ""
    return o


CLS = collections.defaultdict(collections.Counter)   # group -> class -> n
EX = collections.defaultdict(list)

for r in rows:
    grp = "nonacademic" if r.get("nonacademic") else "academic"
    t = (r.get("title") or "").strip()
    ref = mkref(r)

    # 1. does the LIVE matcher find it now?
    hit = False
    try:
        if t:
            res = lk.by_title_exact(t, year=None, journal=ref.journal, author=ref.first_author)
            hit = bool(res and res.found)
        if not hit and hasattr(lk, "oa_by_metadata"):
            res2 = lk.oa_by_metadata(ref)
            hit = hit or bool(res2 and res2.found)
    except Exception:
        pass

    if hit:
        cls = "A-RECOVERABLE-NOW (matcher finds it; dump miss was contention)"
    elif not t:
        cls = "E-no-title (metadata route only)"
    else:
        present = exact_key_in_index(t)
        if present is True:
            cls = "B-EXACT-KEY-IN-INDEX but matcher misses (BUG)"
        elif present is None:
            cls = "D-title-too-generic-to-assert"
        else:
            cls = "C-not-exact-matchable (absent or only similar titles)"

    CLS[grp][cls] += 1
    if len(EX[cls]) < 5:
        EX[cls].append("[%s] %s  (%s, %s)" % (grp[:4], t[:52] or "(no title)",
                                              r.get("journal") or "-", r.get("year")))

print("=" * 74)
print("RESIDUAL 162 — exact-recoverability, split by nonacademic flag")
print("=" * 74)
allc = sorted({c for g in CLS.values() for c in g})
tot = collections.Counter()
for grp in ("academic", "nonacademic"):
    n = sum(CLS[grp].values())
    print("\n%s  (n=%d)" % (grp.upper(), n))
    for c in allc:
        if CLS[grp][c]:
            print("   %-58s %3d" % (c, CLS[grp][c]))
            tot[c] += CLS[grp][c]

print("\n" + "=" * 74)
print("TOTALS")
for c in sorted(tot, key=lambda c: -tot[c]):
    print("   %-58s %3d" % (c, tot[c]))

rec = tot.get("A-RECOVERABLE-NOW (matcher finds it; dump miss was contention)", 0)
bug = tot.get("B-EXACT-KEY-IN-INDEX but matcher misses (BUG)", 0)
print("\nfurther-recoverable by fixing matching : %d (contention %d + exact-key-bug %d)"
      % (rec + bug, rec, bug))
print("genuine wall (not exact-matchable)     : %d"
      % (tot.get("C-not-exact-matchable (absent or only similar titles)", 0)))

print("\nEXAMPLES")
for c in sorted(EX, key=lambda c: -tot[c]):
    print("\n--- %s ---" % c)
    for e in EX[c]:
        print("   " + e)
