#!/usr/bin/env python3
"""Ground-truth test: run OUR detector on the NeurIPS papers GPTZero flagged, and
score its verdict on each human-verified fabricated citation."""
import json,re,tarfile,io,os,sys,collections
sys.path.insert(0,"/space/rwang/fake-citation-detector/scripts")
os.environ.setdefault("OA_LOCAL_INDEX","/space/rwang/oa_index/oa_index.db")
import batch_verify_years as bvy
import arxiv_sweep as asw

MAP=json.load(open("/space/rwang/_speedtest/neurips51_map.json"))
TEI="/space/eric/citation_data/arxiv/tei/new"
def toks(s):
    s=re.sub(r'(\w)-\s*(\w)',r'\1\2',s or "")
    return set(w for w in re.split(r'[^a-z0-9]+',s.lower()) if len(w)>2)
solr=asw._solr() if hasattr(asw,"_solr") else None

results=[]
for title,v in MAP.items():
    aid=v.get("arxiv")
    if not aid: continue
    m=re.match(r"(\d{2})(\d{2})\.",aid)
    if not m:
        results.append({"paper":title,"arxiv":aid,"status":"old-style-id-skip"}); continue
    yymm=m.group(1)+m.group(2)
    tarp=os.path.join(TEI,yymm+".tar.gz")
    if not os.path.exists(tarp):
        results.append({"paper":title,"arxiv":aid,"status":"no-tar-"+yymm}); continue
    member=None
    with tarfile.open(tarp,"r:gz") as tf:
        for n in tf.getnames():
            if re.match(r"\./"+re.escape(aid)+r"v\d+\.tei\.xml$",n): member=n
        if not member:
            results.append({"paper":title,"arxiv":aid,"status":"tei-missing"}); continue
        tei=tf.extractfile(member).read().decode("utf-8",errors="replace")
    refs=bvy.parse_tei_refs(tei)
    ver=bvy.verify_refs(refs,solr) if solr is not None else bvy.verify_refs(refs)
    per=ver.get("per_ref") or ver.get("refs") or []
    # score each known-fabricated citation
    for c in v["cites"]:
        ct=toks(c["cite"])
        best=None;bs=0
        for r in per:
            rt=toks((r.get("raw") or "")+" "+(r.get("ref_title") or ""))
            ov=len(ct&rt)/len(ct) if ct else 0
            if ov>bs: bs=ov;best=r
        if bs<0.4:
            verdict="NOT_EXTRACTED"
        elif best.get("found"):
            verdict="MISSED(matched real work: %s)"%str(best.get("matched_title",""))[:60]
        else:
            verdict="CAUGHT(not_found)"
        results.append({"paper":title[:60],"arxiv":aid,"status":"ok","cite":c["cite"][:100],
                        "overlap":round(bs,2),"verdict":verdict})
    print("done:",aid,title[:50],flush=True)

json.dump(results,open("/space/rwang/_speedtest/neurips_test_results.json","w"),indent=1)
cnt=collections.Counter(r["verdict"].split("(")[0] for r in results if r.get("status")=="ok")
print("\n=== DETECTOR vs 68 KNOWN FABRICATIONS ===")
for k,n in cnt.most_common(): print("  %-14s %d"%(k,n))
miss=[r for r in results if r.get("status")=="ok" and r["verdict"].startswith("MISSED")]
print("\n=== MISSED (matched a real work — hijacking blind spot) ===")
for r in miss: print("  [%s] %s\n      -> %s"%(r["arxiv"],r["cite"][:90],r["verdict"][:100]))
skip=[r for r in results if r.get("status")!="ok"]
print("\nskipped papers:",[(r["arxiv"],r["status"]) for r in skip])
