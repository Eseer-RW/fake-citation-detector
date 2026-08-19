#!/usr/bin/env python3
"""
hunt_prep.py — build two fabrication-hunting pools and dump as JSON for parallel web-verification.
  POOL A (targeted): not-found title-bearing refs from LLM-CONTAMINATED papers (papers where GROBID
    scraped LLM prompt/output text as a 'reference' -> paper was LLM-written -> high fab yield).
  POOL B (scaled): recent (2023-26) residual refs that MISS both the local 484M index AND Crossref
    (the deep residual), Crossref-checked here so web agents focus on genuine hard cases.
"""
import json, os, re, math, glob, random, urllib.request, urllib.parse, time, collections
D="/space/rwang/fake-citation-detector/scripts/arxiv_sweep_v7"; MAILTO="rwang@insilicom.com"
OUT="/space/rwang/_speedtest/hunt"; os.makedirs(OUT,exist_ok=True)
months=[];y,m=9,1
while (y,m)<=(26,6):
    months.append("%02d%02d"%(y,m));m+=1
    if m>12:m=1;y+=1
N=30;sz=math.ceil(len(months)/N)
files=[os.path.join(D,"refs_%s_%s_k1000.jsonl"%(months[i],months[i:i+sz][-1])) for i in range(0,len(months),sz)]
files=[f for f in files if os.path.exists(f)]

# LLM-contamination signature: text that is prompt/model-output, not a citation
CONTAM=re.compile(r"### instruction|as an ai language model|i cannot fulfill|i['’]m sorry,? (but )?i|"
                  r"certainly! here|here is (the|a) |let['’]s first (find|calculate|determine)|"
                  r"respond to the need|carefully review the|as a language model|"
                  r"i (do not|don['’]t) have (access|the ability)|step 1:.*step 2:", re.I)
ARTIFACTREF=re.compile(r"### instruction|as an ai|language model.{0,20}(cannot|sorry)|let['’]s first|carefully review|respond to the need|\bMUST contain\b|instruction:|original-substring", re.I)

# pass 1: find contaminated papers
contaminated=set()
for f in files:
    for line in open(f):
        try:r=json.loads(line)
        except:continue
        blob=(r.get("ref_title") or "")+" "+(r.get("raw") or "")
        if CONTAM.search(blob) and r.get("paper"): contaminated.add(r["paper"])
print("contaminated papers:",len(contaminated),flush=True)

# pass 2: real citation refs (not-found) from contaminated papers
poolA=[]
for f in files:
    for line in open(f):
        try:r=json.loads(line)
        except:continue
        if r.get("paper") not in contaminated: continue
        if r.get("nonacademic") or r.get("found") or not r.get("has_title"): continue
        t=(r.get("ref_title") or "").strip()
        if len(t)<15 or ARTIFACTREF.search(t+" "+(r.get("raw") or "")): continue
        poolA.append({"paper":r["paper"],"year":str(2000+int(r["month"][:2])) if r.get("month") else "?","title":t,"raw":(r.get("raw") or "")[:220]})
print("pool A (contaminated-paper not-found refs):",len(poolA),flush=True)
json.dump(poolA, open(os.path.join(OUT,"poolA_contaminated.json"),"w"))

# pool B: recent residual, Crossref-check, keep deep residual
STOP=set("the a an of for and or to in on with using via based from into over under between is are we our this that new".split())
def toks(s):
    s=re.sub(r'(\w)-\s*(\w)',r'\1\2',s or ""); return [w for w in re.split(r'[^a-z0-9]+',s.lower()) if len(w)>2 and w not in STOP]
def trig(s):
    s=re.sub(r'[^a-z0-9]','',(s or "").lower()); return {s[i:i+3] for i in range(len(s)-2)}
def good(ref,c):
    ct=toks(ref); cs=set(toks(c)); cont=sum(1 for w in ct if w in cs)/len(ct) if ct else 0
    ta,tb=trig(ref),trig(c); tj=len(ta&tb)/len(ta|tb) if (ta|tb) else 0
    return cont>=0.65 or tj>=0.55
def http(u):
    for a in range(4):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"insilicom-fabaudit/1.0 (mailto:%s)"%MAILTO}),timeout=30))
        except urllib.error.HTTPError as e:
            if e.code in (429,500,503): time.sleep(1.5*(a+1)); continue
            return None
        except: time.sleep(1); continue
    return None
def cr_miss(raw,title):
    d=http("https://api.crossref.org/works?"+urllib.parse.urlencode({"query.bibliographic":(raw or title)[:400],"rows":5,"mailto":MAILTO}))
    if not d: return True
    for it in d.get("message",{}).get("items",[]):
        if good(title,(it.get("title") or [""])[0]): return False
    return True
# gather recent residual from full dump
recent=[]
NONART=re.compile(r'[{}<>]|:-|::|=|instruction:|MUST|Eq\.|Via |Institut|Departamento|Dipartimento|Email|affiliated|Slide present|Workshop on|Seminar|Handbook|thesis|dataset|R package',re.I)
for f in glob.glob(os.path.join(D.replace("fake-citation-detector/scripts/arxiv_sweep_v7","_speedtest/fullclass"),"residual_*.jsonl")) or glob.glob("/space/rwang/_speedtest/fullclass/residual_*.jsonl"):
    for line in open(f):
        try:r=json.loads(line)
        except:continue
        yr=r.get("year","")
        if not (yr.isdigit() and int(yr)>=2023): continue
        t=r.get("title","")
        if len(t)<25 or NONART.search(t): continue
        recent.append(r)
random.seed(11); random.shuffle(recent)
poolB=[]; checked=0
for r in recent:
    if len(poolB)>=700 or checked>=2000: break
    checked+=1
    if cr_miss(r.get("raw",""), r.get("title","")): poolB.append(r)
    time.sleep(0.33)
    if checked%200==0: print("  crossref-checked %d, deep so far %d"%(checked,len(poolB)),flush=True)
json.dump(poolB, open(os.path.join(OUT,"poolB_deepresidual.json"),"w"))
print("pool B (recent deep residual):",len(poolB),"from",checked,"checked",flush=True)
print("PREP_DONE",flush=True)
