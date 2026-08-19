#!/usr/bin/env python3
"""hijack_detector v2 — matched-work author token must appear in the citation's raw author text.
Handles oa.author1 being first-token-of-display-name (given OR surname). Validates on GPTZero
ground truth with per-paper caching; only then sweeps v7."""
import json,re,os,sys,glob,math,sqlite3,tarfile,collections,unicodedata
sys.path.insert(0,"/space/rwang/fake-citation-detector/scripts")
os.environ.setdefault("OA_LOCAL_INDEX","/space/rwang/oa_index/oa_index.db")
OUT="/space/rwang/_speedtest/hijack"; os.makedirs(OUT,exist_ok=True)
CACHE=os.path.join(OUT,"per_ref_cache.json")

def deacc(s): return unicodedata.normalize("NFKD",s or "").encode("ascii","ignore").decode()
def alltoks(s): return set(t for t in re.split(r"[^a-z]+",deacc(s).lower()) if len(t)>=3)
def tn(s):
    s=re.sub(r"[^a-z0-9 ]+"," ",deacc(s).lower()); return re.sub(r"\s+"," ",s).strip()
oa=sqlite3.connect("file:/space/rwang/oa_index/oa_index.db?mode=ro",uri=True)
cr=sqlite3.connect("file:/space/rwang/crossref/biblio_index.db?mode=ro",uri=True)
def oa_authors(matched_title):
    t=tn(matched_title)
    if not t: return []
    return [r[0] for r in oa.execute("SELECT author1 FROM oa WHERE title_norm=? LIMIT 8",(t,)).fetchall() if r[0]]
def cr_author_by_doi(doi):
    if not doi: return None
    r=cr.execute("SELECT author1 FROM biblio WHERE doi=? LIMIT 1",(doi.lower().strip(),)).fetchone()
    return r[0] if r and r[0] else None
GENERIC={"and","the","for","with","der","van","von","della"}
def check_ref(r):
    if not r.get("found"): return ("skip",None)
    raw=r.get("raw") or ""
    if len(raw)<40: return ("unjudgeable",None)
    rt=alltoks(raw)-GENERIC
    cands=[]
    mt=r.get("matched_title") or ""
    if mt: cands+=oa_authors(mt)
    ca=cr_author_by_doi(r.get("ref_doi")) if r.get("has_doi") else None
    if ca: cands.append(ca)
    cands=[c for c in cands if alltoks(c)-GENERIC]
    if not cands: return ("unjudgeable",None)
    for c in cands:
        if (alltoks(c)-GENERIC) & rt: return ("ok",c)
    # initials gate: if the author segment (front of raw) carries no full names,
    # a given-name author1 can never appear -> unjudgeable, not hijack
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

mode=sys.argv[1] if len(sys.argv)>1 else "partA"
if mode=="partA":
    cache={}
    if os.path.exists(CACHE): cache=json.load(open(CACHE))
    import batch_verify_years as bvy, arxiv_sweep as asw
    MAP=json.load(open("/space/rwang/_speedtest/neurips51_map.json"))
    TEI="/space/eric/citation_data/arxiv/tei/new"
    solr=None
    def toks(s):
        s=re.sub(r'(\w)-\s*(\w)',r'\1\2',s or "")
        return set(w for w in re.split(r'[^a-z0-9]+',s.lower()) if len(w)>2)
    tp=fn=unj=fp=okn=0; details=[]
    for title,v in MAP.items():
        aid=v.get("arxiv")
        if not aid or not re.match(r"\d{4}\.",aid): continue
        if aid in cache: per=cache[aid]
        else:
            if solr is None: solr=asw._solr()
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
            ver=bvy.verify_refs(refs,solr)
            per=ver.get("per_ref") or ver.get("refs") or []
            cache[aid]=per; json.dump(cache,open(CACHE,"w"))
        # best-overlap assignment: each fab cite -> ONE parsed ref
    fabof={}
    for title,v in MAP.items():
        aid=v.get("arxiv")
        if not aid or aid not in cache: continue
        per=cache[aid]
        for c in v["cites"]:
            ft=toks(c["cite"]); best=None;bs=0
            for j,r in enumerate(per):
                rt=toks((r.get("raw") or "")+" "+(r.get("ref_title") or ""))
                ov=len(ft&rt)/len(ft) if ft else 0
                if ov>bs: bs=ov;best=j
            if bs>=0.4: fabof.setdefault(aid,set()).add(best)
    for aid,per in cache.items():
        fabidx=fabof.get(aid,set())
        for j,r in enumerate(per):
            if not r.get("found"): continue
            verd,ma=check_ref(r)
            if j in fabidx:
                if verd=="hijack": tp+=1; details.append(("TP",aid,(r.get("raw") or "")[:90],ma))
                elif verd=="ok": fn+=1; details.append(("FN",aid,(r.get("raw") or "")[:90],ma))
                else: unj+=1
            else:
                if verd=="hijack": fp+=1; details.append(("FP",aid,(r.get("raw") or "")[:90],ma))
                elif verd=="ok": okn+=1
    print("=== PART A v2 ===")
    print("known-fab matched refs: TP=%d FN=%d unjudgeable=%d -> recall=%.0f%%"%(tp,fn,unj,100*tp/max(1,tp+fn)))
    print("normal matched refs:    FP=%d ok=%d -> FPR=%.2f%%"%(fp,okn,100*fp/max(1,fp+okn)))
    json.dump(details,open(os.path.join(OUT,"partA_v2_details.json"),"w"),indent=1)
    print("PARTA_DONE")
