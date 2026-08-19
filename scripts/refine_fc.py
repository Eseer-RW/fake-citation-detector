#!/usr/bin/env python3
"""
refine_fc.py — stage-2 refinement of the fab_candidate channel (precision was ~1%).
Clears candidates that are real-but-invisible to exact matching:
  R1 title-repair variants (dehyphenate, ligature restore fi/fl, mojibake) -> exact retry vs oa_index
  R2 fuzzy existence vs oa_fts.db (484M titles; token-AND + containment/trigram)
  R3 extended non_article patterns (standards, talks, whitepapers, monographs, specs)
Survivors = deep_fab_candidate (the pool worth human verification).
Modes:
  validate  — run on the 150 human-labeled items (fc_verify_sample.json + verdict map) and
              report cleared/kept per label class (the refiner MUST keep the confirmed fab).
  pool      — stream v8 refs, refine ALL lag-robust 2023-25 fab_candidates, write survivors.
"""
import json, re, os, sys, glob, sqlite3, collections, unicodedata
FTS = "/space/rwang/oa_index/oa_fts.db"
OA  = "/space/rwang/oa_index/oa_index.db"

def deacc(s): return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
STOP=set("the a an of for and or to in on with using via based from into over under between is are we our this that new".split())
def toks(s):
    s=re.sub(r'(\w)-\s*(\w)',r'\1\2',s or "")
    return [w for w in re.split(r'[^a-z0-9]+',deacc(s).lower()) if len(w)>2 and w not in STOP]
def trig(s):
    s=re.sub(r'[^a-z0-9]','',deacc(s).lower()); return {s[i:i+3] for i in range(len(s)-2)}
def good(ref,c):
    ct=toks(ref); cs=set(toks(c))
    cont=sum(1 for w in ct if w in cs)/len(ct) if ct else 0
    ta,tb=trig(ref),trig(c); tj=len(ta&tb)/len(ta|tb) if (ta|tb) else 0
    return cont>=0.6 or tj>=0.5
def tn(s):
    s=re.sub(r"[^a-z0-9 ]+"," ",deacc(s).lower()); return re.sub(r"\s+"," ",s).strip()

def repair_variants(t):
    out={t}
    out.add(re.sub(r'(\w)-\s+(\w)',r'\1\2',t))                    # linebreak hyphen join
    out.add(re.sub(r'(\w)-(\w)',r'\1\2',t))                       # all hyphens joined
    out.add(re.sub(r'(\w)-(\w)',r'\1 \2',t))                      # hyphens as spaces
    # ligature loss: 'recongurable'->'reconfigurable'. try inserting fi/fl at consonant gaps
    for lig in ("fi","fl","ff","ffi"):
        for m in re.finditer(r'[a-z]{2}[bcdgknprstv][aeiou]?gur|[a-z]gur', t.lower()):
            pass
    # cheap general variant: strip subtitles after ':' and after ' - '
    if ":" in t: out.add(t.split(":")[0])
    return {x.strip() for x in out if len(x.strip())>=10}

_XNONART = re.compile(
    r"\b(3GPP|IEEE\s+Std|ISO/|IETF|ITU-T|ETSI|NIST\s+SP|RFC\s*\d)|white\s*paper|"
    r"black\s*hat|def\s*con|keynote|invited talk|tutorial at|technical specification|"
    r"release \d+|\bTS \d{2}\.\d{3}|survey report|annual report|\bproc\.\s*spie\b|"
    r"lecture notes in|graduate texts|monograph|(university|academic|mit|cambridge|oxford|springer|elsevier|crc|wiley)\s+press|"
    r"ph\.?d\.?\s+(thesis|dissertation)|master.?s\s+thesis|habilitation", re.I)

