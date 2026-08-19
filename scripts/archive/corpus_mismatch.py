"""Quantify metadata mismatches across a corpus sample and surface the worst cases."""
import json, random, sys, collections, os
os.environ["SKIP_OPENALEX_API"] = "1"
sys.path.insert(0, "/home/rwang/fake-citation-detector/scripts")
import batch_verify_years as bvy
from solr_lookup import SolrLookup
solr = SolrLookup()

V11 = "/home/rwang/cross_year_study/results_v11.jsonl"
K = 500

papers = [json.loads(l) for l in open(V11) if '"ok"' in l]
random.seed(9); random.shuffle(papers)

tot_refs = found = found_mm = nf = 0
field = collections.Counter()
egregious = []      # references with 2+ mismatched fields
big_year = []       # year off by >= 10
used = 0
for p in papers:
    if used >= K:
        break
    refs = bvy.crossref_refs(p["doi"])
    if not refs:
        continue
    used += 1
    out = bvy.verify_refs(refs, solr)
    tot_refs += out["total"]; found += out["found"]; nf += out["not_found"]
    found_mm += out["found_mismatch"]
    for m in out["mismatches"]:
        for iss in m["issues"]:
            field[iss.split(":")[0]] += 1
            if iss.startswith("year:") and "off by" in iss:
                try:
                    d = int(iss.split("off by")[1].split(")")[0])
                    if d >= 10:
                        big_year.append((m["cited_doi"], iss))
                except Exception:
                    pass
        if len(m["issues"]) >= 2:
            egregious.append((p["doi"], m["cited_doi"], m["issues"]))
    if used % 50 == 0:
        print(f"  papers={used} refs={tot_refs} found={found} mismatch={found_mm}", flush=True)

print("\n=== CORPUS METADATA-MISMATCH SUMMARY ===")
print(f"papers:               {used:,}")
print(f"references checked:    {tot_refs:,}")
print(f"  found (matched):     {found:,}")
print(f"  FOUND_MISMATCH:      {found_mm:,}  ({100*found_mm/found:.2f}% of matched)")
print(f"  not found:           {nf:,}")
print("\nmismatched field breakdown (per-field occurrences):")
for k, c in field.most_common():
    print(f"  {k:<10} {c:,}")
print(f"\nmulti-field (>=2) mismatches: {len(egregious):,}")
for doi, cd, iss in egregious[:12]:
    print(f"  [{cd}]  {iss}")
print(f"\nlarge year gaps (>=10y): {len(big_year):,}")
for cd, iss in big_year[:10]:
    print(f"  [{cd}]  {iss}")
print("DONE")
