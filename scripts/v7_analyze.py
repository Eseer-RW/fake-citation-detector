#!/usr/bin/env python3
"""
v7_analyze.py — Zhao-replication test on the full 208k-paper sweep.
Two channels, each with a multi-form baseline fit and post-ChatGPT (>=2211) excess:
  A) ALL academic refs        (parse-dependent: extraction damage inflates it)
  B) DOI-bearing refs         (parse-INDEPENDENT arbiter)
  C) DOI-bearing, lag-robust  (cited_year <= citing_year-2, so the cited work was indexable)
Fabrication (Zhao) predicts excess ABOVE the secular baseline post-2022. Coverage/indexing-lag
predicts a smooth decline with NO post-2022 step. Sign-flip across fit forms => artifact, not signal.
"""
import json, os, math, glob, collections

D="/space/rwang/fake-citation-detector/scripts/arxiv_sweep_v7"
# exact 30-shard split -> the authoritative, self-consistent file set
months=[]; y,m=9,1
while (y,m)<=(26,6):
    months.append("%02d%02d"%(y,m)); m+=1
    if m>12: m=1;y+=1
N=30; sz=math.ceil(len(months)/N)
pairs=[(months[i],months[i:i+sz][-1]) for i in range(0,len(months),sz)]
files=[os.path.join(D,"refs_%s_%s_k1000.jsonl"%(S,E)) for S,E in pairs]
files=[f for f in files if os.path.exists(f)]

# per citing-month tallies
A=collections.defaultdict(lambda:[0,0])   # academic: [refs, not_found]
B=collections.defaultdict(lambda:[0,0])   # doi-bearing
C=collections.defaultdict(lambda:[0,0])   # doi-bearing, lag-robust
def mo_to_frac(mo):  # YYMM -> decimal year
    yy=2000+int(mo[:2]); mm=int(mo[2:]); return yy+(mm-0.5)/12.0
nlines=0
for f in files:
    with open(f) as fh:
        for line in fh:
            try: r=json.loads(line)
            except Exception: continue
            nlines+=1
            if r.get("nonacademic"): continue
            mo=r.get("month");
            if not mo: continue
            found=bool(r.get("found"))
            A[mo][0]+=1;  A[mo][1]+= (0 if found else 1)
            if r.get("has_doi"):
                B[mo][0]+=1; B[mo][1]+= (0 if found else 1)
                cy=r.get("cited_year");
                try: cy=int(cy)
                except Exception: cy=None
                citing_y=2000+int(mo[:2])
                if cy is not None and cy<=citing_y-2:
                    C[mo][0]+=1; C[mo][1]+= (0 if found else 1)
print("streamed %d ref-lines from %d files"%(nlines,len(files)))

def series(tab):
    xs=[]; ys=[]; ws=[]
    for mo in sorted(tab):
        n,nf=tab[mo]
        if n<50: continue           # drop thin months
        xs.append(mo_to_frac(mo)); ys.append(nf/n); ws.append(n)
    return xs,ys,ws

def fit_forms(xs,ys,ws,cut=2022.83):  # ChatGPT Nov 2022
    import numpy as np
    x=np.array(xs); y=np.array(ys); w=np.array(ws,float)
    base=x<cut; post=x>=cut
    xb,yb,wb=x[base],y[base],w[base]
    out={}
    # flat (weighted mean)
    flat=np.average(yb,weights=wb)
    out["flat"]=(lambda xx: np.full_like(xx,flat))
    # linear WLS
    A_=np.vstack([xb,np.ones_like(xb)]).T
    W=np.diag(wb)
    coef=np.linalg.lstsq(A_*wb[:,None], yb*wb, rcond=None)[0]
    out["linear"]=(lambda xx,c=coef: c[0]*xx+c[1])
    # exp-decay-to-floor: y=f+(a)exp(-k(x-x0)); fit via coarse grid on k, linear on (f,a)
    x0=xb.min(); best=None
    for k in [0.05,0.1,0.2,0.3,0.5,0.8,1.2,2.0]:
        e=np.exp(-k*(xb-x0)); M=np.vstack([np.ones_like(e),e]).T
        c=np.linalg.lstsq(M*wb[:,None], yb*wb, rcond=None)[0]
        resid=(M@c-yb); sse=np.sum(wb*resid*resid)
        if best is None or sse<best[0]: best=(sse,k,c)
    _,k,c=best
    out["exp"]=(lambda xx,k=k,c=c,x0=x0: c[0]+c[1]*np.exp(-k*(xx-x0)))
    # post-2022 weighted excess (observed - projected), in pp
    res={}
    xp,yp,wp=x[post],y[post],w[post]
    for name,fn in out.items():
        proj=fn(xp); exc=np.average(yp-proj,weights=wp)*100
        res[name]=exc
    return res, flat*100, (yp.mean()*100 if len(yp) else float("nan"))

import numpy as np
print("\n=== CHANNEL A: ALL academic refs (parse-dependent) ===")
xs,ys,ws=series(A); rA,_,postA=fit_forms(xs,ys,ws)
print("  post-2022 mean unmatched: %.2f%%"%postA)
print("  excess vs baseline:  flat=%+.2fpp  linear=%+.2fpp  exp=%+.2fpp"%(rA["flat"],rA["linear"],rA["exp"]))
print("  -> %s"%("SIGN-FLIPS across forms => artifact" if (min(rA.values())<0<max(rA.values())) else "consistent sign"))

print("\n=== CHANNEL B: DOI-bearing refs (parse-INDEPENDENT arbiter) ===")
xs,ys,ws=series(B); rB,_,postB=fit_forms(xs,ys,ws)
tot=sum(v[0] for v in B.values()); nf=sum(v[1] for v in B.values())
print("  total DOI refs=%d  not-found=%d  overall=%.3f%%"%(tot,nf,100*nf/tot))
print("  post-2022 mean unmatched: %.3f%%"%postB)
print("  excess vs baseline:  flat=%+.3fpp  linear=%+.3fpp  exp=%+.3fpp"%(rB["flat"],rB["linear"],rB["exp"]))
print("  -> %s"%("SIGN-FLIPS => no robust excess" if (min(rB.values())<0<max(rB.values())) else "consistent sign"))

print("\n=== CHANNEL C: DOI-bearing, lag-robust (cited_year<=citing-2) ===")
xs,ys,ws=series(C); rC,_,postC=fit_forms(xs,ys,ws)
tot=sum(v[0] for v in C.values()); nf=sum(v[1] for v in C.values())
print("  total=%d  not-found=%d  overall=%.3f%%"%(tot,nf,100*nf/tot))
print("  post-2022 mean unmatched: %.3f%%"%postC)
print("  excess vs baseline:  flat=%+.3fpp  linear=%+.3fpp  exp=%+.3fpp"%(rC["flat"],rC["linear"],rC["exp"]))

# annual DOI-not-found (transparency)
print("\n=== DOI-bearing not-found by citing YEAR ===")
byyr=collections.defaultdict(lambda:[0,0])
for mo,(n,f2) in B.items():
    yr=2000+int(mo[:2]); byyr[yr][0]+=n; byyr[yr][1]+=f2
for yr in sorted(byyr):
    n,f2=byyr[yr]; print("  %d: %8d DOI refs, %.3f%% not-found"%(yr,n,100*f2/n))
