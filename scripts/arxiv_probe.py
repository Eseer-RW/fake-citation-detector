import sys, os, time, subprocess, pathlib, tempfile, statistics as st
sys.path.insert(0, "/space/rwang/fake-citation-detector/scripts")
os.chdir("/space/rwang/fake-citation-detector/scripts")
import batch_verify_years as bvy
from solr_lookup import SolrLookup
solr = SolrLookup()

BASE = "/space/eric/citation_data/arxiv/pdf/new"
TARS = [("pre_LLM_2001", "2001.tar"), ("post_LLM_2404", "2404.tar")]
work = tempfile.mkdtemp(prefix="arxprobe_")
print("workdir:", work, flush=True)

def first_pdfs(tarpath, k):
    r = subprocess.run(f"tar -tf {tarpath} | grep '\\.pdf$' | head -{k}",
                       shell=True, capture_output=True, text=True)
    return r.stdout.split()

g_times, v_times = [], []
for label, tf in TARS:
    tp = f"{BASE}/{tf}"
    names = first_pdfs(tp, 6)
    subprocess.run(["tar", "-xf", tp, "-C", work] + names, check=True)
    print(f"\n=== {label} ({tf}): {len(names)} papers ===", flush=True)
    for n in names:
        p = pathlib.Path(work) / n.lstrip("./")
        if not p.exists():
            print("  MISSING", n); continue
        t0 = time.time()
        try:
            tei = bvy.grobid_process(p)
        except Exception as e:
            print(f"  {n}: GROBID ERR {e}", flush=True); continue
        tg = time.time() - t0
        refs = bvy.parse_tei_refs(tei) if tei else []
        t1 = time.time()
        try:
            res = bvy.verify_refs(refs, solr)
            tv = time.time() - t1
            keys = {k: res[k] for k in ("total", "not_found", "not_found_academic",
                                        "heuristic_filtered") if isinstance(res, dict) and k in res}
            print(f"  {n}: refs={len(refs):3} | {keys} | grobid {tg:5.1f}s verify {tv:5.1f}s", flush=True)
            g_times.append(tg); v_times.append(tv)
        except Exception as e:
            print(f"  {n}: refs={len(refs):3} | VERIFY ERR {type(e).__name__}: {e} | grobid {tg:5.1f}s", flush=True)
            g_times.append(tg)

print("\n===== TIMING =====", flush=True)
if g_times:
    print(f"GROBID/paper : mean {st.mean(g_times):.1f}s  median {st.median(g_times):.1f}s  min {min(g_times):.1f}  max {max(g_times):.1f}  (n={len(g_times)})")
if v_times:
    print(f"VERIFY/paper : mean {st.mean(v_times):.1f}s  median {st.median(v_times):.1f}s")
if g_times:
    per = st.mean(g_times) + (st.mean(v_times) if v_times else 0)
    print(f"\n===== RUNTIME PROJECTION (per paper ~{per:.1f}s) =====")
    for K in (150, 300, 500):
        papers = K * 72  # ~72 months, 2020-01..2025-12
        h = papers * per / 3600
        print(f"  K={K}/mo x 72 mo = {papers:6} papers  ->  {h:5.0f}h @1 worker | {h/8:4.0f}h @8 | {h/16:4.0f}h @16")
print("DONE_PROBE", flush=True)
