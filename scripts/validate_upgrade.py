#!/usr/bin/env python3
"""Validate the UPGRADED verify_refs (fab_flag = not_found_candidate|title_hijack|author_hijack)
against the GPTZero NeurIPS ground truth. Caches TEI to disk for fast iteration."""
import json,re,os,sys,tarfile,collections
sys.path.insert(0,"/space/rwang/fake-citation-detector/scripts")
os.environ.setdefault("OA_LOCAL_INDEX","/space/rwang/oa_index/oa_index.db")
import batch_verify_years as bvy, arxiv_sweep as asw
MAP=json.load(open("/space/rwang/_speedtest/neurips51_map.json"))
TEI="/space/eric/citation_data/arxiv/tei/new"; TC="/space/rwang/_speedtest/hijack/tei_cache"
os.makedirs(TC,exist_ok=True)
def toks(s):
    s=re.sub(r'(\w)-\s*(\w)',r'\1\2',s or "")
    return set(w for w in re.split(r'[^a-z0-9]+',s.lower()) if len(w)>2)
solr=asw._solr()
stat=collections.Counter(); fab_rows=[]; norm=collections.Counter()
for title,v in MAP.items():
    aid=v.get("arxiv")
    if not aid or not re.match(r"\d{4}\.",aid): continue
    cp=os.path.join(TC,aid+".xml")
    if os.path.exists(cp): tei=open(cp,encoding="utf-8").read()
    else:
        tarp=os.path.join(TEI,aid.split(".")[0]+".tar.gz")
        if not os.path.exists(tarp): continue
        member=None
        with tarfile.open(tarp,"r:gz") as tf:
            for n in tf.getnames():
                if re.match(r"\./"+re.escape(aid)+r"v\d+\.tei\.xml$",n): member=n
            if not member: continue
            tei=tf.extractfile(member).read().decode("utf-8",errors="replace")
        open(cp,"w",encoding="utf-8").write(tei)
    per=bvy.verify_refs(bvy.parse_tei_refs(tei),solr).get("per_ref") or []
    fabidx=set()
    for c in v["cites"]:
        ft=toks(c["cite"]); best=None;bs=0
        for j,r in enumerate(per):
            rt=toks((r.get("raw") or "")+" "+(r.get("ref_title") or ""))
            ov=len(ft&rt)/len(ft) if ft else 0
            if ov>bs: bs=ov;best=j
        if bs>=0.4: fabidx.add(best)
        else: stat["fab_NOT_EXTRACTED"]+=1
    for j,r in enumerate(per):
        flag=r.get("fab_flag"); found=r.get("found")
        if j in fabidx:
            if not found: stat["fab_caught_notfound"]+=1; fab_rows.append(("nf",r.get("not_found_reason"),(r.get("raw") or "")[:70]))
            elif flag in ("author_hijack","title_hijack"): stat["fab_caught_%s"%flag]+=1; fab_rows.append((flag,None,(r.get("raw") or "")[:70]))
            else: stat["fab_MISSED"]+=1; fab_rows.append(("MISSED",None,(r.get("raw") or "")[:70]))
        else:
            if found and flag in ("author_hijack","title_hijack"): norm["hardflag_%s"%flag]+=1
            elif found: norm["found_clean"]+=1
            elif r.get("not_found_reason")=="fab_candidate": norm["nf_fabcand"]+=1
            elif not found: norm["nf_other"]+=1
    print("done",aid,flush=True)
print("\n=== UPGRADED DETECTOR vs GROUND TRUTH ===")
for k in sorted(stat): print("  %-24s %d"%(k,stat[k]))
caught=sum(v for k,v in stat.items() if k.startswith("fab_caught"))
missed=stat["fab_MISSED"]
print("  RECALL (extractable): %d/%d = %.0f%%"%(caught,caught+missed,100*caught/max(1,caught+missed)))
print("\n=== normal refs (FPR view) ===")
for k in sorted(norm): print("  %-24s %d"%(k,norm[k]))
hf=norm["hardflag_author_hijack"]+norm["hardflag_title_hijack"]
print("  hard-flag FPR on found refs: %.2f%%"%(100*hf/max(1,hf+norm["found_clean"])))
print("\n=== fab not-found reasons (are fabs classified fab_candidate?) ===")
rc=collections.Counter(x[1] for x in fab_rows if x[0]=="nf"); print("  ",dict(rc))
print("VALIDATE_DONE")
