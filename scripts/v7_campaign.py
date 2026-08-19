#!/usr/bin/env python3
"""
v7_campaign.py — SCALED fabrication campaign (~10k refs) on the recent non-DOI not-found bucket.
Two independent existence sources, NO throttling:
  A) Crossref live fuzzy-bibliographic (published works; lenient polite pool + mailto)
  B) local oa_fts.db (484M OpenAlex titles = preprints+articles, NO API), matched by
     token-containment OR char-trigram Jaccard (robust to GROBID token-welding like 'selfsupervised')
Plus an aggressive parse-junk/non-article filter (GROBID scrapes prompts/code/body text/tables as refs).
Whatever clears NEITHER source and isn't junk = residual -> hand/web verify a random subsample (stage 2).
Resumable: sample frozen to sample.tsv; verdicts appended to results.jsonl (skips done idx on restart).
"""
import json, os, re, math, sqlite3, random, time, urllib.request, urllib.parse, sys, collections

D="/space/rwang/fake-citation-detector/scripts/arxiv_sweep_v7"
OUTDIR="/space/rwang/_speedtest/campaign"; os.makedirs(OUTDIR,exist_ok=True)
SAMPLE_TSV=os.path.join(OUTDIR,"sample.tsv"); RESULTS=os.path.join(OUTDIR,"results.jsonl")
FTS="/space/rwang/oa_index/oa_fts.db"; MAILTO="rwang@insilicom.com"
QUOTA={"2015":250,"2016":250,"2017":250,"2018":250, "2023":2500,"2024":2500,"2025":2500,"2026":1500}  # ~10k

STOP=set("the a an of for and or to in on with using via based from into over under between is are we our this that new".split())
def toks(s):
    s=re.sub(r'(\w)-\s*(\w)',r'\1\2',s or "")
    return [w for w in re.split(r'[^a-z0-9]+',s.lower()) if len(w)>2 and w not in STOP]
def trig(s):
    s=re.sub(r'[^a-z0-9]','',(s or "").lower()); return {s[i:i+3] for i in range(len(s)-2)}
def tjac(a,b):
    ta,tb=trig(a),trig(b); return len(ta&tb)/len(ta|tb) if (ta|tb) else 0
def contain(ct,cand):
    cs=set(toks(cand)); return sum(1 for w in ct if w in cs)/len(ct) if ct else 0
def good_match(ref_title,cand):
    ct=toks(ref_title)
    return (contain(ct,cand)>=0.65) or (tjac(ref_title,cand)>=0.55)

NONART=re.compile(r'\bhandbook\b|\bencyclopedia\b|lecture notes|\bproof\b|\btheorem\b|\blemma\b|\bcorollary\b|'
                  r'\bvol\.|\bchapter\b|\bpress\b|springer|elsevier|wiley|\bthesis\b|dissertation|to appear|'
                  r'in press|private communication|\bbook\b|monograph|\bstandard\b|\bRFC\b|datasheet|user guide|'
                  r'\bmanual\b|technical report|tractatus|commentationes|\bed\.\b|editor|proceedings of the|'
                  r'market (research|report)|play\.google|app store|github\.com|\bdataset\b|R package|version \d', re.I)
CODEISH=re.compile(r'[{}<>]=?|:-|::|\)\s*:|_[a-z]+\(|->|instruction:|\bdef \b|MUST |labels:|score:', re.I)
def looks_noncitation(title, raw):
    t=title.strip()
    if NONART.search(raw) or NONART.search(t): return True
    if len(toks(t))<4: return True
    if re.match(r'^(proof|remark|definition|lemma|theorem|note|figure|table|eq|step|a:|q:|based on|use a|let\'s|share|respond|stay|although|notice that|given that|if a|start-pos|non-empty|email address|affiliated|his main|dr\.)', t, re.I): return True
    if CODEISH.search(t): return True
    letters=[c for c in t if c.isalpha()]
    if letters and sum(1 for c in letters if ord(c)>127)/len(letters) > 0.15: return True  # non-English
    return False

