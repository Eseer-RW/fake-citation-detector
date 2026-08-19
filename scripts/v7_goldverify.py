#!/usr/bin/env python3
"""
v7_goldverify.py — Lancet-style gold-standard verification of a POWERED sample of the untested
fabrication space (non-DOI, title-bearing, not-found academic refs). For each ref, query TWO live
fuzzy databases — Crossref (published) + OpenAlex (preprints) — and accept a match on TITLE-TOKEN
overlap (not the API's own score, which false-matches). A ref found in NEITHER, after both fuzzy
searches, is a genuine fabrication candidate for final human/web adjudication.
Sample: pre-LLM control (2016-19) + LLM era (2023-26, weighted).
"""
import json, os, re, math, urllib.request, urllib.parse, time, random, difflib, collections, sys

D="/space/rwang/fake-citation-detector/scripts/arxiv_sweep_v7"
MAILTO="rwang@insilicom.com"
QUOTA={"2016":20,"2017":20,"2018":20,"2019":20, "2023":120,"2024":120,"2025":120,"2026":60}  # 500 total

months=[];y,m=9,1
while (y,m)<=(26,6):
    months.append("%02d%02d"%(y,m));m+=1
    if m>12:m=1;y+=1
N=30;sz=math.ceil(len(months)/N)
files=[os.path.join(D,"refs_%s_%s_k1000.jsonl"%(months[i],months[i:i+sz][-1])) for i in range(0,len(months),sz)]
files=[f for f in files if os.path.exists(f)]

STOP=set("the a an of for and or to in on with using via based from into over under between is are we our this that new".split())
NONART=re.compile(r'https?://|www\.|github|gitlab|\bmanual\b|datasheet|documentation|\bwiki\b|readme|'
                  r'user guide|toolkit|\bRFC\b|patent|\bthesis\b|dissertation|\bstandard\b|catalog', re.I)
def toks(s):
    s=re.sub(r'(\w)-\s*(\w)',r'\1\2',s or "")
    return [w for w in re.split(r'[^a-z0-9]+',s.lower()) if len(w)>2 and w not in STOP]
def norm(s):
    return re.sub(r'[^a-z0-9 ]+',' ',re.sub(r'(\w)-\s*(\w)',r'\1\2',(s or "").lower())).strip()
def is_match(ref_title, cand_title):
    t1=toks(ref_title);
    if not t1 or not cand_title: return False,0
    t2=set(toks(cand_title))
    contain=sum(1 for w in t1 if w in t2)/len(t1)
    ratio=difflib.SequenceMatcher(None,norm(ref_title),norm(cand_title)).ratio()
    return (contain>=0.65 or ratio>=0.80), max(contain,ratio)

def http(u):
    for attempt in range(5):
        try:
            req=urllib.request.Request(u,headers={"User-Agent":"insilicom-fabaudit/1.0 (mailto:%s)"%MAILTO})
            return json.load(urllib.request.urlopen(req,timeout=30))
        except urllib.error.HTTPError as e:
            if e.code in (429,500,503): time.sleep(2**attempt); continue
            return None
        except Exception: time.sleep(1); continue
    return None
def crossref(rawq, ref_title):
    d=http("https://api.crossref.org/works?"+urllib.parse.urlencode({"query.bibliographic":rawq[:400],"rows":4,"mailto":MAILTO}))
    if not d: return None
    for it in d.get("message",{}).get("items",[]):
        ct=(it.get("title") or [""])[0]
        ok,sc=is_match(ref_title,ct)
        if ok: return ("crossref",ct,sc)
    return None
def openalex(ref_title):
    if not ref_title: return None
    d=http("https://api.openalex.org/works?"+urllib.parse.urlencode({"search":ref_title[:300],"per-page":4,"mailto":MAILTO}))
    if not d: return None
    for it in d.get("results",[]):
        ct=it.get("title") or ""
        ok,sc=is_match(ref_title,ct)
        if ok: return ("openalex",ct,sc)
    return None

