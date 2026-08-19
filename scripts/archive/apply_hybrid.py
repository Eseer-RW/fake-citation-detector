#!/usr/bin/env python3
"""
apply_hybrid.py — curated-first OpenAlex lookup with a full-index fallback on misses.
Curated (140M) is ~10x faster but DROPS real works (measured: XGBoost/KDD, WHO reports,
SIGKDD editorials absent), which raised our FPR 6.8%->7.4%. Fix: query curated first; only
when it returns NO hit, retry the full openalexWorks (367M). The 90%+ that match in curated
keep curated's speed; the not-found tail regains full-index coverage. Env-tunable.
"""
import shutil, sys, py_compile
IL = "/space/rwang/fake-citation-detector/scripts/integrated_lookup.py"
s = open(IL, encoding="utf-8").read()

anchor = ('_OA_SOLR = _os.environ.get("SOLR_OPENALEX_URL",\n'
          '    "http://galaxy:8983/solr/openalexWorksCurated/select")\n')
helper = anchor + (
    '_OA_FALLBACK = _os.environ.get("SOLR_OPENALEX_FALLBACK_URL",\n'
    '    "http://galaxy:8983/solr/openalexWorks/select")\n'
    '\n\n'
    'def _oa_resp(params, timeout=20):\n'
    '    """Curated-first OpenAlex query with a full-index fallback. Curated (140M) is ~10x\n'
    '    faster but drops some real works (conference proceedings, reports, older venues --\n'
    '    measured: XGBoost/KDD and WHO reports are absent), which inflates our FPR. Query\n'
    '    curated first; ONLY when it returns no hit, retry the full openalexWorks (367M) to\n'
    '    restore coverage on the not-found tail. Preserves the numFound==1 uniqueness guard\n'
    '    (a >1 fallback result is returned as-is and correctly rejected upstream). Set\n'
    '    SOLR_OPENALEX_FALLBACK_URL=\'\' to disable."""\n'
    '    try:\n'
    '        r = requests.get(_OA_SOLR, params=params, timeout=timeout).json()["response"]\n'
    '    except Exception:\n'
    '        if _OA_FALLBACK and _OA_FALLBACK != _OA_SOLR:\n'
    '            return requests.get(_OA_FALLBACK, params=params, timeout=timeout).json()["response"]\n'
    '        raise\n'
    '    if r.get("numFound", 0) == 0 and _OA_FALLBACK and _OA_FALLBACK != _OA_SOLR:\n'
    '        try:\n'
    '            r2 = requests.get(_OA_FALLBACK, params=params, timeout=timeout).json()["response"]\n'
    '            if r2.get("numFound", 0) > 0:\n'
    '                return r2\n'
    '        except Exception:\n'
    '            pass\n'
    '    return r\n')

if s.count(anchor) != 1:
    sys.exit("ABORT: _OA_SOLR anchor count = %d" % s.count(anchor))

site_docs = '            docs = requests.get(_OA_SOLR, params=params, timeout=20).json()["response"]["docs"]'
site_resp = '                resp = requests.get(_OA_SOLR, params=params, timeout=20).json()["response"]'
n_docs = s.count(site_docs); n_resp = s.count(site_resp)
if n_docs != 2 or n_resp != 1:
    sys.exit("ABORT: call-site counts docs=%d (want 2) resp=%d (want 1)" % (n_docs, n_resp))

s = s.replace(anchor, helper, 1)
s = s.replace(site_docs, '            docs = _oa_resp(params)["docs"]')
s = s.replace(site_resp, '                resp = _oa_resp(params)')

shutil.copy(IL, IL + ".bak_hybrid")
open(IL, "w", encoding="utf-8").write(s)
py_compile.compile(IL, doraise=True)
print("hybrid fallback applied to integrated_lookup.py (backup .bak_hybrid)")
print("  primary : curated (SOLR_OPENALEX_URL)")
print("  fallback: works   (SOLR_OPENALEX_FALLBACK_URL)")
print("  routed 3 query sites through _oa_resp; compiles OK")