def http(u):
    for a in range(5):
        try:
            req=urllib.request.Request(u,headers={"User-Agent":"insilicom-fabaudit/1.0 (mailto:%s)"%MAILTO})
            return json.load(urllib.request.urlopen(req,timeout=30))
        except urllib.error.HTTPError as e:
            if e.code in (429,500,502,503): time.sleep(1.5*(a+1)); continue
            return None
        except Exception: time.sleep(1); continue
    return None
def crossref(rawq, ref_title):
    d=http("https://api.crossref.org/works?"+urllib.parse.urlencode({"query.bibliographic":rawq[:400],"rows":5,"mailto":MAILTO}))
    if not d: return None
    for it in d.get("message",{}).get("items",[]):
        ct=(it.get("title") or [""])[0]
        if ct and good_match(ref_title,ct): return ct
    return None

# ---- build or load frozen sample ----
if not os.path.exists(SAMPLE_TSV):
    months=[];y,m=9,1
    while (y,m)<=(26,6):
        months.append("%02d%02d"%(y,m));m+=1
        if m>12:m=1;y+=1
    N=30;sz=math.ceil(len(months)/N)
    files=[os.path.join(D,"refs_%s_%s_k1000.jsonl"%(months[i],months[i:i+sz][-1])) for i in range(0,len(months),sz)]
    files=[f for f in files if os.path.exists(f)]
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
            buckets[yr].append((yr,t,(r.get("raw") or "").strip()))
    random.seed(20260808)
    with open(SAMPLE_TSV,"w") as o:
        o.write("idx\tyear\ttitle\traw\n"); idx=0
        for yr,q in QUOTA.items():
            pool=buckets.get(yr,[]); random.shuffle(pool)
            for yr2,t,raw in pool[:q]:
                o.write("%d\t%s\t%s\t%s\n"%(idx,yr2,t.replace(chr(9),' '),raw.replace(chr(9),' ')[:300])); idx+=1
    print("froze sample: %d refs (by yr: %s)"%(idx,{y:min(len(buckets.get(y,[])),q) for y,q in QUOTA.items()}),flush=True)

sample=[l.rstrip("\n").split("\t") for l in open(SAMPLE_TSV)][1:]
done=set()
if os.path.exists(RESULTS):
    for l in open(RESULTS):
        try: done.add(json.loads(l)["idx"])
        except: pass
print("sample=%d  already done=%d  remaining=%d"%(len(sample),len(done),len(sample)-len(done)),flush=True)

con=sqlite3.connect("file:%s?mode=ro"%FTS,uri=True)
def in_local(ref_title):
    ct=toks(ref_title); content=[t for t in ct if t not in STOP]
    for sel in (content[:8], sorted(content,key=len,reverse=True)[:5], sorted(content,key=len,reverse=True)[:3]):
        if len(sel)<2: continue
        q=" ".join('"%s"'%t for t in sel)
        try: rows=[r[0] for r in con.execute("SELECT title_norm FROM docs WHERE docs MATCH ? LIMIT 60",(q,)).fetchall()]
        except Exception: rows=[]
        if rows and any(good_match(ref_title,c) for c in rows): return True
    return False

fo=open(RESULTS,"a"); t0=time.time(); n=0
for row in sample:
    if len(row)<4: continue
    idx=int(row[0]); yr=row[1]; title=row[2]; raw=row[3]
    if idx in done: continue
    if looks_noncitation(title,raw): v="noncitation"; src="filter"
    elif in_local(title):           v="REAL"; src="local_fts"
    elif crossref(raw or title,title): v="REAL"; src="crossref"; time.sleep(0.34)
    else:                           v="RESIDUAL"; src="none"; time.sleep(0.34)
    fo.write(json.dumps({"idx":idx,"year":yr,"verdict":v,"src":src,"title":title,"raw":raw})+"\n"); fo.flush()
    n+=1
    if n%200==0: print("  +%d (%.0fs) last idx=%d"%(n,time.time()-t0,idx),flush=True)
fo.close()
print("CLASSIFY DONE, processed %d this run"%n,flush=True)
