"""
per_year_validation.py — measure the CANDIDATE true-not-found rate per year, the
quantity Zhao et al. isolate. Aggregate not-found is dominated by declining coverage
gaps; this strips those out and asks whether the fabrication-candidate slice rises
post-LLM.

For each year, sample K papers, reproduce v11 DOI matching (Solr + local Crossref),
and for each unmatched reference classify it:
  - non-academic (heuristic)        -> not a paper
  - no title                        -> not assessable
  - title found in Crossref 179M    -> real, DOI-less (coverage gap)
  - title, no match anywhere        -> CANDIDATE true-not-found
Then regress the candidate rate (candidate / all citations) over year.
"""
import json, random, sys, collections
sys.path.insert(0, "/home/rwang/fake-citation-detector/scripts")
import batch_verify_years as bvy
from solr_lookup import SolrLookup
from crossref_lookup import batch_crossref, CrossrefLookup

solr = SolrLookup()
xr = CrossrefLookup()
V11 = "/home/rwang/cross_year_study/results_v11.jsonl"
K_PER_YEAR = 300

by_year = collections.defaultdict(list)
for l in open(V11):
    try: r = json.loads(l)
    except Exception: continue
    if r.get("status") == "ok" and r.get("total", 0) >= 5:
        by_year[r.get("year")].append(r)

def doi_matched(ref):
    d = getattr(ref, "doi", None)
    if not d:
        return False
    try:
        if solr.by_doi(d).found:
            return True
    except Exception:
        pass
    try:
        if batch_crossref([ref])[0].found:
            return True
    except Exception:
        pass
    return False

res = {}
for y in sorted(by_year):
    papers = by_year[y]
    random.seed(y); random.shuffle(papers)
    tot = nf = cand = recov = noti = nonac = used = 0
    for p in papers[:K_PER_YEAR]:
        refs = bvy.crossref_refs(p["doi"])
        if not refs:
            continue
        used += 1
        for r in refs:
            tot += 1
            if doi_matched(r):
                continue
            nf += 1
            if bvy.is_likely_nonacademic(r):
                nonac += 1; continue
            t = getattr(r, "title", None)
            if not t:
                noti += 1; continue
            hit = None
            try:
                hit = xr.by_title(t, getattr(r, "year", None))
            except Exception:
                pass
            if hit and hit.found:
                recov += 1
            else:
                cand += 1
    res[y] = dict(papers=used, total=tot, nf=nf, cand=cand, recov=recov, noti=noti, nonac=nonac)
    print(f"{y}: papers={used} refs={tot} nf={nf} ({100*nf/tot:.1f}%)  "
          f"CANDIDATE={cand} ({100*cand/tot:.2f}% of all citations)  "
          f"recoverable={recov} no_title={noti} nonacad={nonac}", flush=True)

import numpy as np
ys = sorted(res)
crate = [100*res[y]["cand"]/res[y]["total"] for y in ys]
nfrate = [100*res[y]["nf"]/res[y]["total"] for y in ys]
print("\n=== PER-YEAR SUMMARY ===")
print("year   not_found%   candidate%")
for y, nr, cr in zip(ys, nfrate, crate):
    print(f"  {y}    {nr:5.1f}%      {cr:5.2f}%")
slope = np.polyfit(np.array(ys)-2020, np.array(crate), 1)[0]
pre_c = sum(res[y]["cand"] for y in ys if y <= 2022); pre_t = sum(res[y]["total"] for y in ys if y <= 2022)
post_c = sum(res[y]["cand"] for y in ys if y >= 2023); post_t = sum(res[y]["total"] for y in ys if y >= 2023)
print(f"\ncandidate-rate slope: {slope:+.3f} pp/year")
print(f"pre-LLM  (2020-22) candidate rate: {100*pre_c/pre_t:.2f}%")
print(f"post-LLM (2023-25) candidate rate: {100*post_c/post_t:.2f}%")
print(f"post - pre: {100*post_c/post_t - 100*pre_c/pre_t:+.2f}pp")
print("\nINTERPRETATION: positive slope / post>pre => Zhao-style rise hidden under the "
      "aggregate; flat/negative => no fabrication trend even in the isolated slice.")
print("DONE")
