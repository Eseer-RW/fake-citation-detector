#!/usr/bin/env python3
"""
similarity_threshold_test.py — should a title-similarity threshold reclassify a
not-found citation as "not fabricated"? Answer it with the eval, not opinion.

THE ONLY QUESTION THAT MATTERS: are the title-similarity distributions of
  (a) the 114 INJECTED FABRICATIONS      -- must stay flagged
  (b) the ~162 GENUINE MISSED papers     -- we'd like to recover
separable? If a threshold exists that recovers many of (b) while leaking almost none of
(a), a corroborated threshold is safe. If they overlap, the exact-only rule is vindicated.

METHOD. For each ref title, pull up to 25 candidate titles from Solr with a loose token
query, and score the BEST candidate two ways on the canonical key:
    ratio   = difflib SequenceMatcher ratio (char-level, 0..1)
    jaccard = token-set overlap (word-level, 0..1)
Also record whether that best candidate CORROBORATES on metadata (author surname present
in candidate authors, OR publication_year within +-1 of cited year) -- the safe variant.

Then sweep thresholds and report, at each: fabrications leaked (NEW false negatives) vs
genuine papers recovered (FPR reduction), for title-only AND title+corroboration.

Reads fabrications from the eval; genuine misses from the gate's residual dump.
"""
import json, glob, os, sys, re, difflib, collections, urllib.parse, urllib.request

sys.path.insert(0, "/space/rwang/fake-citation-detector/scripts")
from title_normalize import normalize_title_key

EVAL = "/space/rwang/fake-citation-detector/eval/fake_injection/cited_sent"
FA = "/space/rwang/_speedtest/fpr_false_alarms.jsonl"
SOLR = "http://galaxy:8983/solr/openalexWorks/select"


def solr_candidates(title, year=None, rows=25):
    key = normalize_title_key(title)
    toks = [w for w in key.split() if len(w) > 2]
    if len(toks) < 3:
        return []
    # loose OR query on salient tokens; no year gate (we want the best twin at ANY year)
    q = " ".join(toks[:12])
    p = [("q", "title:(%s)" % q), ("rows", str(rows)), ("wt", "json"),
         ("facet", "false"), ("hl", "false"),
         ("fl", "title,author_names,publication_year")]
    url = SOLR + "?" + urllib.parse.urlencode(p)
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.load(r)["response"]["docs"]
    except Exception:
        return []


def best_match(title, year, author):
    key = normalize_title_key(title)
    kw = set(key.split())
    surname = None
    if author:
        t = [w for w in re.split(r"[^A-Za-z]+", str(author)) if len(w) >= 3]
        surname = (max(t, key=len) if t else "").lower() or None
    best = (0.0, 0.0, False)
    for d in solr_candidates(title, year):
        ct = d.get("title")
        ct = ct[0] if isinstance(ct, list) and ct else ct
        ck = normalize_title_key(ct or "")
        if not ck:
            continue
        ratio = difflib.SequenceMatcher(None, key, ck).ratio()
        cw = set(ck.split())
        jac = len(kw & cw) / len(kw | cw) if (kw | cw) else 0.0
        # corroboration against THIS candidate
        corrob = False
        anames = " ".join(d.get("author_names") or []).lower() if isinstance(d.get("author_names"), list) else str(d.get("author_names") or "").lower()
        if surname and surname in anames:
            corrob = True
        cy = d.get("publication_year")
        if year and cy:
            try:
                if abs(int(cy) - int(year)) <= 1:
                    corrob = corrob or (jac >= 0.5)   # year alone corroborates only w/ decent title
            except Exception:
                pass
        score = max(ratio, jac)
        if score > best[0]:
            best = (score, jac, corrob)
    return best


# ---- fabrications ----
fabs = []
for f in glob.glob(os.path.join(EVAL, "*.json")):
    for c in json.load(open(f)):
        if c.get("_is_fake") and (c.get("title") or "").strip():
            fabs.append(c)

# ---- genuine misses ----
misses = [json.loads(l) for l in open(FA) if (json.loads(l).get("title") or "").strip()]

print("fabrications with a title : %d" % len(fabs))
print("genuine misses with title : %d\n" % len(misses))
print("scoring against the index (this makes ~%d loose Solr queries)...\n"
      % (len(fabs) + len(misses)))

fab_scores, miss_scores = [], []
for c in fabs:
    au = c.get("authors")
    au = (";".join(map(str, au)) if isinstance(au, list) else au) or ""
    fab_scores.append(best_match(c.get("title"), c.get("year"), au.split(";")[0] if au else None))
for r in misses:
    miss_scores.append(best_match(r.get("title"), r.get("year"), r.get("first_author")))


def dist(scores, label):
    vals = sorted(s[0] for s in scores)
    n = len(vals)
    print("%s (n=%d): min %.2f  p25 %.2f  median %.2f  p75 %.2f  p90 %.2f  max %.2f"
          % (label, n, vals[0], vals[int(.25*n)], vals[int(.5*n)],
             vals[int(.75*n)], vals[int(.9*n)], vals[-1]))


print("=" * 74)
print("BEST TITLE-SIMILARITY TO THE INDEX  (max of char-ratio, token-jaccard)")
print("=" * 74)
dist(fab_scores, "FABRICATIONS (must stay flagged) ")
dist(miss_scores, "GENUINE MISSES (want to recover)  ")

print("\n" + "=" * 74)
print("THRESHOLD SWEEP")
print("=" * 74)
print("%-6s %22s %22s" % ("thr", "TITLE-ONLY", "TITLE + corroboration"))
print("%-6s %10s %10s   %10s %10s" %
      ("", "fabs leak", "real rec", "fabs leak", "real rec"))
for thr in (0.99, 0.95, 0.90, 0.85, 0.80, 0.75, 0.70):
    fl = sum(1 for s in fab_scores if s[0] >= thr)
    rr = sum(1 for s in miss_scores if s[0] >= thr)
    flc = sum(1 for s in fab_scores if s[0] >= thr and s[2])
    rrc = sum(1 for s in miss_scores if s[0] >= thr and s[2])
    print("%-6.2f %8d/%d %8d/%d   %8d/%d %8d/%d" %
          (thr, fl, len(fab_scores), rr, len(miss_scores),
           flc, len(fab_scores), rrc, len(miss_scores)))

print("\nfabs leak = fabrications that would be WRONGLY reclassified as not-fabricated")
print("            (new false negatives -- the cost)")
print("real rec  = genuine papers recovered from the false-alarm bucket (the benefit)")
print("a usable threshold needs real-rec high while fabs-leak stays ~0.")
