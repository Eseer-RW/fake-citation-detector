#!/usr/bin/env python3
"""
v7_fab_nodoi.py — hunt fabrications in the RIGHT place: recent (2022+), non-DOI, title-bearing,
not-found academic refs. This is where LLM hallucinations live (plausible title, no DOI).
For each, fuzzy-check existence against the 484M-title FTS index (oa_fts.db) using distinctive
content tokens. If a title with high token-containment exists -> real-but-unindexed/exact-miss.
If NOTHING close exists anywhere in 484M titles -> genuine FABRICATION candidate (hand-verify).
"""
import json, os, re, math, sqlite3, collections, random, sys
D="/space/rwang/fake-citation-detector/scripts/arxiv_sweep_v7"
FTS="/space/rwang/oa_index/oa_fts.db"
YEAR_MIN=int(sys.argv[1]) if len(sys.argv)>1 else 2022
SAMPLE=int(sys.argv[2]) if len(sys.argv)>2 else 60

months=[];y,m=9,1
while (y,m)<=(26,6):
    months.append("%02d%02d"%(y,m));m+=1
    if m>12:m=1;y+=1
N=30;sz=math.ceil(len(months)/N)
files=[os.path.join(D,"refs_%s_%s_k1000.jsonl"%(months[i],months[i:i+sz][-1])) for i in range(0,len(months),sz)]
files=[f for f in files if os.path.exists(f)]

STOP=set("the a an of for and or to in on with using via based from into over under between is are we our this that new toward towards approach method model models system systems".split())
def toks(s):
    s=re.sub(r'(\w)-\s*(\w)',r'\1\2',s or "")           # de-hyphenate GROBID line-breaks
    return [w for w in re.split(r'[^a-z0-9]+',s.lower()) if len(w)>2]
def contain(ct,cand):
    cs=set(toks(cand)); return sum(1 for w in ct if w in cs)/len(ct) if ct else 0

# non-article guard (URLs, manuals, datasets, software, standards — not citable articles)
NONART=re.compile(r'https?://|www\.|github|gitlab|\bmanual\b|datasheet|white ?paper|documentation|'
                  r'\bwiki\b|readme|user guide|\bAPI\b|toolkit|library|repository|dataset|'
                  r'technical report|tech\. rep|\bstandard\b|\bRFC\b|patent|thesis|dissertation|'
                  r'proceedings of|workshop on|\bversion \d|\bv\d+\.\d+', re.I)

con=sqlite3.connect("file:%s?mode=ro"%FTS,uri=True)
def exists_fuzzy(ct):
    content=[t for t in ct if t not in STOP]
    for sel in (content[:8], sorted(content,key=len,reverse=True)[:5], sorted(content,key=len,reverse=True)[:3]):
        if len(sel)<2: continue
        q=" ".join('"%s"'%t for t in sel)
        try: rows=[r[0] for r in con.execute("SELECT title_norm FROM docs WHERE docs MATCH ? LIMIT 50",(q,)).fetchall()]
        except Exception: rows=[]
        if rows:
            best=max(contain(ct,c) for c in rows)
            if best>=0.7: return "exists", best
    return "none", 0.0

# collect target population
pop=[]
for f in files:
    for line in open(f):
        try:r=json.loads(line)
        except:continue
        if r.get("nonacademic") or r.get("found") or r.get("has_doi"): continue
        if not r.get("has_title"): continue
        yr=2000+int(r["month"][:2]) if r.get("month") else 0
        if yr<YEAR_MIN: continue
        t=(r.get("ref_title") or "").strip()
        if len(toks(t))<3: continue
        pop.append((yr,t,(r.get("raw") or "")[:180]))
print("recent(>=%d) non-DOI title-bearing not-found refs: %d"%(YEAR_MIN,len(pop)))

# sample and classify (FTS is the bottleneck; classify a random sample for a rate estimate)
random.seed(7)
samp=random.sample(pop, min(SAMPLE*8, len(pop)))   # oversample, then report
cls=collections.Counter(); cands=[]
for i,(yr,t,raw) in enumerate(samp):
    ct=toks(t)
    if NONART.search(raw+" "+t): cls["non_article"]+=1; continue
    verdict,score=exists_fuzzy(ct)
    if verdict=="exists": cls["exists_in_484M"]+=1
    else: cls["FAB_CANDIDATE"]+=1; cands.append((yr,t,raw))
n=sum(cls.values())
print("\n=== classified %d sampled refs ==="%n)
for k in ("exists_in_484M","non_article","FAB_CANDIDATE"):
    print("  %-16s %5d  (%.1f%%)"%(k,cls[k],100*cls[k]/n))
print("\n  => est fabrication rate among recent non-DOI not-found title refs: %.1f%% (%d/%d)"%(100*cls['FAB_CANDIDATE']/n,cls['FAB_CANDIDATE'],n))
print("     applied to the %d-ref population -> ~%d candidates need hand-verify"%(len(pop),round(len(pop)*cls['FAB_CANDIDATE']/n)))
print("\n=== up to %d FAB_CANDIDATE for hand-inspection (title :: raw) ==="%SAMPLE)
for yr,t,raw in cands[:SAMPLE]:
    print("  [%d] %s :: %s"%(yr,t[:70],raw))
with open(os.path.join(D,"fab_nodoi_candidates.tsv"),"w") as o:
    o.write("year\ttitle\traw\n")
    for yr,t,raw in cands: o.write("%d\t%s\t%s\n"%(yr,t.replace(chr(9),' '),raw.replace(chr(9),' ')))
print("\nwrote %d candidates -> fab_nodoi_candidates.tsv"%len(cands))
