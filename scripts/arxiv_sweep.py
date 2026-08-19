#!/usr/bin/env python3
"""
arxiv_sweep.py — temporal citation-hallucination sweep over the raw arXiv PDF corpus.

Zhao-style design, our verification:
  * source: /space/eric/citation_data/arxiv/pdf/new/<YYMM>.tar  (raw arXiv PDFs)
  * sample K random v1 papers per month (v1 = original submission; one PDF/paper)
  * extract refs from the PDF itself via GROBID  (NO Crossref sourcing shortcut)
  * verify each ref locally: DOI -> exact metadata -> exact-title  (full accuracy)
  * record per-paper + per-month unmatched counts -> JSONL + CSV

Resumable: months already present in the summary CSV are skipped.
"""
import sys, os, time, random, json, gzip, tarfile, tempfile, shutil, pathlib, argparse, threading, types
from concurrent.futures import ThreadPoolExecutor, as_completed

SCRIPTS = "/space/rwang/fake-citation-detector/scripts"
sys.path.insert(0, SCRIPTS); os.chdir(SCRIPTS)
os.environ.setdefault("SKIP_OPENALEX_API", "1")   # verify stays fully local
os.environ.setdefault("SKIP_CROSSREF_API", "1")   # no live Crossref API: local dump only (fixed snapshot)
import batch_verify_years as bvy
import solr_lookup
from solr_lookup import SolrLookup

# Raise the Solr GET timeout for sweep use. The 8s default is at or below measured
# cold-index latency, and a timed-out lookup is silently counted as NOT FOUND --
# which would make this study's headline metric depend on how busy the box is.
solr_lookup.SOLR_TIMEOUT = 60

BASE = "/space/eric/citation_data/arxiv/pdf/new"

# GROBID and Solr have very different concurrency ceilings and MUST be gated
# separately. GROBID scales to ~32 across 4 containers; Solr does not -- running
# the whole pool against it produced ~90 simultaneous connections, stalled an
# outside query past 90s, and turned a 20-paper month into >15 minutes. One
# worker pool still drives both stages, but this semaphore caps how many workers
# may be inside verify_refs at once, so GROBID keeps its parallelism while Solr
# sees a bounded load.
_SOLR_SEM = threading.Semaphore(8)

# Warmup probes: (year, doi, title). Spread across the sweep's year range and across
# fields so the DOI, exact-metadata and exact-title code paths each touch the index
# and pull their blocks into page cache before the worker pool starts.
_WARM_PROBES = [
    (2013, "10.1038/nature12373",            "Sequence-based prediction"),
    (2015, "10.1126/science.1259855",        "Ebola virus epidemiology"),
    (2017, "10.1038/nmeth.3317",             "HISAT: a fast spliced aligner"),
    (2019, "10.1093/bioinformatics/btz070",  "Extracting biomedical entities"),
    (2020, "10.1038/s41586-020-2649-2",      "Array programming with NumPy"),
    (2020, "10.1001/jama.2020.1585",         "Characteristics of COVID-19 patients"),
    (2021, "10.1103/PhysRevLett.116.061102", "Observation of gravitational waves"),
    (2022, "10.1016/j.cell.2015.05.002",     "Cell atlas reference"),
    (2023, "10.1371/journal.pone.0177459",   "Deep learning image classification"),
    (2024, "10.48550/arXiv.1706.03762",      "Attention is all you need"),
]
_grobid_warmed = False
_tl = threading.local()
def _solr():
    if not hasattr(_tl, "s"):
        _tl.s = SolrLookup()
    return _tl.s

