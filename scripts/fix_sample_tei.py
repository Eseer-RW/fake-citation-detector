#!/usr/bin/env python3
r"""
fix_sample_tei.py — make sample_tei K-independent. The first version iterated all members
then tf.extract()'d each pick; on a gzip stream a backward extract re-decompresses from the
start, so K picks = K full decompresses (~170s for K=10). Rewrite as TWO forward passes:
pass 1 lists v1 names (one decompress ~17-30s), pass 2 streams once more and extracts only
the picked members inline (forward-only, no per-file re-decompress). Total ~2 decompresses,
independent of K.
"""
import shutil, sys, py_compile
AS = "/space/rwang/fake-citation-detector/scripts/arxiv_sweep.py"
s = open(AS, encoding="utf-8").read()

old = '''def sample_tei(tei_tar_path, k, seed, dest):
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
    return len(v1), paths'''

new = '''def sample_tei(tei_tar_path, k, seed, dest):
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
        pick = set(r.sample(v1, min(200, len(v1)))[:k])
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
    return len(v1), paths'''

if s.count(old) != 1:
    sys.exit("ABORT: sample_tei anchor count = %d" % s.count(old))
shutil.copy(AS, AS + ".bak_sampletei2pass")
open(AS, "w", encoding="utf-8").write(s.replace(old, new, 1))
py_compile.compile(AS, doraise=True)
print("sample_tei rewritten to 2-pass forward extraction (backup .bak_sampletei2pass); compiles OK")
