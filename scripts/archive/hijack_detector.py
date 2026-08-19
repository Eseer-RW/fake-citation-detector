#!/usr/bin/env python3
"""
hijack_detector.py — second detection channel: author cross-check on MATCHED refs.
A hijacked/Frankenstein citation reuses a real title with fabricated authors -> existence
checks match the real work and miss it. Here: for each found ref, look up the matched work's
first author (oa_index.oa.author1 via title_norm) and compare surnames with the citation's
parsed author. No overlap -> HIJACK candidate.
Part A: validate on the 34 GPTZero NeurIPS papers (must flag the 17 known misses, low FPR).
Part B: retroactive sweep over all v7 matched refs.
"""
import json,re,os,sys,glob,math,sqlite3,tarfile,collections,unicodedata
sys.path.insert(0,"/space/rwang/fake-citation-detector/scripts")
os.environ.setdefault("OA_LOCAL_INDEX","/space/rwang/oa_index/oa_index.db")
OUT="/space/rwang/_speedtest/hijack"; os.makedirs(OUT,exist_ok=True)

def deacc(s): return unicodedata.normalize("NFKD",s or "").encode("ascii","ignore").decode()
def surnames(s):
    s=deacc(s).lower()
    toks=[t for t in re.split(r"[^a-z]+",s) if len(t)>=3]
    drop={"and","the","van","von","der","den","del","della","collaboration","team","et","al","others","jr","iii"}
    return set(t for t in toks if t not in drop)
def author_agrees(ref_author, oa_author1):
    ra=surnames(ref_author); oa=surnames(oa_author1)
    if not ra or not oa: return None    # can't judge
    return bool(ra & oa)

def tn(s):
    s=re.sub(r"[^a-z0-9 ]+"," ",deacc(s).lower()); return re.sub(r"\s+"," ",s).strip()
con=sqlite3.connect("file:/space/rwang/oa_index/oa_index.db?mode=ro",uri=True)
def oa_author(matched_title):
    t=tn(matched_title)
    if not t: return None
    rows=con.execute("SELECT author1 FROM oa WHERE title_norm=? LIMIT 8",(t,)).fetchall()
    auths=[r[0] for r in rows if r[0]]
    return auths or None

def check_ref(r):
    """returns (verdict, matched_author) verdict in {hijack, ok, unjudgeable}"""
    if not r.get("found"): return ("skip",None)
    ra=r.get("ref_author") or ""
    mt=r.get("matched_title") or ""
    if not ra or not mt: return ("unjudgeable",None)
    cands=oa_author(mt)
    if not cands: return ("unjudgeable",None)
    # agree if the citation surname matches ANY oa record w/ this title (title collisions across records)
    for a in cands:
        ag=author_agrees(ra,a)
        if ag: return ("ok",a)
    if all(author_agrees(ra,a) is None for a in cands): return ("unjudgeable",None)
    return ("hijack",cands[0])

# ---------- PART A: ground-truth validation ----------
if "--partB" not in sys.argv:
    import batch_verify_years as bvy, arxiv_sweep as asw
    MAP=json.load(open("/space/rwang/_speedtest/neurips51_map.json"))
    TEI="/space/eric/citation_data/arxiv/tei/new"
    solr=asw._solr() if hasattr(asw,"_solr") else None
    def toks(s):
        s=re.sub(r'(\w)-\s*(\w)',r'\1\2',s or "")
        return set(w for w in re.split(r'[^a-z0-9]+',s.lower()) if len(w)>2)
    tp=0; fn=0; flagged_other=0; ok_other=0; unj=0; details=[]
    for title,v in MAP.items():
        aid=v.get("arxiv")
        if not aid or not re.match(r"\d{4}\.",aid): continue
        yymm=aid[:4].replace(".","")
        yymm=aid.split(".")[0]
        tarp=os.path.join(TEI,yymm+".tar.gz")
        if not os.path.exists(tarp): continue
        member=None
        with tarfile.open(tarp,"r:gz") as tf:
            for n in tf.getnames():
                if re.match(r"\./"+re.escape(aid)+r"v\d+\.tei\.xml$",n): member=n
            if not member: continue
            tei=tf.extractfile(member).read().decode("utf-8",errors="replace")
        refs=bvy.parse_tei_refs(tei)
        ver=bvy.verify_refs(refs,solr) if solr is not None else bvy.verify_refs(refs)
        per=ver.get("per_ref") or ver.get("refs") or []
        # which parsed refs correspond to known fabs?
        fabsets=[toks(c["cite"]) for c in v["cites"]]
        for r in per:
            if not r.get("found"): continue
            rt=toks((r.get("raw") or "")+" "+(r.get("ref_title") or ""))
            isfab=any((len(ft&rt)/len(ft) if ft else 0)>=0.4 for ft in fabsets)
            verd,ma=check_ref(r)
            if isfab:
                if verd=="hijack": tp+=1; details.append(("TP",aid,(r.get("raw") or "")[:90],ma))
                elif verd=="ok": fn+=1; details.append(("FN",aid,(r.get("raw") or "")[:90],ma))
                else: unj+=1; details.append(("UNJ",aid,(r.get("raw") or "")[:90],None))
            else:
                if verd=="hijack": flagged_other+=1; details.append(("FP?",aid,(r.get("raw") or "")[:90],ma))
                elif verd=="ok": ok_other+=1
        print("A done:",aid,flush=True)
    print("\n=== PART A: hijack detector vs ground truth ===")
    print("known-fab matched refs:  flagged(TP)=%d  passed(FN)=%d  unjudgeable=%d"%(tp,fn,unj))
    print("other matched refs:      flagged(FP?)=%d  passed(ok)=%d  -> FPR=%.1f%%"%(flagged_other,ok_other,100*flagged_other/max(1,flagged_other+ok_other)))
    json.dump(details,open(os.path.join(OUT,"partA_details.json"),"w"),indent=1)
    print("PARTA_DONE",flush=True)

# ---------- PART B: retro sweep over v7 matched refs ----------
if "--partA-only" not in sys.argv:
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
                    "ref_author":r.get("ref_author"),"matched_title":r.get("matched_title"),"matched_author":ma})+"\n")
            if n%200000==0: print("B: %d refs..."%n,flush=True)
        fh.flush()
    fh.close()
    print("\n=== PART B: retroactive hijack sweep (%d matched refs) ==="%n)
    tot=collections.Counter()
    for yr in sorted(cnt):
        c=cnt[yr]; tot.update(c)
        jt=c["hijack"]+c["ok"]
        print("  %s: hijack=%6d ok=%8d unjudgeable=%7d  rate=%.3f%%"%(yr,c["hijack"],c["ok"],c["unjudgeable"],100*c["hijack"]/jt if jt else 0))
    jt=tot["hijack"]+tot["ok"]
    print("TOTAL: hijack=%d ok=%d unjudgeable=%d rate=%.3f%%"%(tot["hijack"],tot["ok"],tot["unjudgeable"],100*tot["hijack"]/jt if jt else 0))
    print("PARTB_DONE",flush=True)
