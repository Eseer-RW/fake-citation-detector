#!/usr/bin/env python3
"""
s2_coverage.py — how much does Semantic Scholar have that our two sources don't?

The right test population is NOT random references. It is references that (a) our
pipeline failed to match against OpenAlex Solr + local Crossref, and (b) I hand-labelled
as genuinely article-like (journal / conference / preprint). Those are, by construction,
real papers we missed. If Semantic Scholar finds them, that is coverage we lack; if it
does not, a third source buys nothing and the failures are elsewhere.

Rate limit: unauthenticated Semantic Scholar allows roughly 100 requests / 5 min and is
already returning 429, so this sleeps between calls and samples rather than sweeping.
"""
import json, time, sys, urllib.parse, collections
import requests

LAB = "/space/rwang/fake-citation-detector/eval/notfound_labeled.jsonl"
S2 = "https://api.semanticscholar.org/graph/v1/paper/search"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 40
SLEEP = float(sys.argv[2]) if len(sys.argv) > 2 else 3.5

rows = [json.loads(l) for l in open(LAB)]
# article-like AND carrying a usable title -- a title is what S2 search needs
cands = [r for r in rows if r.get("article_like") and (r.get("title") or "").strip()
         and len((r.get("title") or "").split()) >= 4]
print("hand-labelled refs: %d   article-like with a usable title: %d"
      % (len(rows), len(cands)))
print("testing %d of them against Semantic Scholar\n" % min(N, len(cands)))

found = miss = err = 0
by_label = collections.Counter()
hits, misses = [], []

for r in cands[:N]:
    t = (r.get("title") or "").strip()
    try:
        resp = requests.get(S2, params={"query": t, "limit": 3,
                                        "fields": "title,year,externalIds,venue"},
                            timeout=30)
        if resp.status_code == 429:
            print("  429 rate-limited -- backing off 20s"); time.sleep(20)
            resp = requests.get(S2, params={"query": t, "limit": 3,
                                            "fields": "title,year,externalIds,venue"},
                                timeout=30)
        if resp.status_code != 200:
            err += 1; time.sleep(SLEEP); continue
        data = resp.json().get("data") or []
    except Exception:
        err += 1; time.sleep(SLEEP); continue

    # conservative: require a real token overlap, not just "S2 returned something"
    want = set(w for w in t.lower().split() if len(w) > 3)
    ok = False
    for d in data:
        got = set(w for w in (d.get("title") or "").lower().split() if len(w) > 3)
        if want and got and len(want & got) / len(want) >= 0.6:
            ok = True
            hits.append((r.get("label"), t[:52], (d.get("externalIds") or {})))
            break
    if ok:
        found += 1; by_label[r.get("label")] += 1
    else:
        miss += 1; misses.append((r.get("label"), t[:60]))
    time.sleep(SLEEP)

n = found + miss
print("=" * 66)
print("references OUR pipeline missed, that are genuinely article-like")
print("=" * 66)
print("tested            : %d   (errors/skipped %d)" % (n, err))
print("FOUND in Sem.Schol: %d  (%.0f%%)" % (found, 100.0 * found / n if n else 0))
print("also missing there: %d  (%.0f%%)" % (miss, 100.0 * miss / n if n else 0))
if by_label:
    print("\nrecovered, by reference type:")
    for k, v in by_label.most_common():
        print("   %-18s %d" % (k, v))
if hits:
    print("\nexamples S2 has and we don't (with the ids it knows):")
    for lab, t, ext in hits[:6]:
        ids = ",".join("%s=%s" % (k, v) for k, v in list(ext.items())[:3])
        print("   [%s] %s" % (lab, t))
        print("        %s" % (ids or "(no external ids)"))
if misses:
    print("\nstill missing in S2 too (a third source would not help these):")
    for lab, t in misses[:5]:
        print("   [%s] %s" % (lab, t))
