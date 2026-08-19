#!/usr/bin/env python3
"""
v8_analyze.py — full analysis of the 1M-paper sweep with the upgraded detector.
Channels:
  A  all-academic not-found rate (parse-dependent; multi-form baseline, post-ChatGPT excess)
  B  DOI-bearing not-found (parse-independent arbiter)
  C  fab_candidate rate (NEW: not_found cleaned of parse junk / no-title / foreign / non-article)
  D  author_hijack + title_hijack rates on FOUND refs (NEW: the channel existence checks miss;
     detector ROC: recall 57% author / FPR 6.9%; interpret vs pre-2022 baseline)
  E  not_found_reason decomposition by year (what IS the not-found bucket?)
Dedups months across shard/restart files (first file wins).
"""
import json, os, re, glob, collections, math
D = "/space/rwang/fake-citation-detector/scripts/arxiv_sweep_v8b"
CUT = 2022.83  # ChatGPT

files = sorted(glob.glob(os.path.join(D, "refs_*.jsonl")))
month_owner = {}
for f in files:
    for mo in set():
        pass
# first pass: assign each month to the first file containing it (cheap scan of month field)
seen_pairs = {}
for f in files:
    got = set()
    with open(f) as fh:
        for line in fh:
            m = re.search(r'"month": ?"(\d{4})"', line)
            if m:
                got.add(m.group(1))
    for mo in got:
        month_owner.setdefault(mo, f)
print("months found:", len(month_owner), "across", len(files), "files", flush=True)

A = collections.defaultdict(lambda: [0, 0])          # month -> [academic refs, not_found]
B = collections.defaultdict(lambda: [0, 0])          # doi-bearing
C = collections.defaultdict(lambda: [0, 0])          # [academic titled refs, fab_candidate]
H = collections.defaultdict(lambda: [0, 0, 0, 0])    # found refs: [judged, author_hijack, all_found, title_hijack]
R = collections.defaultdict(collections.Counter)     # year -> not_found_reason counts
n = 0
for f in files:
    with open(f) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except Exception:
                continue
            mo = r.get("month")
            if not mo or month_owner.get(mo) != f:
                continue
            if r.get("nonacademic"):
                continue
            n += 1
            yr = str(2000 + int(mo[:2]))
            found = bool(r.get("found"))
            A[mo][0] += 1; A[mo][1] += (0 if found else 1)
            if r.get("has_doi"):
                B[mo][0] += 1; B[mo][1] += (0 if found else 1)
            if found:
                H[yr][2] += 1
                ah = r.get("author_hijack")
                if ah is not None:
                    H[yr][0] += 1; H[yr][1] += (1 if ah else 0)
                if r.get("fab_flag") == "title_hijack":
                    H[yr][3] += 1
            else:
                reason = r.get("not_found_reason") or "none"
                R[yr][reason] += 1
                if r.get("has_title"):
                    C[mo][0] += 1; C[mo][1] += (1 if reason == "fab_candidate" else 0)
            if n % 5000000 == 0:
                print("  %dM refs..." % (n // 1000000), flush=True)
print("streamed %d academic refs" % n, flush=True)

import numpy as np
def mo_frac(mo): return 2000 + int(mo[:2]) + (int(mo[2:]) - 0.5) / 12.0
def series(tab, minn=50):
    xs, ys, ws = [], [], []
    for mo in sorted(tab):
        tot, bad = tab[mo]
        if tot < minn: continue
        xs.append(mo_frac(mo)); ys.append(bad / tot); ws.append(tot)
    return np.array(xs), np.array(ys), np.array(ws, float)
def fit_forms(xs, ys, ws):
    base = xs < CUT; post = xs >= CUT
    xb, yb, wb = xs[base], ys[base], ws[base]
    xp, yp, wp = xs[post], ys[post], ws[post]
    out = {}
    flat = np.average(yb, weights=wb)
    out["flat"] = np.average(yp - flat, weights=wp) * 100
    cf = np.linalg.lstsq(np.vstack([xb, np.ones_like(xb)]).T * wb[:, None], yb * wb, rcond=None)[0]
    out["linear"] = np.average(yp - (cf[0] * xp + cf[1]), weights=wp) * 100
    x0 = xb.min(); best = None
    for k in (0.05, 0.1, 0.2, 0.3, 0.5, 0.8, 1.2, 2.0):
        e = np.exp(-k * (xb - x0)); M = np.vstack([np.ones_like(e), e]).T
        c = np.linalg.lstsq(M * wb[:, None], yb * wb, rcond=None)[0]
        sse = np.sum(wb * (M @ c - yb) ** 2)
        if best is None or sse < best[0]: best = (sse, k, c)
    _, k, c = best
    out["exp"] = np.average(yp - (c[0] + c[1] * np.exp(-k * (xp - x0))), weights=wp) * 100
    return out, np.average(yp, weights=wp) * 100 if len(yp) else float("nan")

for name, tab in (("A: ALL academic not-found", A), ("B: DOI-bearing not-found", B), ("C: fab_candidate (cleaned)", C)):
    xs, ys, ws = series(tab)
    ex, post = fit_forms(xs, ys, ws)
    tot = sum(v[0] for v in tab.values()); bad = sum(v[1] for v in tab.values())
    print("\n=== %s ===" % name)
    print("  overall: %d/%d = %.3f%%   post-2022 mean: %.3f%%" % (bad, tot, 100 * bad / tot, post))
    print("  post-ChatGPT excess: flat=%+.3fpp linear=%+.3fpp exp=%+.3fpp  -> %s" %
          (ex["flat"], ex["linear"], ex["exp"],
           "SIGN-FLIPS (artifact)" if min(ex.values()) < 0 < max(ex.values()) else "consistent sign"))

print("\n=== D: hijack channels on FOUND refs, by year ===")
print("  year   found      judged   author_hijack rate    title_hijack rate")
for yr in sorted(H):
    j, ah, fnd, th = H[yr]
    print("  %s %9d %9d   %6d  %.3f%%      %5d  %.4f%%" %
          (yr, fnd, j, ah, 100 * ah / j if j else 0, th, 100 * th / fnd if fnd else 0))

print("\n=== E: not_found_reason decomposition (selected years) ===")
keys = ["no_title", "parse_junk", "non_article", "foreign_language", "datacite_preprint", "short_title", "fab_candidate", "none"]
print("  year  " + "  ".join("%s" % k[:9] for k in keys))
for yr in sorted(R):
    if yr in ("2010", "2015", "2019", "2021", "2022", "2023", "2024", "2025", "2026"):
        tot = sum(R[yr].values())
        print("  %s  " % yr + "  ".join("%6.1f%%" % (100 * R[yr][k] / tot) for k in keys))

# fab_candidate by year (the headline new channel)
print("\n=== fab_candidate RATE by citing year (of all academic refs) ===")
FY = collections.defaultdict(lambda: [0, 0])
for mo, (t, b) in A.items():
    yr = str(2000 + int(mo[:2])); FY[yr][0] += t
for yr in R:
    FY[yr][1] += R[yr]["fab_candidate"]
for yr in sorted(FY):
    t, b = FY[yr]
    print("  %s: %8d refs, fab_candidate %6d = %.3f%%" % (yr, t, b, 100 * b / t if t else 0))
print("\nANALYZE_DONE")
