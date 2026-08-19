#!/usr/bin/env python3
"""
full_classify.py <refs_file> — classify EVERY not-found academic ref in one shard file.
Buckets: no_title (uncheckable by title; physics-style journal+vol+page, not an LLM-fab candidate),
noncitation (GROBID parse junk), real_local (exists in 484M OpenAlex FTS via token/trigram match),
residual (has title, not junk, not in local index -> needs Crossref+hand to adjudicate).
Writes: <out>/counts_<shard>.json  and  <out>/residual_<shard>.jsonl
"""
import json, os, re, sqlite3, sys, collections
REFS=sys.argv[1]; OUT=sys.argv[2]
FTS=os.environ.get("OA_FTS","/dev/shm/oa_fts.db")
shard=os.path.basename(REFS).replace("refs_","").replace("_k1000.jsonl","")

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
def good(ref,cand):
    return contain(toks(ref),cand)>=0.65 or tjac(ref,cand)>=0.55
NONART=re.compile(r'\bhandbook\b|\bencyclopedia\b|lecture notes|\bproof\b|\btheorem\b|\blemma\b|\bcorollary\b|'
                  r'\bvol\.|\bchapter\b|\bpress\b|springer|elsevier|wiley|\bthesis\b|dissertation|to appear|'
                  r'in press|private communication|\bbook\b|monograph|\bstandard\b|\bRFC\b|datasheet|user guide|'
                  r'\bmanual\b|technical report|tractatus|commentationes|\bed\.\b|editor|proceedings of the|'
                  r'market (research|report)|play\.google|app store|github\.com|\bdataset\b|R package|version \d',re.I)
CODEISH=re.compile(r'[{}<>]=?|:-|::|\)\s*:|_[a-z]+\(|->|instruction:|\bdef \b|MUST |labels:|score:',re.I)
def junk(t,raw):
    if NONART.search(raw) or NONART.search(t): return True
    if len(toks(t))<4: return True
    if re.match(r'^(proof|remark|definition|lemma|theorem|note|figure|table|eq|step|a:|q:|based on|use a|let\'s|share|respond|stay|although|notice that|given that|if a|start-pos|non-empty|email address|affiliated|his main|dr\.)',t,re.I): return True
    if CODEISH.search(t): return True
    L=[c for c in t if c.isalpha()]
    if L and sum(1 for c in L if ord(c)>127)/len(L)>0.15: return True
    return False

con=sqlite3.connect("file:%s?mode=ro"%FTS,uri=True)
def in_local(t):
    ct=toks(t); content=[x for x in ct if x not in STOP]
    for sel in (content[:8], sorted(content,key=len,reverse=True)[:5], sorted(content,key=len,reverse=True)[:3]):
        if len(sel)<2: continue
        q=" ".join('"%s"'%x for x in sel)
        try: rows=[r[0] for r in con.execute("SELECT title_norm FROM docs WHERE docs MATCH ? LIMIT 60",(q,)).fetchall()]
        except Exception: rows=[]
        if rows and any(good(t,c) for c in rows): return True
    return False

cnt=collections.defaultdict(lambda: collections.Counter())  # year -> {bucket:count}
fr=open(os.path.join(OUT,"residual_%s.jsonl"%shard),"w")
for line in open(REFS):
    try: r=json.loads(line)
    except: continue
    if r.get("nonacademic") or r.get("found"): continue
    yr=str(2000+int(r["month"][:2])) if r.get("month") else "?"
    t=(r.get("ref_title") or "").strip()
    if not r.get("has_title") or len(toks(t))<3:
        cnt[yr]["no_title"]+=1; continue
    if junk(t,r.get("raw") or ""):
        cnt[yr]["noncitation"]+=1; continue
    if in_local(t):
        cnt[yr]["real_local"]+=1; continue
    cnt[yr]["residual"]+=1
    fr.write(json.dumps({"year":yr,"title":t,"raw":(r.get("raw") or "")[:200],"doi":bool(r.get("has_doi"))})+"\n")
fr.close()
json.dump({y:dict(c) for y,c in cnt.items()}, open(os.path.join(OUT,"counts_%s.json"%shard),"w"))
print("shard %s done"%shard, flush=True)