# build sample
buckets=collections.defaultdict(list)
for f in files:
    for line in open(f):
        try:r=json.loads(line)
        except:continue
        if r.get("nonacademic") or r.get("found") or r.get("has_doi") or not r.get("has_title"): continue
        yr=str(2000+int(r["month"][:2])) if r.get("month") else "0"
        if yr not in QUOTA: continue
        t=(r.get("ref_title") or "").strip()
        if len(toks(t))<3: continue
        if NONART.search((r.get("raw") or "")+" "+t): continue
        buckets[yr].append((yr,t,(r.get("raw") or "").strip()))
random.seed(20260808)
sample=[]
for yr,q in QUOTA.items():
    pool=buckets.get(yr,[]); random.shuffle(pool); sample+=pool[:q]
print("sample size: %d  (by year: %s)"%(len(sample), {y:min(len(buckets.get(y,[])),q) for y,q in QUOTA.items()}), flush=True)

res=[]; t0=time.time()
for i,(yr,t,raw) in enumerate(sample):
    hit=crossref(raw or t, t) or openalex(t)
    if hit: verdict="REAL"; src,ct,sc=hit
    else:   verdict="FAB_CANDIDATE"; src,ct,sc="none","",0
    res.append((yr,verdict,src,round(sc,2),t,ct,raw))
    time.sleep(0.34)   # polite pacing
    if (i+1)%50==0: print("  %d/%d  (%.0fs)"%(i+1,len(sample),time.time()-t0), flush=True)

# summary
per=collections.defaultdict(lambda:[0,0])  # year -> [n, fab]
for yr,v,src,sc,t,ct,raw in res:
    per[yr][0]+=1; per[yr][1]+= (1 if v=="FAB_CANDIDATE" else 0)
print("\n=== GOLD-STANDARD VERIFICATION (Crossref + OpenAlex fuzzy) ===")
nfab=sum(1 for r in res if r[1]=="FAB_CANDIDATE"); n=len(res)
print("year   n   fab_candidates  rate")
ctrl=[0,0]; llm=[0,0]
for yr in sorted(per):
    nn,ff=per[yr]; print("  %s  %3d   %3d           %.2f%%"%(yr,nn,ff,100*ff/nn if nn else 0))
    if yr<"2020": ctrl[0]+=nn; ctrl[1]+=ff
    else: llm[0]+=nn; llm[1]+=ff
print("\n  pre-LLM control (2016-19): %d/%d = %.2f%%"%(ctrl[1],ctrl[0],100*ctrl[1]/ctrl[0] if ctrl[0] else 0))
print("  LLM era (2023-26):        %d/%d = %.2f%%"%(llm[1],llm[0],100*llm[1]/llm[0] if llm[0] else 0))
print("  TOTAL fab-candidates: %d/%d = %.2f%%"%(nfab,n,100*nfab/n))
# Wilson 95% upper bound on total
p=nfab/n; z=1.96
import math as _m
den=1+z*z/n; centre=p+z*z/(2*n); half=z*_m.sqrt(p*(1-p)/n+z*z/(4*n*n))
print("  95%% CI on fab rate: [%.2f%%, %.2f%%]"%(100*(centre-half)/den,100*(centre+half)/den))
with open(os.path.join(D,"gold_verify.tsv"),"w") as o:
    o.write("year\tverdict\tsource\tscore\tref_title\tmatched_title\traw\n")
    for yr,v,src,sc,t,ct,raw in res:
        o.write("\t".join([yr,v,src,str(sc),t.replace(chr(9),' '),(ct or '').replace(chr(9),' '),raw.replace(chr(9),' ')[:200]])+"\n")
print("\n=== FAB_CANDIDATEs (need final human/web adjudication) ===")
for yr,v,src,sc,t,ct,raw in res:
    if v=="FAB_CANDIDATE": print("  [%s] %s :: %s"%(yr,t[:70],raw[:110]))
print("\nwrote gold_verify.tsv")
