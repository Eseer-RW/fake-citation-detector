#!/usr/bin/env python3
"""Lag-controlled fab_candidate + title_hijack flag dump (2024-25 sample for hand-verify)."""
import json,glob,re,collections,random,os
D="/space/rwang/fake-citation-detector/scripts/arxiv_sweep_v8"
files=sorted(glob.glob(os.path.join(D,"refs_*.jsonl")))
owner={}
for f in files:
    got=set()
    for line in open(f):
        m=re.search(r'"month": ?"(\d{4})"',line)
        if m: got.add(m.group(1))
    for mo in got: owner.setdefault(mo,f)
FC=collections.defaultdict(lambda:[0,0])   # year -> [lag-robust academic refs, fab_candidate]
TH=[]                                       # title_hijack flags 2024-25
n=0
for f in files:
    for line in open(f):
        try:r=json.loads(line)
        except:continue
        mo=r.get("month")
        if not mo or owner.get(mo)!=f or r.get("nonacademic"): continue
        yr=2000+int(mo[:2]); n+=1
        cy=r.get("cited_year")
        try: cy=int(cy)
        except: cy=None
        if cy is not None and cy<=yr-2:
            FC[yr][0]+=1
            if not r.get("found") and r.get("not_found_reason")=="fab_candidate": FC[yr][1]+=1
        if r.get("fab_flag")=="title_hijack" and yr in (2024,2025):
            TH.append({"year":yr,"raw":(r.get("raw") or "")[:220],"ref_title":r.get("ref_title"),
                       "ref_doi":r.get("ref_doi"),"matched_title":r.get("matched_title"),"paper":r.get("paper")})
print("streamed",n)
print("\n=== LAG-CONTROLLED fab_candidate (cited_year <= citing-2) ===")
for yr in sorted(FC):
    t,b=FC[yr]; print("  %d: %8d refs, fab_cand %6d = %.3f%%"%(yr,t,b,100*b/t if t else 0))
random.seed(4); random.shuffle(TH)
json.dump(TH[:60],open("/space/rwang/_speedtest/th_verify_sample.json","w"),indent=0)
print("\ntitle_hijack flags 2024-25 total:",len(TH),"| sampled 60 -> th_verify_sample.json")
print("ROBUST1_DONE")
