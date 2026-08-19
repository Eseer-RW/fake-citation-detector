#!/usr/bin/env python3
"""
coverage_audit.py — is the "coverage" bucket real, or is the pipeline missing works
that ARE in the index?

The probe attributed 112/176 false alarms to "absent from both indexes." Two of the
examples (Flaxman et al., Nature 2020; a 1998 PNAS paper) are heavily-cited papers that
are almost certainly indexed. If they are, "coverage" is overcounted and the query layer
has more to recover than 9%.

METHOD. For every ref the probe called COVERAGE, hit Solr directly three ways with the
FULL stored title (not the 66-char preview), and classify:
    IN-INDEX-EXACT    exact title phrase present  -> pipeline SHOULD have matched; bug
    IN-INDEX-LOOSE    present only as a loose phrase / prefix -> normalization mismatch
    MOJIBAKE          the stored title carries corrupted bytes -> extraction damage
    TRULY-ABSENT      no plausible hit any way -> genuine coverage gap
Also tabulate by era: post-2019 absences are suspicious (should be indexed), pre-2000
absences are expected (obscure old works nobody re-indexed).
"""
import json, sys, os, re, urllib.parse, urllib.request, collections

SOLR = "http://galaxy:8983/solr/openalexWorks/select"
IN = "/space/rwang/_speedtest/fpr_false_alarms.jsonl"
rows = [json.loads(l) for l in open(IN)]

# reproduce the probe's COVERAGE class: had a title, not flagged nonacademic, and (by the
# probe's finding) title+year, title-no-year, repair, prefix, metadata all missed.
# Simplest faithful reproduction: re-run the same checks here is expensive; instead just
# take everything with a title that is NOT nonacademic, then re-classify from scratch.
cands = [r for r in rows if (r.get("title") or "").strip() and not r.get("nonacademic")]
print("re-auditing %d titled, non-nonacademic false alarms\n" % len(cands))

_MOJI = re.compile(r"[±·¡¿Ã¢â€™Ââ�]|â€|Ã©|Ã¨|Ã¼")


def q(qstr, extra=None):
    p = [("q", qstr), ("rows", "3"), ("wt", "json"), ("facet", "false"),
         ("hl", "false"), ("fl", "id,title,publication_year")]
    for e in (extra or []):
        p.append(("fq", e))
    url = SOLR + "?" + urllib.parse.urlencode(p, doseq=True)
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            d = json.load(r)
        return d["response"]["numFound"], d["response"]["docs"]
    except Exception as e:
        return -1, []


def esc(s):
    return re.sub(r'([+\-!(){}\[\]^"~*?:\\/])', r'\\\1', str(s))


def norm(s):
    if isinstance(s, list):
        s = s[0] if s else ""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


CLASS = collections.Counter()
ERA = collections.defaultdict(collections.Counter)
examples = collections.defaultdict(list)

for r in cands:
    t = (r.get("title") or "").strip()
    yr = r.get("year")
    era = ("pre-2000" if (yr or 0) < 2000 else
           "2000-2018" if (yr or 0) < 2019 else "2019+")

    if _MOJI.search(t) or _MOJI.search(r.get("raw") or ""):
        cls = "MOJIBAKE (extraction damage)"
    else:
        # exact phrase on the title field
        n_exact, docs = q('title:"%s"' % esc(t))
        if n_exact >= 1:
            # confirm it is really the same title, not a phrase-substring accident
            same = any(norm(d.get("title")) == norm(t) for d in docs)
            cls = "IN-INDEX-EXACT (pipeline bug)" if same else "IN-INDEX-LOOSE (normalization)"
        else:
            # loose AND of the salient words
            toks = [w for w in norm(t).split() if len(w) > 3][:8]
            if len(toks) >= 3:
                n_loose, docs = q(" AND ".join("title:%s" % esc(w) for w in toks))
                if n_loose >= 1 and any(
                        len(set(norm(d.get("title")).split()) & set(toks)) >= max(3, len(toks) - 1)
                        for d in docs):
                    cls = "IN-INDEX-LOOSE (normalization)"
                else:
                    cls = "TRULY-ABSENT (coverage gap)"
            else:
                cls = "TRULY-ABSENT (coverage gap)"

    CLASS[cls] += 1
    ERA[cls][era] += 1
    if len(examples[cls]) < 4:
        examples[cls].append("%s  [%s, %s]" % (t[:58], r.get("journal") or "-", yr))

n = sum(CLASS.values()) or 1
print("=" * 72)
print("RE-CLASSIFICATION of the 'coverage' bucket")
print("=" * 72)
for c, k in sorted(CLASS.items(), key=lambda kv: -kv[1]):
    eras = "  ".join("%s:%d" % (e, ERA[c][e]) for e in ("2019+", "2000-2018", "pre-2000") if ERA[c][e])
    print("%-34s %4d  %5.1f%%   (%s)" % (c, k, 100.0 * k / n, eras))

recoverable = (CLASS.get("IN-INDEX-EXACT (pipeline bug)", 0)
               + CLASS.get("IN-INDEX-LOOSE (normalization)", 0)
               + CLASS.get("MOJIBAKE (extraction damage)", 0))
print("\npotentially recoverable (in index OR fixable damage): %d of %d  (%.0f%%)"
      % (recoverable, n, 100.0 * recoverable / n))
print("genuine coverage gap (need a 3rd source)           : %d  (%.0f%%)"
      % (CLASS.get("TRULY-ABSENT (coverage gap)", 0),
         100.0 * CLASS.get("TRULY-ABSENT (coverage gap)", 0) / n))

print("\nEXAMPLES")
for c in sorted(examples, key=lambda c: -CLASS[c]):
    print("\n--- %s ---" % c)
    for e in examples[c]:
        print("   " + e)
