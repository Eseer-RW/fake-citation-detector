#!/usr/bin/env python3
"""
diag_phase3.py — where exactly does the exact-metadata phase die?

97 of 410 unmatched refs are physics-style (journal abbrev in the title slot), and 75%
of them satisfy Phase 3's eligibility gate (year+journal+volume+page/author) yet still
came back NOT FOUND. Only 23% are pre-2000, so index coverage does not explain it.

oa_by_metadata builds:  (venue_id:.. OR ..) AND volume:".."   fq=year+-1
then requires numFound==1 after adding EITHER first_page:".." OR author_names:"..".

So there are four places it can fail, and this walks each one for real references:
  1. journal_authority resolves the cited abbreviation to venue_ids at all?
  2. venue+volume+year -> how many candidates? (if 0, venue or volume is wrong)
  3. +first_page      -> exactly 1?  (page extracted? matches the indexed field?)
  4. +author_names    -> exactly 1?  (EXACT PHRASE match -- cited "W.G. Unruh" vs
                        indexed "William G. Unruh" would fail, and note the matcher is
                        STRICTER here than validate_metadata's surname-token compare)
"""
import sys, os, json, re
sys.path.insert(0, "/space/rwang/fake-citation-detector/scripts")
os.chdir("/space/rwang/fake-citation-detector/scripts")
os.environ.setdefault("SKIP_OPENALEX_API", "1")
os.environ.setdefault("SKIP_CROSSREF_API", "1")
import requests

OA = "http://galaxy:8983/solr/openalexWorks/select"


def esc(s):
    return re.sub(r'(["\\])', r"\\\1", str(s))


def nf(q, fq=None):
    p = {"q": q, "facet": "false", "hl": "false", "wt": "json", "rows": 3,
         "fl": "id,title,doi,publication_year,venue_name,volume,first_page,author_names"}
    if fq:
        p["fq"] = fq
    try:
        r = requests.get(OA, params=p, timeout=90).json()["response"]
        return r.get("numFound", -1), r.get("docs", [])
    except Exception as e:
        return -2, [("ERR", type(e).__name__)]


CASES = [
    # raw, journal, volume, first_page, first_author, year   (from the real sample)
    ("W.G. Unruh, Phys. Rev. D14, 870 (1976).", "Phys. Rev. D", "14", "870", "W.G. Unruh", 1976),
    ("S. Fulling, Phys. Rev. D7, 2850 (1973)", "Phys. Rev. D", "7", "2850", "S. Fulling", 1973),
    ("Y. Yang, C. Peng, ... Phys. Rev. Lett. 113, 037401 (2014).", "Phys. Rev. Lett", "113", "037401", "Y. Yang", 2014),
    ("S. Gandolfi, A. Gezerlis, J. Carlson, Annu. Rev. Nucl. Part. Sci. 65, 303 (2015).", "Annu. Rev. Nucl. Part. Sci", "65", "303", "S. Gandolfi", 2015),
    ("A. Gezerlis and J. Carlson, Phys. Rev. C 77, 032801(R) (2008).", "Phys. Rev. C", "77", "032801", "A. Gezerlis", 2008),
    ("N. Kaiser, Eur. Phys. J. A 48, 148 (2012).", "Eur. Phys. J. A", "48", "148", "N. Kaiser", 2012),
]

try:
    from journal_authority import venue_ids_for
except Exception as e:
    print("journal_authority import FAILED:", e)
    venue_ids_for = lambda x: []

for raw, j, vol, pg, au, yr in CASES:
    print("=" * 78)
    print(raw)
    vids = []
    try:
        vids = venue_ids_for(j) or []
    except Exception as e:
        print("   venue_ids_for raised:", type(e).__name__, e)
    print("  1. journal_authority: %r -> %d venue_id(s) %s"
          % (j, len(vids), [str(v).rsplit('/', 1)[-1] for v in vids][:4]))

    fq = "publication_year:[%d TO %d]" % (yr - 1, yr + 1)
    base = []
    if vids:
        base.append("(%s)" % " OR ".join('venue_id:"%s"' % str(v).rsplit("/", 1)[-1] for v in vids))
    base.append('volume:"%s"' % esc(vol))
    bq = " AND ".join(base)

    n2, docs2 = nf(bq, fq)
    print("  2. venue+volume+year          -> numFound=%s" % n2)
    if n2 == 0:
        # which half is wrong?
        nv, _ = nf(base[0], fq) if vids else (None, None)
        nvol, _ = nf('volume:"%s"' % esc(vol), fq)
        print("        venue alone=%s   volume alone=%s   <-- 0 means that field is the problem"
              % (nv, nvol))

    n3, docs3 = nf(bq + ' AND first_page:"%s"' % esc(pg), fq)
    print("  3. + first_page:%-8r      -> numFound=%s %s" % (pg, n3, "UNIQUE -> MATCH" if n3 == 1 else ""))

    n4, docs4 = nf(bq + ' AND author_names:"%s"' % esc(au), fq)
    print("  4. + author_names:%-14r-> numFound=%s %s" % (au, n4, "UNIQUE -> MATCH" if n4 == 1 else ""))

    # what does the index actually hold for the author, if we can see a candidate?
    if n2 and n2 > 0 and docs2 and isinstance(docs2[0], dict):
        d = docs2[0]
        an = d.get("author_names") or []
        print("     sample candidate: fp=%r authors=%s" % (d.get("first_page"), (an[:3] if isinstance(an, list) else an)))
        print("        title: %s" % str(d.get("title"))[:70])
