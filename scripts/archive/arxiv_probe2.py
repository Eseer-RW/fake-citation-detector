import sys, os, time, subprocess, pathlib, tempfile, statistics as st
sys.path.insert(0, "/space/rwang/fake-citation-detector/scripts")
os.chdir("/space/rwang/fake-citation-detector/scripts")
import batch_verify_years as bvy
from solr_lookup import SolrLookup
solr = SolrLookup()

BASE = "/space/eric/citation_data/arxiv/pdf/new"
TARS = [("pre_2001", "2001.tar"), ("post_2404", "2404.tar")]
work = tempfile.mkdtemp(prefix="arxprobe2_")

def first_pdfs(tp, k):
    r = subprocess.run(f"tar -tf {tp} | grep '\\.pdf$' | head -{k}", shell=True, capture_output=True, text=True)
    return r.stdout.split()

# warm up Solr / integrated (avoid cold-start skew)
try:
    bvy.verify_refs([], solr); print("warmup ok", flush=True)
except Exception as e:
    print("warmup:", e, flush=True)

papers = []
for label, tf in TARS:
    tp = f"{BASE}/{tf}"; names = first_pdfs(tp, 3)
    subprocess.run(["tar", "-xf", tp, "-C", work] + names, check=True)
    for n in names:
        papers.append((label, pathlib.Path(work) / n.lstrip("./"), n))

print("\npaper                       refs | FAST(DOI+meta) s / nf | FULL(+title) s / nf", flush=True)
fast_t, full_t = [], []
for label, p, n in papers:
    if not p.exists(): continue
    tei = bvy.grobid_process(p)
    refs = bvy.parse_tei_refs(tei) if tei else []
    # FAST: DOI + metadata only (title phase off)
    os.environ["DISABLE_TITLE_MATCH"] = "1"
    t0 = time.time(); rf = bvy.verify_refs(refs, solr); tf_fast = time.time() - t0
    # FULL: add exact-title phase (network-storage crossref title index)
    os.environ["DISABLE_TITLE_MATCH"] = "0"
    t0 = time.time(); rF = bvy.verify_refs(refs, solr); tf_full = time.time() - t0
    fast_t.append(tf_fast); full_t.append(tf_full)
    print(f"  {n:26} {len(refs):3} | {tf_fast:6.1f} / {rf['not_found']:3} | {tf_full:6.1f} / {rF['not_found']:3}", flush=True)

print("\n===== VERIFY timing per paper =====", flush=True)
if fast_t: print(f"FAST (DOI+meta): mean {st.mean(fast_t):.1f}s  median {st.median(fast_t):.1f}s")
if full_t: print(f"FULL (+title)  : mean {st.mean(full_t):.1f}s  median {st.median(full_t):.1f}s")
GROBID = 8.5
for name, vt in (("FAST", fast_t), ("FULL", full_t)):
    if not vt: continue
    per = GROBID + st.mean(vt)
    p = 300 * 72
    print(f"  {name}: ~{per:.1f}s/paper -> K=300/mo x72mo = {p} papers = {p*per/3600:.0f}h@1 | {p*per/3600/8:.0f}h@8 | {p*per/3600/16:.0f}h@16")
print("DONE_PROBE2", flush=True)
