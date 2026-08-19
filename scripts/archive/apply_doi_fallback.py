#!/usr/bin/env python3
"""
apply_doi_fallback.py — extend the curated->works hybrid fallback to SolrLookup._solr_get,
which by_doi/all_by_doi use. v5 regressed 340 DOI matches vs v4 because the DOI phase hit
curated only (curated drops arXiv/preprint/proceedings DOIs that Crossref also lacks) with
no fallback. Adding it at _solr_get covers the DOI phase (and every other SolrLookup query)
uniformly, matching integrated_lookup._oa_resp. Same env var, so one setting flips both.
"""
import shutil, sys, py_compile
SL = "/space/rwang/fake-citation-detector/scripts/solr_lookup.py"
s = open(SL, encoding="utf-8").read()

# 1. add fallback URL next to SOLR_URL
a1 = ('SOLR_URL = _os.environ.get("SOLR_OPENALEX_URL",\n'
      '    "http://galaxy:8983/solr/openalexWorksCurated/select")\n')
r1 = a1 + ('_FALLBACK_URL = _os.environ.get("SOLR_OPENALEX_FALLBACK_URL",\n'
           '    "http://galaxy:8983/solr/openalexWorks/select")\n')
if s.count(a1) != 1:
    sys.exit("ABORT: SOLR_URL anchor count = %d" % s.count(a1))

# 2. make _solr_get curated-first + works-fallback on a zero-hit
a2 = ('    try:\n'
      '        resp = requests.get(SOLR_URL, params=params,\n'
      '                            timeout=(timeout if timeout is not None else SOLR_TIMEOUT))\n'
      '        resp.raise_for_status()\n'
      '        return resp.json()\n'
      '    except requests.exceptions.Timeout:\n')
r2 = ('    _to = timeout if timeout is not None else SOLR_TIMEOUT\n'
      '    try:\n'
      '        resp = requests.get(SOLR_URL, params=params, timeout=_to)\n'
      '        resp.raise_for_status()\n'
      '        data = resp.json()\n'
      '        # HYBRID FALLBACK: curated (SOLR_URL) is smaller & faster but drops real\n'
      '        # works (arXiv/preprint/proceedings DOIs, e.g. XGBoost). On a zero-hit, retry\n'
      '        # the full index so coverage matches works. Only fires on misses; keeps the\n'
      '        # numFound==1 guards intact (a >1 fallback result is returned as-is).\n'
      '        if (_FALLBACK_URL and _FALLBACK_URL != SOLR_URL\n'
      '                and data.get("response", {}).get("numFound", 0) == 0):\n'
      '            try:\n'
      '                r2 = requests.get(_FALLBACK_URL, params=params, timeout=_to)\n'
      '                r2.raise_for_status()\n'
      '                d2 = r2.json()\n'
      '                if d2.get("response", {}).get("numFound", 0) > 0:\n'
      '                    return d2\n'
      '            except Exception:\n'
      '                pass\n'
      '        return data\n'
      '    except requests.exceptions.Timeout:\n')
if s.count(a2) != 1:
    sys.exit("ABORT: _solr_get body anchor count = %d" % s.count(a2))

s = s.replace(a1, r1, 1).replace(a2, r2, 1)
shutil.copy(SL, SL + ".bak_doifallback")
open(SL, "w", encoding="utf-8").write(s)
py_compile.compile(SL, doraise=True)
print("DOI/_solr_get hybrid fallback applied (backup .bak_doifallback); compiles OK")