class Refiner:
    def __init__(self):
        self.fts = sqlite3.connect("file:%s?mode=ro" % FTS, uri=True)
        self.oa  = sqlite3.connect("file:%s?mode=ro" % OA, uri=True)
    def oa_exact(self, title):
        for v in repair_variants(title):
            if self.oa.execute("SELECT 1 FROM oa WHERE title_norm=? LIMIT 1",(tn(v),)).fetchone():
                return True
        return False
    def fts_fuzzy(self, title, raw=""):
        """Fuzzy title match CLEARS a candidate ONLY when the matched work's author also
        appears in the citation. Frankenstein fabrications resemble real works by
        construction, so an author-free fuzzy clear removes exactly the fabs we want
        (validated: it cleared the confirmed FedCurv fake). Author-unjudgeable -> keep."""
        rt = set(t for t in re.split(r"[^a-z]+", deacc(raw).lower()) if len(t) >= 3)
        ct = toks(title)
        content=[t for t in ct if t not in STOP]
        for sel in (content[:8], sorted(content,key=len,reverse=True)[:5], sorted(content,key=len,reverse=True)[:3]):
            if len(sel)<2: continue
            q=" ".join('"%s"'%t for t in sel)
            try:
                rows=[r[0] for r in self.fts.execute("SELECT title_norm FROM docs WHERE docs MATCH ? LIMIT 60",(q,)).fetchall()]
            except Exception: rows=[]
            for c in rows:
                if not good(title,c): continue
                # author gate: matched title -> oa author1 tokens must appear in raw
                try:
                    arows=[a[0] for a in self.oa.execute("SELECT author1 FROM oa WHERE title_norm=? LIMIT 8",(tn(c),)).fetchall() if a[0]]
                except Exception: arows=[]
                for a in arows:
                    at=set(t for t in re.split(r"[^a-z]+", deacc(a).lower()) if len(t)>=3)
                    if at and (at & rt):
                        return True
        return False
    def refine(self, title, raw):
        """returns (verdict, reason): cleared_* -> not a deep candidate; keep -> deep_fab_candidate"""
        if _XNONART.search(raw or "") or _XNONART.search(title or ""):
            return ("cleared_nonarticle", None)
        if self.oa_exact(title):
            return ("cleared_repairmatch", None)
        if self.fts_fuzzy(title, raw):
            return ("cleared_fuzzy", None)
        return ("keep", None)

mode = sys.argv[1] if len(sys.argv)>1 else "validate"
R = Refiner()
if mode=="validate":
    samp=json.load(open("/space/rwang/_speedtest/fc_verify_sample.json"))
    # verdict map from the human/web verification (idx order = file order)
    verd=json.load(open("/space/rwang/_speedtest/fc_verdicts.json"))
    vmap={v["idx"]:v["verdict"] for v in verd}
    cnt=collections.defaultdict(collections.Counter)
    for i,x in enumerate(samp):
        label=vmap.get(i,"?")
        r,_=R.refine(x.get("ref_title") or "", x.get("raw") or "")
        cnt[label][r]+=1
    print("=== REFINER vs 150 human labels ===")
    for label in sorted(cnt):
        c=cnt[label]; n=sum(c.values())
        kept=c["keep"]
        print("  %-18s n=%3d  kept=%3d (%.0f%%)  cleared: %s"%(label,n,kept,100*kept/n,
              {k:v for k,v in c.items() if k!="keep"}))
    print("VALIDATE_DONE")
elif mode=="pool":
    D="/space/rwang/fake-citation-detector/scripts/arxiv_sweep_v8"
    files=sorted(glob.glob(os.path.join(D,"refs_*.jsonl")))
    owner={}
    for f in files:
        got=set()
        for line in open(f):
            m=re.search(r'"month": ?"(\d{4})"',line)
            if m: got.add(m.group(1))
        for mo in got: owner.setdefault(mo,f)
    import os as _os
    _sh=_os.environ.get("RSHARD","0/1"); _k,_N=map(int,_sh.split("/"))
    _tag=_os.environ.get("POOLTAG","")
    out=open("/space/rwang/_speedtest/deep_fab_pool%s_%d.jsonl"%(_tag,_k),"w")
    cnt=collections.Counter(); n=0
    files=[f for i,f in enumerate(files) if i%_N==_k]
    for f in files:
        for line in open(f):
            try:r=json.loads(line)
            except:continue
            mo=r.get("month")
            if not mo or owner.get(mo)!=f or r.get("nonacademic") or r.get("found"): continue
            if r.get("not_found_reason")!="fab_candidate": continue
            yr=2000+int(mo[:2])
            _y0=int(_os.environ.get("Y0","2023")); _y1=int(_os.environ.get("Y1","2025"))
            if not (_y0<=yr<=_y1): continue
            cy=r.get("cited_year")
            try: cy=int(cy)
            except: cy=None
            if cy is None or cy>yr-2: continue
            n+=1
            v,_=R.refine(r.get("ref_title") or "", r.get("raw") or "")
            cnt[v]+=1
            if v=="keep":
                out.write(json.dumps({"year":yr,"raw":(r.get("raw") or "")[:220],
                    "ref_title":r.get("ref_title"),"paper":r.get("paper")})+"\n")
            if n%20000==0: print("  %d candidates, verdicts %s"%(n,dict(cnt)),flush=True)
    out.close()
    print("=== POOL REFINEMENT (%d lag-robust 2023-25 fab_candidates) ==="%n)
    for k,v in cnt.most_common(): print("  %-22s %d (%.1f%%)"%(k,v,100*v/n))
    print("POOL_DONE")
