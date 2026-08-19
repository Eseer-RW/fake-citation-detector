import json,glob,re,random,os,collections
D="/space/rwang/fake-citation-detector/scripts/arxiv_sweep_v8"
files=sorted(glob.glob(os.path.join(D,"refs_*.jsonl")))
owner={}
for f in files:
    got=set()
    for line in open(f):
        m=re.search(r'"month": ?"(\d{4})"',line)
        if m: got.add(m.group(1))
    for mo in got: owner.setdefault(mo,f)
llm=[]; ctrl=[]
for f in files:
    for line in open(f):
        try:r=json.loads(line)
        except:continue
        mo=r.get("month")
        if not mo or owner.get(mo)!=f or r.get("nonacademic") or r.get("found"): continue
        if r.get("not_found_reason")!="fab_candidate": continue
        cy=r.get("cited_year")
        try: cy=int(cy)
        except: cy=None
        yr=2000+int(mo[:2])
        if cy is None or cy>yr-2: continue   # lag-robust only
        rec={"year":yr,"raw":(r.get("raw") or "")[:220],"ref_title":r.get("ref_title"),"paper":r.get("paper")}
        if 2023<=yr<=2025: llm.append(rec)
        elif 2019<=yr<=2021: ctrl.append(rec)
random.seed(12); random.shuffle(llm); random.shuffle(ctrl)
out=[dict(x,group="llm") for x in llm[:100]]+[dict(x,group="ctrl") for x in ctrl[:50]]
json.dump(out,open("/space/rwang/_speedtest/fc_verify_sample.json","w"),indent=0)
print("llm pool:",len(llm),"ctrl pool:",len(ctrl),"-> sampled 150")
print("DUMP_DONE")
