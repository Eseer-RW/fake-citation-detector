#!/usr/bin/env python3
r"""
apply_local_integration.py — route the three OpenAlex phases (DOI, metadata, title) to the
local oa_index.db (oa_local) when it's available, with Solr as the fallback only when it's
not. Pure-local: a local miss returns not-found (the index is full-works coverage, so
re-querying Solr would just be the same works, slow). Gate validates vs the 6.8%/114-114
baseline. Journal names are resolved via the SAME authority (_norm + canonical_name) the
Crossref biblio path uses. All edits are guarded + anchor-checked.
"""
import shutil, sys, py_compile
IL = "/space/rwang/fake-citation-detector/scripts/integrated_lookup.py"
SL = "/space/rwang/fake-citation-detector/scripts/solr_lookup.py"

il = open(IL, encoding="utf-8").read()
sl = open(SL, encoding="utf-8").read()

# 1. _oa_title_exact -> local title
a1 = ("        if not title:\n"
      "            return None\n"
      "        params = {\"q\": 'title_exact:\"%s\"' % str(title).replace('\"', \" \"),")
r1 = ("        if not title:\n"
      "            return None\n"
      "        try:\n"
      "            import oa_local\n"
      "            if oa_local.available():\n"
      "                from title_normalize import normalize_title_key as _nk\n"
      "                return oa_local.by_title(_nk(title), year)\n"
      "        except Exception:\n"
      "            pass\n"
      "        params = {\"q\": 'title_exact:\"%s\"' % str(title).replace('\"', \" \"),")
if il.count(a1) != 1: sys.exit("ABORT title_exact anchor=%d" % il.count(a1))

# 2. _oa_title_phrase -> local title (after key computed)
a2 = ("        from title_normalize import normalize_title_key\n"
      "        key = normalize_title_key(title)\n")
r2 = (a2 +
      "        try:\n"
      "            import oa_local\n"
      "            if oa_local.available():\n"
      "                return oa_local.by_title(key, year)\n"
      "        except Exception:\n"
      "            pass\n")
if il.count(a2) != 1: sys.exit("ABORT title_phrase anchor=%d" % il.count(a2))

# 3. oa_by_metadata -> local metadata (after the guard)
a3 = ("        j = getattr(ref, \"journal\", None)\n"
      "        if not (vol and yr and (pg or au)):\n"
      "            return nf\n")
r3 = (a3 +
      "        try:\n"
      "            import oa_local\n"
      "            if oa_local.available():\n"
      "                from journal_authority import _norm as _jn, canonical_name as _cn\n"
      "                _norms = set()\n"
      "                for _cand in (j, (_cn(j) if j else None)):\n"
      "                    if _cand:\n"
      "                        _k = _jn(_cand)\n"
      "                        if _k:\n"
      "                            _norms.add(_k)\n"
      "                _d = oa_local.by_metadata(_norms, yr, vol, pg, au)\n"
      "                if _d:\n"
      "                    return SolrResult(found=True, method=MatchMethod.META_MATCH, record=_d, confidence=1.0)\n"
      "                return nf\n"
      "        except Exception:\n"
      "            pass\n")
if il.count(a3) != 1: sys.exit("ABORT metadata anchor=%d" % il.count(a3))

# 4. SolrLookup.by_doi -> local doi
a4 = ("        doi = re.sub(r'^https?://(?:dx\\.)?doi\\.org/', '', doi.strip(), flags=re.I)\n"
      "        doi = doi.lower()\n"
      "        params = {\n")
r4 = ("        doi = re.sub(r'^https?://(?:dx\\.)?doi\\.org/', '', doi.strip(), flags=re.I)\n"
      "        doi = doi.lower()\n"
      "        try:\n"
      "            import oa_local\n"
      "            if oa_local.available():\n"
      "                _d = oa_local.by_doi(doi)\n"
      "                if _d:\n"
      "                    return SolrResult(found=True, method=MatchMethod.DOI, record=_d, confidence=1.0)\n"
      "                return SolrResult(found=False, method=MatchMethod.NOT_FOUND)\n"
      "        except Exception:\n"
      "            pass\n"
      "        params = {\n")
if sl.count(a4) != 1: sys.exit("ABORT by_doi anchor=%d" % sl.count(a4))

shutil.copy(IL, IL + ".bak_localint"); shutil.copy(SL, SL + ".bak_localint")
open(IL, "w", encoding="utf-8").write(il.replace(a1, r1, 1).replace(a2, r2, 1).replace(a3, r3, 1))
open(SL, "w", encoding="utf-8").write(sl.replace(a4, r4, 1))
py_compile.compile(IL, doraise=True); py_compile.compile(SL, doraise=True)
print("local-index integration applied to integrated_lookup.py + solr_lookup.py (backups .bak_localint); compile OK")
