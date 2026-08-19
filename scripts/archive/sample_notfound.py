#!/usr/bin/env python3
"""
sample_notfound.py — dump the TEXT of unmatched references, spread across years.

Two purposes:
  1. Training/eval data for a non-academic classifier. The sweep's per_ref rows
     carry only flags, not text, so nothing can be classified from them.
  2. Answer the drift question directly: if the COMPOSITION of the not-found
     bucket shifts over time (more websites/software/datasets in later years,
     say), that composition shift IS the month-to-month drift that caps this
     study's power at ~25%. Cleaning the denominator would then remove it.
     Sampling several years is what makes that comparison possible.

Output: one JSONL row per unmatched reference, with raw text + parsed fields +
the existing heuristic's verdict, ready for labelling.
"""
import sys, os, json, random, tarfile, tempfile, shutil, pathlib, argparse, threading, time
from concurrent.futures import ThreadPoolExecutor

SCRIPTS = "/space/rwang/fake-citation-detector/scripts"
sys.path.insert(0, SCRIPTS); os.chdir(SCRIPTS)
os.environ.setdefault("SKIP_OPENALEX_API", "1")
os.environ.setdefault("SKIP_CROSSREF_API", "1")
import batch_verify_years as bvy
import solr_lookup
from solr_lookup import SolrLookup

solr_lookup.SOLR_TIMEOUT = 60
bvy.GROBID_HOSTS = ["http://localhost:8070", "http://localhost:8071",
                    "http://localhost:8072", "http://localhost:8073"]
bvy.GROBID_ENDPOINT = "processReferences"
bvy.GROBID_TIMEOUT = 120

BASE = "/space/eric/citation_data/arxiv/pdf/new"
_tl = threading.local()
_SEM = threading.Semaphore(8)


def _solr():
    if not hasattr(_tl, "s"):
        _tl.s = SolrLookup()
    return _tl.s


def sample_extract(tar_path, k, seed, dest):
    r = random.Random(seed)
    tf = tarfile.open(tar_path, "r")
    v1 = [m for m in tf if m.isfile() and m.name.endswith("v1.pdf")]
    pick = v1 if len(v1) <= k else r.sample(v1, k)
    paths = []
    for m in pick:
        try:
            tf.extract(m, dest)
            paths.append((pathlib.Path(dest) / m.name.lstrip("./"),
                          m.name.lstrip("./").replace("v1.pdf", "")))
        except Exception:
            pass
    tf.close()
    return paths


def process(pdf_path, arxiv_id, month, out, lock):
    try:
        tei = bvy.grobid_process(pdf_path)
        refs = bvy.parse_tei_refs(tei) if tei else []
        if not refs:
            return 0
        with _SEM:
            res = bvy.verify_refs(refs, _solr())
        rows = []
        for pr in res.get("per_ref", []):
            if pr.get("found"):
                continue
            r = refs[pr["i"]]
            rows.append({
                "month": month, "paper": arxiv_id, "i": pr["i"],
                "raw": (getattr(r, "raw", "") or "")[:600],
                "title": getattr(r, "title", None),
                "journal": getattr(r, "journal", None),
                "year": getattr(r, "year", None),
                "doi": getattr(r, "doi", None),
                "first_author": getattr(r, "first_author", None),
                "volume": getattr(r, "volume", None),
                # what the CURRENT heuristic thinks -- the baseline to beat
                "heuristic_nonacademic": bool(pr.get("nonacademic")),
                "label": None,          # to be filled by the labelling pass
            })
        with lock:
            for row in rows:
                out.write(json.dumps(row) + "\n")
            out.flush()
        return len(rows)
    except Exception:
        return 0
    finally:
        try: pdf_path.unlink()
        except Exception: pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", default="1901,2101,2301,2501",
                    help="spread across years so composition drift is visible")
    ap.add_argument("--k", type=int, default=30)
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--out", default="/space/rwang/_speedtest/notfound_sample.jsonl")
    a = ap.parse_args()

    # warm both services before timing anything
    months = [m.strip() for m in a.months.split(",") if m.strip()]
    tmp0 = tempfile.mkdtemp(prefix="warm_")
    p0 = sample_extract(f"{BASE}/{months[0]}.tar", 1, 1, tmp0)
    if p0:
        print("grobid warmup:", bvy.grobid_warmup(p0[0][0]), flush=True)
    shutil.rmtree(tmp0, ignore_errors=True)

    lock = threading.Lock()
    total = 0
    with open(a.out, "w") as out:
        for mo in months:
            tar = f"{BASE}/{mo}.tar"
            if not os.path.exists(tar):
                print(f"[{mo}] missing tar", flush=True); continue
            tmp = tempfile.mkdtemp(prefix=f"nf_{mo}_")
            t0 = time.time()
            paths = sample_extract(tar, a.k, int(mo), tmp)
            with ThreadPoolExecutor(max_workers=a.workers) as ex:
                counts = list(ex.map(lambda t: process(t[0], t[1], mo, out, lock), paths))
            shutil.rmtree(tmp, ignore_errors=True)
            n = sum(counts)
            total += n
            print(f"[{mo}] papers={len(paths)} unmatched_refs={n} {time.time()-t0:.0f}s",
                  flush=True)
    print(f"\nwrote {total} unmatched references -> {a.out}", flush=True)


if __name__ == "__main__":
    main()
