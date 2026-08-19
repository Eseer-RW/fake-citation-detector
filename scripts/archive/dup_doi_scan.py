"""Quantify the OpenAlex duplicate-DOI defect: of DOIs in the wild, what fraction have
duplicate records in the Solr index, and what fraction have CONFLICTING metadata?"""
import json, random, sys, collections
sys.path.insert(0, "/home/rwang/fake-citation-detector/scripts")
import batch_verify_years as bvy
from solr_lookup import SolrLookup
solr = SolrLookup()

V11 = "/home/rwang/cross_year_study/results_v11.jsonl"
TARGET = 20000   # unique cited-reference DOIs to check

papers = [json.loads(l) for l in open(V11) if '"ok"' in l]
random.seed(21); random.shuffle(papers)

seen = set()
for p in papers:
    if len(seen) >= TARGET:
        break
    refs = bvy.crossref_refs(p["doi"])
    if not refs:
        continue
    for r in refs:
        d = getattr(r, "doi", None)
        if d:
            seen.add(d)
        if len(seen) >= TARGET:
            break
dois = list(seen)
print(f"collected {len(dois):,} unique cited DOIs; scanning Solr...", flush=True)

n = present = multi = conflict = 0
examples = []
for i, d in enumerate(dois, 1):
    recs = solr.all_by_doi(d)
    if not recs:
        continue
    n += 1
    if len(recs) > 1:
        multi += 1
        venues, years = set(), set()
        for rec in recs:
            v = rec.get("venue_name")
            v = (v[0] if isinstance(v, list) and v else v) or ""
            venues.add(v.lower().strip())
            y = rec.get("publication_year")
            if y:
                try: years.add(int(y))
                except Exception: pass
        vconf = len([v for v in venues if v]) > 1
        yconf = (max(years) - min(years) > 1) if len(years) > 1 else False
        if vconf or yconf:
            conflict += 1
            if len(examples) < 25:
                examples.append((d, [(rec.get("venue_name"), rec.get("publication_year")) for rec in recs]))
    if i % 2000 == 0:
        print(f"  scanned {i:,}/{len(dois):,}  present={n} multi={multi} conflict={conflict}", flush=True)

print("\n=== OPENALEX DUPLICATE-DOI DEFECT ===")
print(f"DOIs found in index:        {n:,}")
print(f"with duplicate records (>1): {multi:,}  ({100*multi/n:.2f}%)")
print(f"with CONFLICTING metadata:   {conflict:,}  ({100*conflict/n:.2f}%)")
print("\nsample conflicting DOIs:")
for d, recs in examples:
    print(f"  {d}: {recs}")
print("DONE")
