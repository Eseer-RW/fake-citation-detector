#!/usr/bin/env python3
r"""
apply_tei_source.py — let arxiv_sweep read Eric's precomputed GROBID-TEI corpus
(/space/eric/citation_data/arxiv/tei/) directly, skipping PDF sampling + GROBID entirely.
Same parse_tei_refs + verify_refs; same nested-seeded sampling (K stays a reversible
prefix). Adds --tei-source; the PDF path is untouched when it's unset.
"""
import shutil, sys, py_compile
AS = "/space/rwang/fake-citation-detector/scripts/arxiv_sweep.py"
s = open(AS, encoding="utf-8").read()

# 1. new functions, inserted right after sample_extract
a_fn = "    tf.close()\n    return len(v1), paths\n"
r_fn = a_fn + '''

def sample_tei(tei_tar_path, k, seed, dest):
    """Sample k v1 TEI members from Eric's GROBID-TEI tarball; mirror sample_extract's
    nested-seeded draw so K stays a reversible prefix. Tarballs are gzipped."""
    r = random.Random(seed)
    tf = tarfile.open(tei_tar_path, "r:*")
    v1 = [m for m in tf if m.isfile() and m.name.endswith("v1.tei.xml")]
    if len(v1) <= k:
        pick = v1
    else:
        pick = r.sample(v1, min(200, len(v1)))[:k]
    paths = []
    for m in pick:
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
'''
if s.count(a_fn) != 1:
    sys.exit("ABORT fn: anchor count %d" % s.count(a_fn))

# 2. --tei-source arg (insert before --outdir)
a_arg = '    ap.add_argument("--outdir",'
r_arg = ('    ap.add_argument("--tei-source", default="",\n'
         '                    help="dir of precomputed GROBID-TEI tarballs (<YYMM>.tar.gz); "\n'
         '                         "when set, read TEI directly instead of PDF+GROBID")\n'
         '    ap.add_argument("--outdir",')
if s.count(a_arg) != 1:
    sys.exit("ABORT arg: anchor count %d" % s.count(a_arg))

# 3. source-aware tar path
a_tp = '        tar_path = f"{BASE}/{mo}.tar"\n'
r_tp = ('        _tei_mode = bool(a.tei_source)\n'
        '        tar_path = f"{a.tei_source}/{mo}.tar.gz" if _tei_mode else f"{BASE}/{mo}.tar"\n')
if s.count(a_tp) != 1:
    sys.exit("ABORT tar_path: anchor count %d" % s.count(a_tp))

# 4. source-aware sampler
a_sm = "            pool_n, paths = sample_extract(tar_path, a.k, seed=int(mo), dest=tmp)\n"
r_sm = "            pool_n, paths = (sample_tei if _tei_mode else sample_extract)(tar_path, a.k, seed=int(mo), dest=tmp)\n"
if s.count(a_sm) != 1:
    sys.exit("ABORT sampler: anchor count %d" % s.count(a_sm))

# 5. skip GROBID warmup in TEI mode
a_wu = "        if not _grobid_warmed and paths:\n"
r_wu = "        if not _tei_mode and not _grobid_warmed and paths:\n"
if s.count(a_wu) != 1:
    sys.exit("ABORT warmup: anchor count %d" % s.count(a_wu))

# 6. source-aware processor
a_pr = "            futs = [ex.submit(process_pdf, p, aid, mo) for p, aid in paths]\n"
r_pr = "            futs = [ex.submit(process_tei if _tei_mode else process_pdf, p, aid, mo) for p, aid in paths]\n"
if s.count(a_pr) != 1:
    sys.exit("ABORT processor: anchor count %d" % s.count(a_pr))

for a, r in [(a_fn, r_fn), (a_arg, r_arg), (a_tp, r_tp), (a_sm, r_sm), (a_wu, r_wu), (a_pr, r_pr)]:
    s = s.replace(a, r, 1)

shutil.copy(AS, AS + ".bak_teisource")
open(AS, "w", encoding="utf-8").write(s)
py_compile.compile(AS, doraise=True)
print("arxiv_sweep.py: --tei-source support added (backup .bak_teisource); compiles OK")
