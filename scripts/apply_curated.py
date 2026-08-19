#!/usr/bin/env python3
"""
apply_curated.py — switch the OpenAlex Solr endpoint to openalexWorksCurated via the
SOLR_OPENALEX_URL env var (boss directive). Both live matching files read the SAME env
var, so one setting flips both consistently; default is curated. Overridable back to the
full index for the scientific A/B (curated is a different instrument — see notes).

Verified before this change: curated has every field we query (title_exact/title/volume/
first_page/venue_id/year/author_names/title_norm), dropped the MPG.PuRe junk records that
caused false matches, and retained 98% of matches on a 60-ref head-to-head (the one loss
was a conference-proceedings DOI).
"""
import shutil, sys, py_compile

DEFAULT = "http://galaxy:8983/solr/openalexWorksCurated/select"

def patch(path, old_line, var):
    s = open(path, encoding="utf-8").read()
    if s.count(old_line) != 1:
        sys.exit("ABORT %s: anchor count = %d" % (path, s.count(old_line)))
    new = ('import os as _os  # SOLR_OPENALEX_URL override (boss: curated is faster)\n'
           '%s = _os.environ.get("SOLR_OPENALEX_URL",\n'
           '    "%s")' % (var, DEFAULT))
    shutil.copy(path, path + ".bak_curated")
    open(path, "w", encoding="utf-8").write(s.replace(old_line, new, 1))
    py_compile.compile(path, doraise=True)
    print("patched %s (backup .bak_curated)" % path.rsplit("/", 1)[-1])

patch("/space/rwang/fake-citation-detector/scripts/solr_lookup.py",
      'SOLR_URL = "http://galaxy:8983/solr/openalexWorks/select"', "SOLR_URL")
patch("/space/rwang/fake-citation-detector/scripts/integrated_lookup.py",
      '_OA_SOLR = "http://galaxy:8983/solr/openalexWorks/select"', "_OA_SOLR")

# confirm what each resolves to right now (no env set -> curated default)
sys.path.insert(0, "/space/rwang/fake-citation-detector/scripts")
import importlib, solr_lookup, integrated_lookup
importlib.reload(solr_lookup); importlib.reload(integrated_lookup)
print("resolved SOLR_URL :", solr_lookup.SOLR_URL)
print("resolved _OA_SOLR :", integrated_lookup._OA_SOLR)
