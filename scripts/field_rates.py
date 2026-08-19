#!/usr/bin/env python3
"""Per-FIELD fab_candidate rates by year: sample citing papers from v8b, fetch arXiv
primary_category via bulk id_list API (100/call, 3s politeness), join, output
field x year rates + pre/post-LLM increase."""
import json,glob,re,os,collections,random,time,urllib.request,urllib.parse,sys
D="/space/rwang/fake-citation-detector/scripts/arxiv_sweep_v8b"
OUT="/space/rwang/_speedtest/fieldrates"
os.makedirs(OUT,exist_ok=True)
CAT=os.path.join(OUT,"categories.json")

files=sorted(glob.glob(os.path.join(D,"refs_*.jsonl")))
owner={}
for f in files:
    got=set()
    for line in open(f):
        m=re.search(r'"month": ?"(\d{4})"',line)
        if m: got.add(m.group(1))
    for mo in got: owner.setdefault(mo,f)

# pass 1: per-paper tallies (refs, fab_candidates) keyed by (year, paper)
tal=collections.defaultdict(lambda:[0,0])
for f in files:
    for line in open(f):
        try:r=json.loads(line)
        except:continue
        mo=r.get("month"); p=r.get("paper")
        if not mo or not p or owner.get(mo)!=f or r.get("nonacademic"): continue
        yr=2000+int(mo[:2])
        t=tal[(yr,p)]; t[0]+=1
        if (not r.get("found")) and r.get("not_found_reason")=="fab_candidate": t[1]+=1
print("papers tallied:",len(tal),flush=True)

# stratified paper sample
byyr=collections.defaultdict(list)
for (yr,p),t in tal.items(): byyr[yr].append(p)
random.seed(9)
sample=[]
for yr,ps in byyr.items():
    random.shuffle(ps); sample += [(yr,p) for p in ps[:5000]]
print("sampled papers:",len(sample),flush=True)

# fetch categories (resumable)
cats={}
if os.path.exists(CAT): cats=json.load(open(CAT))
ids=[p for _,p in sample if p not in cats]
print("to fetch:",len(ids),flush=True)
UA={"User-Agent":"insilicom-fieldrates/1.0 (mailto:rwang@insilicom.com)"}
for i in range(0,len(ids),100):
    chunk=ids[i:i+100]
    u="http://export.arxiv.org/api/query?"+urllib.parse.urlencode({"id_list":",".join(chunk),"max_results":len(chunk)})
    try:
        xml=urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=60).read().decode("utf-8","replace")
        for ent in re.findall(r"<entry>(.*?)</entry>",xml,re.S):
            mid=re.search(r"<id>[^<]*?abs/([\d.]+)",ent)
            mc=re.search(r'<arxiv:primary_category[^>]*term="([^"]+)"',ent)
            if mid and mc: cats[mid.group(1)]=mc.group(1)
    except Exception as e:
        time.sleep(10)
    time.sleep(3)
    if (i//100)%50==0:
        json.dump(cats,open(CAT,"w")); print("  fetched %d/%d (%d cats)"%(i+100,len(ids),len(cats)),flush=True)
json.dump(cats,open(CAT,"w"))
print("categories:",len(cats),flush=True)

# aggregate: field = category prefix (cs, math, cond-mat, astro-ph, hep, stat, eess, q-bio, econ/q-fin, physics-other)
def fld(c):
    c=c.split(".")[0]
    if c in ("hep-th","hep-ph","hep-ex","hep-lat"): return "hep"
    if c in ("astro-ph",): return "astro"
    if c in ("cond-mat",): return "cond-mat"
    if c in ("q-fin","econ"): return "econ/q-fin"
    if c in ("cs",): return "cs"
    if c in ("math","math-ph"): return "math"
    if c in ("stat",): return "stat"
    if c in ("eess",): return "eess"
    if c in ("q-bio",): return "q-bio"
    return "physics-other"
FY=collections.defaultdict(lambda:[0,0])
for (yr,p) in sample:
    c=cats.get(p)
    if not c: continue
    t=tal[(yr,p)]
    k=(fld(c),yr); FY[k][0]+=t[0]; FY[k][1]+=t[1]
fields=sorted(set(k[0] for k in FY))
print("\n=== fab_candidate rate (%) by FIELD x YEAR ===")
print("field        "+"  ".join("%6d"%y for y in range(2019,2027)))
res={}
for f2 in fields:
    row=[]
    for y in range(2019,2027):
        t,b=FY.get((f2,y),[0,0])
        row.append(100*b/t if t else None)
    res[f2]=row
    print("%-12s "%f2+"  ".join(("%6.2f"%v if v is not None else "     -") for v in row))
print("\n=== pre-LLM (2019-22) vs post (2023-25) + increase ===")
for f2 in fields:
    pre_t=sum(FY.get((f2,y),[0,0])[0] for y in range(2019,2023)); pre_b=sum(FY.get((f2,y),[0,0])[1] for y in range(2019,2023))
    post_t=sum(FY.get((f2,y),[0,0])[0] for y in range(2023,2026)); post_b=sum(FY.get((f2,y),[0,0])[1] for y in range(2023,2026))
    if pre_t>10000 and post_t>10000:
        pre=100*pre_b/pre_t; post=100*post_b/post_t
        print("  %-12s pre %5.2f%%  post %5.2f%%  delta %+5.2fpp  (%+.0f%%)  [n=%dk/%dk]"%(f2,pre,post,post-pre,100*(post-pre)/pre,pre_t//1000,post_t//1000))
print("FIELDRATES_DONE")
