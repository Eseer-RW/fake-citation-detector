#!/usr/bin/env python3
"""
apply_fqcache.py — move venue_id/volume/year into fq (with q=*:*) in the LIVE metadata
path integrated_lookup.oa_by_metadata, so Solr's filterCache reuses those bitsets across
the ~47% of metadata lookups that share a journal issue. Proven result-equivalent by
fq_equiv_test.py (53/53). Aborts if the anchor is not unique.
"""
import shutil, sys, py_compile

IL = "/space/rwang/fake-citation-detector/scripts/integrated_lookup.py"
il = open(IL, encoding="utf-8").read()

anchor = (
    "        base = []\n"
    "        if vids:\n"
    "            base.append(\"(%s)\" % \" OR \".join('venue_id:\"%s\"' % str(v).rsplit(\"/\", 1)[-1] for v in vids))\n"
    "        base.append('volume:\"%s\"' % _solr_esc(vol))\n"
    "        fq = \"publication_year:[%d TO %d]\" % (int(yr) - 1, int(yr) + 1)\n"
    "\n"
    "        def _unique(extra):\n"
    "            params = {\"q\": \" AND \".join(base + [extra]), \"fq\": fq, \"facet\": \"false\",\n"
    "                      \"hl\": \"false\", \"wt\": \"json\", \"rows\": 2,\n"
    "                      \"fl\": \"id,title,doi,publication_year,venue_name,volume,author_names\"}\n"
)

replace = (
    "        base_fq = []\n"
    "        if vids:\n"
    "            base_fq.append(\"(%s)\" % \" OR \".join('venue_id:\"%s\"' % str(v).rsplit(\"/\", 1)[-1] for v in vids))\n"
    "        base_fq.append('volume:\"%s\"' % _solr_esc(vol))\n"
    "        base_fq.append(\"publication_year:[%d TO %d]\" % (int(yr) - 1, int(yr) + 1))\n"
    "\n"
    "        def _unique(extra):\n"
    "            # Everything is a FILTER (fq) with q=*:*, so Solr's filterCache reuses the\n"
    "            # venue_id / volume / year bitsets across the ~47%% of metadata lookups that\n"
    "            # share a journal issue (measured on v4 refs). Only the discriminator\n"
    "            # (first_page / author) varies per ref. Result-equivalent to ANDing the\n"
    "            # constraints in q under the numFound==1 guard (verified 53/53) -- just\n"
    "            # cacheable and unscored.\n"
    "            params = {\"q\": \"*:*\", \"fq\": base_fq + [extra], \"facet\": \"false\",\n"
    "                      \"hl\": \"false\", \"wt\": \"json\", \"rows\": 2,\n"
    "                      \"fl\": \"id,title,doi,publication_year,venue_name,volume,author_names\"}\n"
)

n = il.count(anchor)
if n != 1:
    sys.exit("ABORT: oa_by_metadata query anchor count = %d (expected 1)" % n)

shutil.copy(IL, IL + ".bak_fqcache")
open(IL, "w", encoding="utf-8").write(il.replace(anchor, replace, 1))
py_compile.compile(IL, doraise=True)
print("oa_by_metadata: fq-cache refactor applied (backup .bak_fqcache); compiles OK")