def index_state():
    """Snapshot the Solr index identity so runs are comparable after the fact.

    openalexWorks is actively rewritten (observed 7.5-14.5M deleted docs/shard and
    a lastModified that moved mid-week), so a verdict of "not found" is only
    meaningful relative to the index that produced it. Captured at start AND end:
    if they differ, the run straddled an index update and months are not mutually
    comparable.
    """
    import urllib.request
    out = {}
    try:
        u = ("http://galaxy:8983/solr/openalexWorks/select"
             "?q=*:*&rows=0&facet=false&hl=false&wt=json")
        with urllib.request.urlopen(u, timeout=120) as r:
            out["numFound"] = json.loads(r.read())["response"]["numFound"]
    except Exception as e:
        out["numFound_error"] = str(e)
    try:
        u = "http://galaxy:8983/solr/admin/cores?action=STATUS&wt=json"
        with urllib.request.urlopen(u, timeout=120) as r:
            st = json.loads(r.read()).get("status", {})
        out["cores"] = {
            n: {"numDocs": s.get("index", {}).get("numDocs"),
                "lastModified": s.get("index", {}).get("lastModified"),
                "version": s.get("index", {}).get("version")}
            for n, s in st.items() if n.startswith("openalexWorks")
        }
    except Exception as e:
        out["cores_error"] = str(e)
    out["wall"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return out


def months(start, end):
    """Yield YYMM strings from start..end inclusive (e.g. 1901..2606)."""
    s_y, s_m = 2000 + int(start[:2]), int(start[2:])
    e_y, e_m = 2000 + int(end[:2]), int(end[2:])
    y, m = s_y, s_m
    while (y, m) <= (e_y, e_m):
        yield f"{y % 100:02d}{m:02d}"
        m += 1
        if m > 12: y += 1; m = 1

def sample_extract(tar_path, k, seed, dest):
    """Scan v1 members (header-only seek scan), random-sample k, extract to dest."""
    r = random.Random(seed)
    tf = tarfile.open(tar_path, "r")
    v1 = []
    for m in tf:
        nm = m.name
        if m.isfile() and nm.endswith("v1.pdf"):
            v1.append(m)
    # NESTED sampling: draw a fixed 200-paper ordering, then take the first k. With
    # r.sample(v1, k) the draw depends on k, so a K=30 run and a K=100 run share NO
    # papers and a smaller run can never be topped up -- you would re-extract
    # everything. Taking a prefix makes K=30 a strict subset of K=100, so raising K
    # later re-uses the cached TEI for the papers already done.
    if len(v1) <= k:
        pick = v1
    else:
        n = min(max(k, 200), len(v1)); pick = r.sample(v1, n)[:k]
    paths = []
    for m in pick:
        try:
            tf.extract(m, dest)
            paths.append((pathlib.Path(dest) / m.name.lstrip("./"),
                          m.name.lstrip("./").replace("v1.pdf", "")))
        except Exception:
            pass
    tf.close()
    return len(v1), paths


def sample_tei(tei_tar_path, k, seed, dest):
    """Sample k v1 TEI members from a gzipped GROBID-TEI tarball. TWO forward passes so
    cost is K-INDEPENDENT: pass 1 lists v1 names (one decompress), pass 2 streams once more
    and extracts ONLY the picked members inline (forward-only -- a gzip stream re-decompresses
    from the start on any backward seek, so never extract after a full iterate). Nested-seeded
    draw on a sorted name list keeps K a reversible, reproducible prefix."""
    r = random.Random(seed)
    tf = tarfile.open(tei_tar_path, "r:*")
    v1 = sorted(m.name for m in tf if m.isfile() and m.name.endswith("v1.tei.xml"))
    tf.close()
    if len(v1) <= k:
        pick = set(v1)
    else:
        n = min(max(k, 2000), len(v1)); pick = set(r.sample(v1, n)[:k])
    paths = []
    tf = tarfile.open(tei_tar_path, "r:*")
    for m in tf:                       # forward pass: extract picks as we reach them
        if m.name in pick:
            try:
                tf.extract(m, dest)
                nm = m.name.lstrip("./")
                paths.append((pathlib.Path(dest) / nm, nm.replace("v1.tei.xml", "")))
            except Exception:
                pass
    tf.close()
    return len(v1), paths


def process_tei(tei_path, arxiv_id, month=None):
    """Verify a paper straight from a precomputed GROBID TEI file -- no PDF, no GROBID.
    Same parse_tei_refs + verify_refs as process_pdf."""
    rec = {"id": arxiv_id, "tei_source": True}
    try:
        with open(tei_path, encoding="utf-8", errors="replace") as f:
            tei = f.read()
        refs = bvy.parse_tei_refs(tei) if tei else []
        rec["refs"] = len(refs)
        if not refs:
            rec["error"] = "no_refs"; return rec
        with _SOLR_SEM:
            res = bvy.verify_refs(refs, _solr())
        rec.update(total=res["total"], not_found=res["not_found"],
                   not_found_academic=res["not_found_academic"],
                   found_mismatch=res.get("found_mismatch", 0),
                   heuristic_filtered=res.get("heuristic_filtered", 0),
                   heuristic_filter_drift=res.get("heuristic_filter_drift", 0),
                   solr_errors=res.get("solr_errors", {}))
        rec["_per_ref"] = res.get("per_ref", [])
    except Exception as e:
        rec["error"] = str(e)[:200]
    return rec

TEI_CACHE = "/space/rwang/tei_cache"      # shared across runs; set "" to disable


def _tei_cache_path(arxiv_id, month):
    """Cache path for one paper's TEI.

    Keyed by ENDPOINT as well as id: processReferences returns only <listBibl> while
    processFulltextDocument returns the whole document, so a cache shared between them
    would silently serve the wrong TEI and strip the body text.
    """
    if not TEI_CACHE:
        return None
    return os.path.join(TEI_CACHE, bvy.GROBID_ENDPOINT, str(month),
                        str(arxiv_id).replace("/", "_") + ".xml.gz")


def _tei_load(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
            t = f.read()
        return t or None
    except Exception:
        return None                       # corrupt/partial cache entry -> re-extract


def _tei_store(path, tei):
    """Write via a temp file + atomic rename so a killed run can't leave a truncated
    entry that a later run would happily read as complete."""
    if not (path and tei):
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp%d" % os.getpid()
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            f.write(tei)
        os.replace(tmp, path)
    except Exception:
        pass                              # caching is an optimisation, never fatal


def process_pdf(pdf_path, arxiv_id, month=None):
    rec = {"id": arxiv_id}
    try:
        # GROBID is the single most expensive step and its output is a pure function of
        # (pdf, endpoint) -- so cache it. Re-analysis then costs a file read instead of
        # a re-extraction, and the TEI becomes available to other projects.
        cpath = _tei_cache_path(arxiv_id, month) if month is not None else None
        tei = _tei_load(cpath)
        if tei is None:
            tei = bvy.grobid_process(pdf_path)
            _tei_store(cpath, tei)
        else:
            rec["tei_cached"] = True
        refs = bvy.parse_tei_refs(tei) if tei else []
        rec["refs"] = len(refs)
        if not refs:
            rec["error"] = "no_refs"; return rec
        with _SOLR_SEM:                 # bound Solr concurrency (see _SOLR_SEM above)
            res = bvy.verify_refs(refs, _solr())
        rec.update(total=res["total"], not_found=res["not_found"],
                   not_found_academic=res["not_found_academic"],
                   found_mismatch=res.get("found_mismatch", 0),
                   heuristic_filtered=res.get("heuristic_filtered", 0),
                   heuristic_filter_drift=res.get("heuristic_filter_drift", 0),
                   solr_errors=res.get("solr_errors", {}))
        # keep per-ref detail out of the paper-level record; it goes to its own file
        rec["_per_ref"] = res.get("per_ref", [])
    except Exception as e:
        rec["error"] = f"{type(e).__name__}: {e}"
    finally:
        try: pdf_path.unlink()
        except Exception: pass
    return rec

def done_months(summary_csv):
    if not os.path.exists(summary_csv): return set()
    out = set()
    with open(summary_csv) as f:
        for ln in f:
            out.add(ln.split(",")[0])
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="1901"); ap.add_argument("--end", default="2606")
    ap.add_argument("--k", type=int, default=100)
    ap.add_argument("--workers", type=int, default=32)
    # GROBID pool. One container plateaus at ~2.9x no matter how many client
    # threads you throw at it (measured: 8 workers 0.664 s/pdf, 16 workers no
    # better + dropped requests). Four containers at 32 workers = 0.251 s/pdf,
    # a further 2.65x. processReferences skips body parsing for another 4.86x.
    ap.add_argument("--grobid-hosts",
                    default="http://localhost:8070,http://localhost:8071,"
                            "http://localhost:8072,http://localhost:8073",
                    help="comma-separated GROBID base URLs to round-robin")
    ap.add_argument("--endpoint", default="processReferences",
                    choices=["processReferences", "processFulltextDocument"])
    ap.add_argument("--solr-workers", type=int, default=8,
                    help="max workers inside verify_refs at once. Separate from "
                         "--workers because Solr saturates far earlier than GROBID; "
                         "setting this equal to --workers is what caused the stall.")
    ap.add_argument("--grobid-timeout", type=int, default=120,
                    help="per-request seconds (3 retries). 240 let one bad PDF stall "
                         "a month for 12 min; 60 was too tight and silently DROPPED a "
                         "100+ reference paper from 2504, biasing that month's rate. "
                         "120 keeps the stall bounded without losing large papers.")
    ap.add_argument("--tei-cache", default="/space/rwang/tei_cache",
                    help="dir for cached GROBID TEI (keyed by endpoint); \"\" disables")
    ap.add_argument("--tei-source", default="",
                    help="dir of precomputed GROBID-TEI tarballs (<YYMM>.tar.gz); "
                         "when set, read TEI directly instead of PDF+GROBID")
    ap.add_argument("--outdir", default="/space/rwang/fake-citation-detector/results/arxiv_sweep")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)

    # retarget the shared GROBID helper at our pool (defaults in bvy are left
    # alone so the production batch path / verify_pdf keep full-document TEI)
    bvy.GROBID_HOSTS    = [h.strip() for h in a.grobid_hosts.split(",") if h.strip()]
    bvy.GROBID_ENDPOINT = a.endpoint
    bvy.GROBID_TIMEOUT  = a.grobid_timeout
    global _SOLR_SEM
    _SOLR_SEM = threading.Semaphore(a.solr_workers)
    global TEI_CACHE
    TEI_CACHE = a.tei_cache or ""
    print(f"grobid: {len(bvy.GROBID_HOSTS)} host(s) -> /api/{a.endpoint} "
          f"timeout={a.grobid_timeout}s workers={a.workers} "
          f"solr_workers={a.solr_workers}", flush=True)
    jsonl = os.path.join(a.outdir, f"papers_{a.start}_{a.end}_k{a.k}.jsonl")
    refsl = os.path.join(a.outdir, f"refs_{a.start}_{a.end}_k{a.k}.jsonl")
    summ  = os.path.join(a.outdir, f"months_{a.start}_{a.end}_k{a.k}.csv")
    already = done_months(summ)

    # One-time Solr warmup. This MATTERS: an identical DOI query measured 22.23s on a
    # cold index vs 0.01s warm (~2000x). The previous implementation passed an EMPTY
    # ref list, so every phase looped zero times and it issued no queries at all --
    # it warmed nothing despite the comment. Real refs are required.
    try:
        warm_refs = []
        for yr, doi, title in _WARM_PROBES:
            o = types.SimpleNamespace(raw="", doi=doi, year=yr, title=title,
                                      journal="Nature", volume=None,
                                      first_page=None, first_author="Smith")
            warm_refs.append(o)
        t0 = time.time()
        w = bvy.verify_refs(warm_refs, SolrLookup())
        print(f"solr warmup: {len(warm_refs)} probe refs, "
              f"{w['found']}/{w['total']} resolved, {time.time()-t0:.1f}s", flush=True)
    except Exception as e:
        print("solr warmup FAILED:", e, flush=True)

    idxpath = os.path.join(a.outdir, "index_state.json")
    idx0 = index_state()
    print(f"index at start: numFound={idx0.get('numFound')} "
          f"cores={len(idx0.get('cores') or {})}", flush=True)
    try:
        prev = json.load(open(idxpath)) if os.path.exists(idxpath) else {}
    except Exception:
        prev = {}
    prev.setdefault("start", idx0)
    prev["last_start"] = idx0
    json.dump(prev, open(idxpath, "w"), indent=1)

    if not already:
        with open(summ, "w") as f:
            f.write("month,n_papers,v1_pool,sum_refs,sum_not_found,sum_nf_academic,sum_mismatch,errors,unmatched_rate,seconds\n")

    for mo in months(a.start, a.end):
        if mo in already:
            print(f"[{mo}] skip (done)", flush=True); continue
        _tei_mode = bool(a.tei_source)
        tar_path = f"{a.tei_source}/{mo}.tar.gz" if _tei_mode else f"{BASE}/{mo}.tar"
        if not os.path.exists(tar_path):
            print(f"[{mo}] MISSING tar", flush=True); continue
        t0 = time.time()
        tmp = tempfile.mkdtemp(prefix=f"arx_{mo}_")
        try:
            pool_n, paths = (sample_tei if _tei_mode else sample_extract)(tar_path, a.k, seed=int(mo), dest=tmp)
        except Exception as e:
            print(f"[{mo}] sample ERR {e}", flush=True); shutil.rmtree(tmp, ignore_errors=True); continue

        # Warm every GROBID container on a real document before the pool starts.
        # /api/isalive goes true while models are still loading, and requests sent
        # in that window fail -- the source of the stray per-month `errors`.
        global _grobid_warmed
        if not _tei_mode and not _grobid_warmed and paths:
            st = bvy.grobid_warmup(paths[0][0])
            print(f"grobid warmup: {st}", flush=True)
            if not any(st.values()):
                print("ABORT: no GROBID host answered 200", flush=True); return
            bvy.GROBID_HOSTS = [h for h, ok in st.items() if ok]
            _grobid_warmed = True

        recs = []
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = [ex.submit(process_tei if _tei_mode else process_pdf, p, aid, mo) for p, aid in paths]
            for fu in as_completed(futs):
                recs.append(fu.result())
        shutil.rmtree(tmp, ignore_errors=True)
        # aggregate
        ok = [r for r in recs if "total" in r]
        errs = len(recs) - len(ok)
        s_refs = sum(r["total"] for r in ok)
        s_nf = sum(r["not_found"] for r in ok)
        s_nfa = sum(r["not_found_academic"] for r in ok)
        s_mm = sum(r["found_mismatch"] for r in ok)
        rate = (s_nfa / s_refs) if s_refs else 0.0
        dt = time.time() - t0
        with open(jsonl, "a") as f, open(refsl, "a") as g:
            for r in recs:
                r["month"] = mo
                for row in r.pop("_per_ref", []):
                    row["month"] = mo
                    row["paper"] = r.get("id")
                    g.write(json.dumps(row) + "\n")
                f.write(json.dumps(r) + "\n")
        with open(summ, "a") as f:
            f.write(f"{mo},{len(ok)},{pool_n},{s_refs},{s_nf},{s_nfa},{s_mm},{errs},{rate:.5f},{dt:.0f}\n")
        print(f"[{mo}] papers={len(ok)}/{len(paths)} pool={pool_n} refs={s_refs} "
              f"nf_acad={s_nfa} ({rate*100:.2f}%) mism={s_mm} err={errs} {dt:.0f}s", flush=True)
    idx1 = index_state()
    try:
        cur = json.load(open(idxpath))
    except Exception:
        cur = {}
    cur["end"] = idx1
    json.dump(cur, open(idxpath, "w"), indent=1)
    n0, n1 = idx0.get("numFound"), idx1.get("numFound")
    if n0 is not None and n1 is not None and n0 != n1:
        print(f"WARNING: index CHANGED during the run ({n0} -> {n1}, delta "
              f"{n1 - n0:+d}). Months in this sweep were verified against "
              f"different corpora and are NOT strictly comparable.", flush=True)
    else:
        print(f"index stable across run (numFound={n1})", flush=True)
    print("SWEEP_DONE", flush=True)

if __name__ == "__main__":
    main()
