#!/usr/bin/env python3
"""
residual_crossref.py — powered Crossref verification of a random sample of the 216,017 residual refs.
Gentle: single process, API-only (no tmpfs/FTS/shards). Crossref clears real PUBLISHED works whose
titles the local index missed via mangling. What Crossref ALSO misses (preprints/books/obscure) is the
'deep residual' -> dumped for web adjudication. Weighted to LLM era. Estimates the true-fab fraction.
"""
import json, os, re, glob, random, urllib.request, urllib.parse, time, collections, difflib
POOL=glob.glob("/space/rwang/_speedtest/fullclass/residual_*.jsonl")
MAILTO="rwang@insilicom.com"; SAMPLE=600
STOP=set("the a an of for and or to in on with using via based from into over under between is are we our this that new".split())
def toks(s):
    s=re.sub(r'(\w)-\s*(\w)',r'\1\2',s or "")
    return [w for w in re.split(r'[^a-z0-9]+',s.lower()) if len(w)>2 and w not in STOP]
def trig(s):
    s=re.sub(r'[^a-z0-9]','',(s or "").lower()); return {s[i:i+3] for i in range(len(s)-2)}
def tjac(a,b):
    ta,tb=trig(a),trig(b); return len(ta&tb)/len(ta|tb) if (ta|tb) else 0
def contain(ct,c): cs=set(toks(c)); return sum(1 for w in ct if w in cs)/len(ct) if ct else 0
def good(ref,c): return contain(toks(ref),c)>=0.65 or tjac(ref,c)>=0.55 or difflib.SequenceMatcher(None,ref.lower(),(c or '').lower()).ratio()>=0.8
def http(u):
    for a in range(4):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"insilicom-fabaudit/1.0 (mailto:%s)"%MAILTO}),timeout=30))
        except urllib.error.HTTPError as e:
            if e.code in (429,500,503): time.sleep(1.5*(a+1)); continue
            return None
        except Exception: time.sleep(1); continue
    return None
def crossref(raw,title):
    d=http("https://api.crossref.org/works?"+urllib.parse.urlencode({"query.bibliographic":(raw or title)[:400],"rows":5,"mailto":MAILTO}))
    if not d: return None
    for it in d.get("message",{}).get("items",[]):
        ct=(it.get("title") or [""])[0]
        if ct and good(title,ct): return ct
    return None

# load residual, weight to LLM era
rows=[]
for f in POOL:
    for l in open(f):
        try: rows.append(json.loads(l))
        except: pass
print("total residual loaded:",len(rows),flush=True)
recent=[r for r in rows if r.get("year","").isdigit() and int(r["year"])>=2022]
old=[r for r in rows if r.get("year","").isdigit() and int(r["year"])<2022]
random.seed(7); random.shuffle(recent); random.shuffle(old)
samp=recent[:int(SAMPLE*0.7)]+old[:SAMPLE-int(SAMPLE*0.7)]
print("sample: %d (recent %d + old %d)"%(len(samp),int(SAMPLE*0.7),SAMPLE-int(SAMPLE*0.7)),flush=True)

cleared=0; deep=[]; t0=time.time()
for i,r in enumerate(samp):
    if crossref(r.get("raw",""), r.get("title","")): cleared+=1
    else: deep.append(r)
    time.sleep(0.34)
    if (i+1)%100==0: print("  %d/%d cleared=%d deep=%d (%.0fs)"%(i+1,len(samp),cleared,len(deep),time.time()-t0),flush=True)
n=len(samp)
print("\n=== RESIDUAL SAMPLE (%d) ==="%n)
print("cleared by Crossref (real, published): %d (%.1f%%)"%(cleared,100*cleared/n))
print("deep residual (Crossref+local both miss): %d (%.1f%%)"%(len(deep),100*len(deep)/n))
print("  -> extrapolated deep residual in full 216,017: ~%d"%(round(216017*len(deep)/n)))
json.dump(deep, open("/space/rwang/_speedtest/deep_residual_sample.json","w"))
print("\n=== 40 deep-residual for web adjudication ===")
for r in deep[:40]: print("  [%s] %s :: %s"%(r.get("year"),r.get("title","")[:72],r.get("raw","")[:100]))
print("\nwrote deep_residual_sample.json (%d)"%len(deep))
