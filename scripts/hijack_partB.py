#!/usr/bin/env python3
"""Part B: retroactive hijack sweep over ALL v7 matched refs with the validated v2 checker
(recall 62%, FPR ~10% -> interpret via pre-2022 baseline subtraction)."""
import json,re,os,sys,glob,math,sqlite3,collections,unicodedata
OUT="/space/rwang/_speedtest/hijack"
def deacc(s): return unicodedata.normalize("NFKD",s or "").encode("ascii","ignore").decode()
def alltoks(s): return set(t for t in re.split(r"[^a-z]+",deacc(s).lower()) if len(t)>=3)
def tn(s):
    s=re.sub(r"[^a-z0-9 ]+"," ",deacc(s).lower()); return re.sub(r"\s+"," ",s).strip()
oa=sqlite3.connect("file:/space/rwang/oa_index/oa_index.db?mode=ro",uri=True)
cr=sqlite3.connect("file:/space/rwang/crossref/biblio_index.db?mode=ro",uri=True)
GENERIC={"and","the","for","with","der","van","von","della","team","robotics","computational",
         "association","findings","press","university","institute","group","collaboration","consortium"}
def oa_authors(mt):
    t=tn(mt)
    if not t: return []
    return [r[0] for r in oa.execute("SELECT author1 FROM oa WHERE title_norm=? LIMIT 8",(t,)).fetchall() if r[0]]
def cr_author(doi):
    if not doi: return None
    r=cr.execute("SELECT author1 FROM biblio WHERE doi=? LIMIT 1",(doi.lower().strip(),)).fetchone()
    return r[0] if r and r[0] else None
def check_ref(r):
    raw=r.get("raw") or ""
    if len(raw)<40: return ("unjudgeable",None)
    rt=alltoks(raw)-GENERIC
    cands=[]
    if r.get("matched_title"): cands+=oa_authors(r["matched_title"])
    if r.get("has_doi"):
        ca=cr_author(r.get("ref_doi"))
        if ca: cands.append(ca)
    cands=[c for c in cands if alltoks(c)-GENERIC]
    if not cands: return ("unjudgeable",None)
    for c in cands:
        if (alltoks(c)-GENERIC) & rt: return ("ok",c)
    head=raw[:130]
    # leading-initials style ("F. Sciortino", "S.-J. Huang") -> given-name index token can never appear
    if re.match(r'\s*(?:[A-Z]\.[-\s]*){1,4}[A-Z]?[a-z]', head): return ("unjudgeable",None)
    # surname-first style ("Zhang Y, Li K") -> same problem
    if re.match(r'\s*[A-Z][a-z]+\s+[A-Z]\b[,.\s]', head): return ("unjudgeable",None)
    # need at least two full given-name-capable words BEFORE any period-terminated author block
    authseg=head.split('. ')[0] if '. ' in head[:60] else head
    fullnames=[t for t in re.split(r"[^A-Za-z]+",authseg) if len(t)>=4 and t[0].isupper()]
    if len(fullnames)<2: return ("unjudgeable",None)
    # don't trust short (accent-mangled) index tokens as disagreement evidence
    if all(max((len(t) for t in alltoks(c)-GENERIC), default=0)<5 for c in cands): return ("unjudgeable",None)
    return ("hijack",cands[0])

months=[];y,m=9,1
while (y,m)<=(26,6):
    months.append("%02d%02d"%(y,m));m+=1
    if m>12:m=1;y+=1
N=30;sz=math.ceil(len(months)/N)
D="/space/rwang/fake-citation-detector/scripts/arxiv_sweep_v7"
files=[os.path.join(D,"refs_%s_%s_k1000.jsonl"%(months[i],months[i:i+sz][-1])) for i in range(0,len(months),sz)]
files=[f for f in files if os.path.exists(f)]
cnt=collections.defaultdict(lambda:collections.Counter()); n=0
fh=open(os.path.join(OUT,"hijack_candidates.jsonl"),"w")
for f in files:
    for line in open(f):
        try:r=json.loads(line)
        except:continue
        if not r.get("found") or r.get("nonacademic"): continue
        n+=1
        verd,ma=check_ref(r)
        yr=str(2000+int(r["month"][:2])) if r.get("month") else "?"
        cnt[yr][verd]+=1
        if verd=="hijack":
            fh.write(json.dumps({"year":yr,"paper":r.get("paper"),"raw":(r.get("raw") or "")[:200],
                "matched_title":r.get("matched_title"),"matched_author":ma})+"\n")
        if n%200000==0:
            print("B: %d refs..."%n,flush=True); fh.flush()
fh.close()
print("\n=== PART B: hijack flag-rate by citing year (%d matched refs) ==="%n)
for yr in sorted(cnt):
    c=cnt[yr]; jt=c["hijack"]+c["ok"]
    print("  %s: hijack=%6d ok=%8d unjudge=%7d  flagrate=%.2f%%"%(yr,c["hijack"],c["ok"],c["unjudgeable"],100*c["hijack"]/jt if jt else 0))
print("PARTB_DONE",flush=True)
