#!/usr/bin/env python3
"""
v7_fakecount.py — how many not-found DOI-bearing citations are ACTUALLY fabricated?
A DOI is a hard identifier. For each not-found DOI ref, normalize the DOI and check it against
TWO independent registries: OpenAlex (486M) and Crossref (179M). Classify:
  extraction_artifact : DOI string is malformed/truncated (GROBID damage) -> not a real citation error
  real_in_openalex    : actually resolves in OA (sweep's exact-match missed it on normalization) -> NOT fake
  real_in_crossref    : registered in Crossref, just absent from OA -> NOT fake
  FAB_CANDIDATE        : well-formed DOI, registered in NEITHER -> genuine fabrication candidate (hand-verify)
"""
import json, os, re, math, sqlite3, collections, random
D="/space/rwang/fake-citation-detector/scripts/arxiv_sweep_v7"
months=[]; y,m=9,1
while (y,m)<=(26,6):
    months.append("%02d%02d"%(y,m)); m+=1
    if m>12: m=1;y+=1
N=30; sz=math.ceil(len(months)/N)
pairs=[(months[i],months[i:i+sz][-1]) for i in range(0,len(months),sz)]
files=[os.path.join(D,"refs_%s_%s_k1000.jsonl"%(S,E)) for S,E in pairs]
files=[f for f in files if os.path.exists(f)]

WELL=re.compile(r'^10\.\d{4,9}/\S+$')
def norm_doi(d):
    if not d: return None
    d=d.strip().lower()
    d=re.sub(r'^(https?://)?(dx\.)?doi\.org/','',d)
    d=re.sub(r'^doi:\s*','',d)
    d=d.strip().rstrip(').,;]}>')
    return d

oa=sqlite3.connect("file:/space/rwang/oa_index/oa_index.db?mode=ro",uri=True)
cr=sqlite3.connect("file:/space/rwang/crossref/biblio_index.db?mode=ro",uri=True)
def in_oa(d): return oa.execute("SELECT 1 FROM oa WHERE doi=? LIMIT 1",(d,)).fetchone() is not None
def in_cr(d): return cr.execute("SELECT 1 FROM biblio WHERE doi=? LIMIT 1",(d,)).fetchone() is not None

# collect not-found DOI refs
nf=[]
for f in files:
    for line in open(f):
        try: r=json.loads(line)
        except Exception: continue
        if r.get("nonacademic") or not r.get("has_doi") or r.get("found"): continue
        nf.append((r.get("ref_doi"), r.get("cited_year"), r.get("month"), (r.get("raw") or "")[:160]))
print("not-found DOI-bearing refs:", len(nf))

cls=collections.Counter(); era_fab=collections.Counter(); era_tot=collections.Counter()
fab_samples=[]
for doi_raw, cy, mo, raw in nf:
    yr=2000+int(mo[:2]) if mo else 0
    e="pre-2022" if yr<2022 else "2022+"
    era_tot[e]+=1
    d=norm_doi(doi_raw)
    if not d or not WELL.match(d):
        cls["extraction_artifact"]+=1; continue
    if in_oa(d):
        cls["real_in_openalex"]+=1; continue
    if in_cr(d):
        cls["real_in_crossref"]+=1; continue
    cls["FAB_CANDIDATE"]+=1; era_fab[e]+=1
    fab_samples.append((yr, d, raw))

tot=len(nf)
print("\n=== classification of %d not-found DOI refs ==="%tot)
for k in ("extraction_artifact","real_in_openalex","real_in_crossref","FAB_CANDIDATE"):
    print("  %-20s %6d  (%.1f%% of not-found DOI, %.4f%% of ALL 1.12M DOI refs)"%(k,cls[k],100*cls[k]/tot,100*cls[k]/1123523))
print("\n=== FAB_CANDIDATE by era ===")
for e in ("pre-2022","2022+"):
    print("  %-8s %d fab-candidates / %d not-found DOI refs"%(e,era_fab[e],era_tot[e]))
print("\n=== random 40 FAB_CANDIDATE for hand-inspection ===")
random.seed(42)
for yr,d,raw in random.sample(fab_samples, min(40,len(fab_samples))):
    print("  [%d] %s  ::  %s"%(yr,d,raw))
# dump all candidates
with open(os.path.join(D,"fab_candidates.tsv"),"w") as o:
    o.write("year\tdoi\traw\n")
    for yr,d,raw in sorted(fab_samples): o.write("%d\t%s\t%s\n"%(yr,d,raw.replace(chr(9),' ')))
print("\nwrote all %d candidates -> %s/fab_candidates.tsv"%(len(fab_samples),D))
