#!/usr/bin/env python3
"""Skip the PMID->Solr lookup in verify_dois when the local index is active (we can't
resolve PMID locally and don't want to touch Solr during the fully-local scale run;
PMID-only refs are rare on arXiv). Anchor-checked."""
import shutil, sys, py_compile
IL = "/space/rwang/fake-citation-detector/scripts/integrated_lookup.py"
s = open(IL, encoding="utf-8").read()
a = ("                _pm = pmid_from_text(_raw)\n"
     "                if _pm and out[i] is None:\n")
r = ("                _pm = pmid_from_text(_raw)\n"
     "                try:\n"
     "                    _localon = __import__(\"oa_local\").available()\n"
     "                except Exception:\n"
     "                    _localon = False\n"
     "                if _pm and out[i] is None and not _localon:\n")
if s.count(a) != 1:
    sys.exit("ABORT pmid anchor=%d" % s.count(a))
shutil.copy(IL, IL + ".bak_pmidlocal")
open(IL, "w", encoding="utf-8").write(s.replace(a, r, 1))
py_compile.compile(IL, doraise=True)
print("pmid path now skips Solr when local index active (backup .bak_pmidlocal); compiles OK")
