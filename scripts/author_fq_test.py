#!/usr/bin/env python3
"""
author_fq_test.py — measure the cost of the unquoted author filter in solr_by_metadata.

THE SUSPECTED BUG. Phase 2.6 builds  fq = author_names:{author}  with no quotes. For a
two-token author ("Stefano Gandolfi") Solr's standard parser reads that as
    author_names:Stefano  OR  <default_field>:Gandolfi
so the filter stops constraining authors and starts matching almost everything
(463,568 docs vs 566 for the quoted form).

WHY THAT CAUSES FALSE NEGATIVES RATHER THAN FALSE MATCHES. The phase accepts a record
ONLY when numFound == 1 (uniqueness guard). A filter that fails to narrow leaves several
docs in the same venue+year+volume, numFound > 1, and the guard REJECTS -- so a genuine
citation is reported not-found. The guard converts an over-broad filter into a miss.

DESIGN. Draw real works from the index that have venue_id + year + volume + authors.
Each is, by construction, a citation that SHOULD verify. Re-issue Phase 2.6's exact
query three ways and count how many are accepted (numFound == 1):
    A) unquoted        -- what ships today
    B) quoted phrase   -- minimal fix
    C) surname token   -- the fix already applied in oa_by_metadata, for consistency
This is a self-consistency test: the ground truth is that every sampled work exists.
"""
import json, urllib.parse, urllib.request, re, sys, collections

SOLR = "http://galaxy:8983/solr/openalexWorks/select"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 200


def q(params):
    params = list(params) + [("wt", "json"), ("facet", "false"), ("hl", "false")]
    url = SOLR + "?" + urllib.parse.urlencode(params, doseq=True)
    with urllib.request.urlopen(url, timeout=90) as r:
        return json.load(r)


def esc(s):
    return re.sub(r'([+\-!(){}\[\]^"~*?:\\/])', r'\\\1', str(s))


# sample real works: need venue_id, year, volume, author_names all present
print("sampling %d real works with venue_id+year+volume+authors ..." % N)
docs = []
for seed in range(0, 40):
    if len(docs) >= N:
        break
    d = q([("q", "*:*"),
           ("fq", ["venue_id:*", "volume:*", "author_names:*",
                   "publication_year:[1990 TO 2024]"]),
           ("fl", "id,venue_id,volume,publication_year,author_names,title"),
           ("rows", "60"), ("sort", "random_%d asc" % (1000 + seed))])
    got = d.get("response", {}).get("docs", [])
    if not got:
        break
    docs.extend(got)
docs = docs[:N]
print("got %d\n" % len(docs))
if not docs:
    sys.exit("no sample -- is random_* dynamic field available?")

acc = collections.Counter()
nf = collections.defaultdict(list)
skipped = 0

for d in docs:
    vid = d.get("venue_id")
    vid = vid[0] if isinstance(vid, list) and vid else vid
    vol = d.get("volume")
    vol = vol[0] if isinstance(vol, list) and vol else vol
    yr = d.get("publication_year")
    au = d.get("author_names") or []
    au = au[0] if isinstance(au, list) and au else au
    if not (vid and vol and yr and au):
        skipped += 1
        continue

    toks = [t for t in re.split(r"[^A-Za-z]+", str(au)) if len(t) >= 3]
    surname = max(toks, key=len) if toks else None

    variants = {
        "A-unquoted (ships today)": "author_names:%s" % esc(au),
        "B-quoted phrase":          'author_names:"%s"' % esc(au),
    }
    if surname:
        variants["C-surname token"] = "author_names:%s" % esc(surname)

    for name, afq in variants.items():
        try:
            r = q([("q", "venue_id:(%s)" % vid),
                   ("fq", ["publication_year:%d" % int(yr),
                           'volume:"%s"' % esc(vol), afq]),
                   ("fl", "id"), ("rows", "3")])
            n = r["response"]["numFound"]
        except Exception:
            n = -1
        nf[name].append(n)
        if n == 1:
            acc[name] += 1

tot = len(docs) - skipped
print("=" * 70)
print("PHASE 2.6 ACCEPTANCE on %d works that are all genuinely in the index" % tot)
print("(accepted = numFound==1, which is what the uniqueness guard requires)")
print("=" * 70)
for name in sorted(nf):
    ns = nf[name]
    ok = acc[name]
    zero = sum(1 for x in ns if x == 0)
    many = sum(1 for x in ns if x > 1)
    err = sum(1 for x in ns if x < 0)
    med = sorted(x for x in ns if x >= 0)
    med = med[len(med) // 2] if med else -1
    print("%-26s accepted %4d/%4d = %5.1f%%   (0 hits %3d, >1 hits %3d, err %d, median numFound %d)"
          % (name, ok, tot, 100.0 * ok / tot if tot else 0, zero, many, err, med))

a = acc.get("A-unquoted (ships today)", 0)
for k in ("B-quoted phrase", "C-surname token"):
    if k in acc and tot:
        print("\n%s recovers %+d works vs shipping (%.1f pp)"
              % (k, acc[k] - a, 100.0 * (acc[k] - a) / tot))
