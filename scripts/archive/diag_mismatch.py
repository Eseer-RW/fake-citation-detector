import sys, os, subprocess, tempfile, pathlib, collections, re
sys.path.insert(0, "/space/rwang/fake-citation-detector/scripts")
os.chdir("/space/rwang/fake-citation-detector/scripts")
os.environ.setdefault("SKIP_OPENALEX_API", "1"); os.environ.setdefault("SKIP_CROSSREF_API", "1")
import batch_verify_years as bvy
from solr_lookup import SolrLookup
solr = SolrLookup()
bvy.verify_refs([], solr)  # warm

BASE = "/space/eric/citation_data/arxiv/pdf/new"
tar = f"{BASE}/2404.tar"   # post-LLM month
names = subprocess.run(f"tar -tf {tar} | grep 'v1\\.pdf$' | head -12", shell=True,
                       capture_output=True, text=True).stdout.split()
work = tempfile.mkdtemp(prefix="diagmm_")
subprocess.run(["tar", "-xf", tar, "-C", work] + names, check=True)

cat = collections.Counter()
by_method = collections.Counter()
examples = []
tot_refs = tot_found = tot_mm = 0
for n in names:
    p = pathlib.Path(work) / n.lstrip("./")
    if not p.exists(): continue
    tei = bvy.grobid_process(p)
    refs = bvy.parse_tei_refs(tei) if tei else []
    if not refs: continue
    res = bvy.verify_refs(refs, solr)
    tot_refs += res["total"]; tot_found += res["found"]; tot_mm += res["found_mismatch"]
    for mm in res.get("mismatches", []):
        by_method[mm.get("method")] += 1
        for iss in mm.get("issues", []):
            # categorize by the field named in the issue string
            low = iss.lower()
            if "year" in low: cat["year"] += 1
            elif "volume" in low: cat["volume"] += 1
            elif "journal" in low or "venue" in low: cat["journal"] += 1
            elif "author" in low: cat["author"] += 1
            elif "title" in low: cat["title"] += 1
            else: cat["other"] += 1
        if len(examples) < 25:
            examples.append((mm.get("method"), mm.get("issues")))

print(f"\npapers processed, refs={tot_refs} found={tot_found} found_mismatch={tot_mm} "
      f"({tot_mm/max(tot_found,1)*100:.1f}% of found)")
print("\n=== mismatch by MATCH METHOD (identity certainty) ===")
for k, v in by_method.most_common(): print(f"  {k:16} {v}")
print("\n=== issue field composition ===")
for k, v in cat.most_common(): print(f"  {k:10} {v}")
print("\n=== raw issue examples (method | issues) ===")
for m, iss in examples[:20]:
    print(f"  [{m}] {iss}")
print("DIAG_DONE")
