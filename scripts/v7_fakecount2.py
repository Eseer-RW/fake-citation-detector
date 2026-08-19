#!/usr/bin/env python3
"""
v7_fakecount2.py — tighten DOI repair, then re-resolve, to separate extraction damage from
genuine fabrication. The first pass showed the 'fab candidates' are almost all mangled DOIs
(concatenated multi-DOIs, arXiv-id fused on, truncation, LaTeX). Clean each aggressively, try
the cleaned DOI + a prefix probe against OpenAlex(486M) and Crossref(179M). What still resolves
NOWHERE after cleaning, and isn't an arXiv/DataCite preprint DOI, is the true fabrication ceiling.
"""
import json, os, re, math, sqlite3, collections, random
D="/space/rwang/fake-citation-detector/scripts/arxiv_sweep_v7"
months=[]; y,m=9,1
while (y,m)<=(26,6):
    months.append("%02d%02d"%(y,m)); m+=1
    if m>12: m=1;y+=1
N=30; sz=math.ceil(len(months)/N)
pairs=[(months[i],months[i:i+sz][-1]) for i in range(0,len(months),sz)]
files=[os.path.join(D,"refs_%s_%s_k1000.jsonl"%(S,E)) for S,E in pairs if os.path.exists(os.path.join(D,"refs_%s_%s_k1000.jsonl"%(S,E)))]

WELL=re.compile(r'^10\.\d{4,9}/\S+$')
def clean_doi(d):
    """return (cleaned_doi_or_None, is_arxiv_datacite)"""
    if not d: return None,False
    d=d.strip().lower()
    d=re.sub(r'^(https?://)?(dx\.)?doi\.org/','',d)
    d=re.sub(r'^doi:\s*','',d)
    d=d.replace('{_}','_').replace('{','').replace('}','')
    d=d.split()[0] if d.split() else d
    # concatenated: cut at a SECOND '10.' occurrence
    m2=re.search(r'10\..*?(10\.\d{4,9}/)', d)
    if m2: d=d[:m2.start(1)]
    d=d.split(',')[0]           # comma-joined DOIs
    d=d.split('[')[0]           # fused [arxiv-id]
    d=re.sub(r'\.?\(\d.*$','',d) # trailing (34, .(34
    d=d.rstrip(').,;]}> ')
    d=re.sub(r'\.mr\d.*$','',d)  # AMS MR number appended
    d=re.sub(r'(researchgate|preprint).*$','',d)
    d=d.rstrip('.-;,) ')
    arxiv = d.startswith('10.48550/') or d.startswith('10.13140/')  # arXiv & ResearchGate DataCite
    if not WELL.match(d): return None,arxiv
    return d,arxiv

oa=sqlite3.connect("file:/space/rwang/oa_index/oa_index.db?mode=ro",uri=True)
cr=sqlite3.connect("file:/space/rwang/crossref/biblio_index.db?mode=ro",uri=True)
def resolves(d):
    if oa.execute("SELECT 1 FROM oa WHERE doi=? LIMIT 1",(d,)).fetchone(): return True
    if cr.execute("SELECT 1 FROM biblio WHERE doi=? LIMIT 1",(d,)).fetchone(): return True
    # prefix probe catches truncation: cleaned DOI is a prefix of a real one (require reasonably long stem)
    if len(d)>=14:
        if oa.execute("SELECT 1 FROM oa WHERE doi>=? AND doi<? LIMIT 1",(d,d+'￿')).fetchone(): return True
        if cr.execute("SELECT 1 FROM biblio WHERE doi>=? AND doi<? LIMIT 1",(d,d+'￿')).fetchone(): return True
    return False

nf=[]
for f in files:
    for line in open(f):
        try: r=json.loads(line)
        except Exception: continue
        if r.get("nonacademic") or not r.get("has_doi") or r.get("found"): continue
        nf.append((r.get("ref_doi"), r.get("month"), (r.get("raw") or "")[:170]))
print("not-found DOI-bearing refs:", len(nf))

cls=collections.Counter(); era=collections.Counter(); era_tot=collections.Counter(); resid=[]
for doi_raw, mo, raw in nf:
    yr=2000+int(mo[:2]) if mo else 0
    e="pre-2022" if yr<2022 else "2022+"; era_tot[e]+=1
    d,arx=clean_doi(doi_raw)
    if d is None:
        cls["unrepairable_extraction"]+=1; continue
    if arx and not resolves(d):
        cls["arxiv_datacite_preprint"]+=1; continue   # real preprint, DataCite, not in Crossref/OA snapshot
    if resolves(d):
        cls["real_after_cleaning"]+=1; continue
    cls["TRUE_FAB_CANDIDATE"]+=1; era[e]+=1; resid.append((yr,d,raw))

tot=len(nf)
print("\n=== after aggressive DOI repair (%d not-found DOI refs) ==="%tot)
for k in ("real_after_cleaning","unrepairable_extraction","arxiv_datacite_preprint","TRUE_FAB_CANDIDATE"):
    print("  %-26s %5d  (%.1f%%)"%(k,cls[k],100*cls[k]/tot))
print("\n  TRUE_FAB_CANDIDATE as %% of ALL 1,123,523 DOI refs: %.4f%%"%(100*cls['TRUE_FAB_CANDIDATE']/1123523))
print("  by era: pre-2022=%d  2022+=%d"%(era["pre-2022"],era["2022+"]))
print("\n=== random 40 TRUE_FAB_CANDIDATE (the actual residual to hand-verify) ===")
random.seed(1)
for yr,d,raw in random.sample(resid,min(40,len(resid))):
    print("  [%d] %s :: %s"%(yr,d,raw))
with open(os.path.join(D,"true_fab_residual.tsv"),"w") as o:
    o.write("year\tcleaned_doi\traw\n")
    for yr,d,raw in sorted(resid): o.write("%d\t%s\t%s\n"%(yr,d,raw.replace(chr(9),' ')))
print("\nwrote %d residual -> true_fab_residual.tsv"%len(resid))
