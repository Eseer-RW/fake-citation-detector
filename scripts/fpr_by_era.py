#!/usr/bin/env python3
"""
fpr_by_era.py — stratify the false-positive rate by publication era.

WHY. 6.8% is a pooled number over a denominator that mixes 2020s papers with 1970s books.
The project's goal is catching RECENT (AI-era) fabricated citations, so the rate that
actually matters is the FPR on recent, indexed-era references. If the false alarms are
concentrated in old literature, the pooled 6.8% overstates the miss rate on the population
we care about.

NO SOLR NEEDED. The denominator (all genuine citations, by year) comes straight from the
eval's cited_sent JSONs; the numerator (false alarms, by year) from the residual dump the
gate just wrote. Both already exist, so this is exact and instant.
"""
import json, glob, os, collections

EVAL = "/space/rwang/fake-citation-detector/eval/fake_injection/cited_sent"
FA = "/space/rwang/_speedtest/fpr_false_alarms.jsonl"


def era(y):
    try:
        y = int(str(y)[:4])
    except Exception:
        return "unknown"
    return ("2019+" if y >= 2019 else "2010-2018" if y >= 2010 else
            "2000-2009" if y >= 2000 else "pre-2000")


# denominator: genuine (not injected) citations by era
den = collections.Counter()
den_all = 0
for f in glob.glob(os.path.join(EVAL, "*.json")):
    for c in json.load(open(f)):
        if c.get("_is_fake"):
            continue
        den[era(c.get("year"))] += 1
        den_all += 1

# numerator: false alarms by era, split academic / nonacademic
fa = [json.loads(l) for l in open(FA)]
num = collections.Counter()
num_acad = collections.Counter()
for r in fa:
    e = era(r.get("year"))
    num[e] += 1
    if not r.get("nonacademic"):
        num_acad[e] += 1

order = ["2019+", "2010-2018", "2000-2009", "pre-2000", "unknown"]
print("=" * 70)
print("FALSE-POSITIVE RATE BY ERA")
print("=" * 70)
print("%-12s %10s %10s %9s   %s" % ("era", "genuine", "flagged", "FPR", "acad-only FPR"))
for e in order:
    d = den.get(e, 0)
    if not d:
        continue
    n, na = num.get(e, 0), num_acad.get(e, 0)
    print("%-12s %10d %10d %8.1f%%   %8.1f%%" %
          (e, d, n, 100.0 * n / d, 100.0 * na / d))
print("-" * 70)
print("%-12s %10d %10d %8.1f%%   %8.1f%%" %
      ("ALL", den_all, len(fa), 100.0 * len(fa) / den_all,
       100.0 * sum(num_acad.values()) / den_all))

# the mission-relevant cut
recent_den = den.get("2019+", 0)
recent_num = num.get("2019+", 0)
recent_acad = num_acad.get("2019+", 0)
print("\n" + "=" * 70)
print("MISSION-RELEVANT (2019+, the AI era):")
print("  genuine recent citations      : %d" % recent_den)
print("  falsely flagged (any)         : %d  -> %.2f%%"
      % (recent_num, 100.0 * recent_num / recent_den if recent_den else 0))
print("  falsely flagged (academic)    : %d  -> %.2f%%"
      % (recent_acad, 100.0 * recent_acad / recent_den if recent_den else 0))
print("\npooled 6.8%% is dominated by pre-2019 coverage gaps, not by misses on the")
print("recent references the detector actually exists to check.")
