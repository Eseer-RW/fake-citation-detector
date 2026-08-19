#!/usr/bin/env python3
"""
fpr_probe.py — for every false alarm, decide WHY it failed. The distinction that
matters is:

  QUERY BUG   the work IS in an index we already have, and we failed to ask correctly
              -> fixable in the matcher, cheap, no new dependency
  COVERAGE    the work is genuinely absent from both indexes
              -> needs a third source; no amount of matcher work recovers it
  NOT-ARTICLE the reference is a book/web/thesis and should never have been in the
              fabrication denominator at all -> filter work, not matcher work

METHOD. Each reference gets probed against the indexes with progressively looser
constraints, in a fixed order, and is attributed to the FIRST probe that hits:

  1. title + year          what the pipeline already tries -> should be empty by construction
  2. title, NO year        year filter is the bug (preprint/published drift, cited-year typo)
  3. repaired title        ligature/mojibake/author-leak damage in the title string
  4. title prefix          truncated title (line-break loss) -- match on first 8 words
  5. journal+vol+page      metadata route, no title at all
  6. nothing hits          coverage gap, or the title is simply wrong

NOTE ON HONESTY OF PROBE 4. A prefix match is looser than the boss's exact-match rule
and is used HERE ONLY AS A DIAGNOSTIC, to attribute a cause. Nothing in this script
changes the matcher. Whether a prefix rule is acceptable in production is a separate
decision, and the counts below are what that decision should be based on.
"""
import json, sys, os, re, collections

sys.path.insert(0, "/space/rwang/fake-citation-detector/scripts")
os.chdir("/space/rwang/fake-citation-detector/scripts")
os.environ.setdefault("SKIP_OPENALEX_API", "1")
os.environ.setdefault("SKIP_CROSSREF_API", "1")

import solr_lookup
solr_lookup.SOLR_TIMEOUT = 60
from solr_lookup import SolrLookup
import integrated_lookup as IL

IN = sys.argv[1] if len(sys.argv) > 1 else "/space/rwang/_speedtest/fpr_false_alarms.jsonl"
rows = [json.loads(l) for l in open(IN)]

# drop rows from papers where Solr errored: a timeout is not a false positive
dirty = [r for r in rows if r.get("paper_had_solr_err")]
clean = [r for r in rows if not r.get("paper_had_solr_err")]
print("false alarms dumped : %d" % len(rows))
if dirty:
    print("  excluded (solr err): %d  -- contended, cause not attributable" % len(dirty))
print("  analysed           : %d\n" % len(clean))

solr = SolrLookup()
lk = IL.IntegratedLookup(solr) if hasattr(IL, "IntegratedLookup") else None
if lk is None:
    for _n in dir(IL):
        if _n.lower().endswith("lookup") and _n != "SolrLookup":
            try:
                lk = getattr(IL, _n)(solr); break
            except Exception:
                pass
if lk is None:
    sys.exit("could not construct the integrated lookup object")

try:
    from text_repair import title_repair_variants
except Exception:
    title_repair_variants = lambda t: []

_MOJI = re.compile(r"[±·¡¿Ã¢â€™Ââ]|�")


def _hit(fn):
    try:
        r = fn()
        return bool(r and getattr(r, "found", False))
    except Exception:
        return False


CAUSES = collections.Counter()
EX = collections.defaultdict(list)

for r in clean:
    t = (r.get("title") or "").strip()
    yr, jr = r.get("year"), r.get("journal")
    vol, pg = r.get("volume"), r.get("first_page")
    au = r.get("first_author")

    if r.get("nonacademic"):
        cause = "already-flagged-nonacademic"
    elif not t:
        # no title at all -> metadata route is the only one available
        if vol and pg and jr and _hit(lambda: lk.by_metadata(journal=jr, year=yr,
                                                            volume=vol, first_page=pg)
                                     if hasattr(lk, "by_metadata") else None):
            cause = "2-metadata-hits-but-pipeline-missed"
        else:
            cause = "6-no-title-and-metadata-absent"
    else:
        if _hit(lambda: lk.by_title_exact(t, year=yr, journal=jr, author=au)):
            cause = "0-RETRY-HITS (nondeterministic / was contention)"
        elif _hit(lambda: lk.by_title_exact(t, year=None, journal=jr, author=au)):
            cause = "1-YEAR-FILTER-is-the-bug"
        else:
            got = False
            for v in (title_repair_variants(t) or []):
                if v != t and _hit(lambda v=v: lk.by_title_exact(v, year=None)):
                    cause = "3-TITLE-DAMAGE (repair variant hits)"; got = True; break
            if not got:
                w = t.split()
                if len(w) >= 9 and _hit(lambda: lk.by_title_exact(" ".join(w[:8]), year=None)):
                    cause = "4-TRUNCATION (prefix hits)"
                elif (vol and pg and jr and hasattr(lk, "by_metadata")
                      and _hit(lambda: lk.by_metadata(journal=jr, year=yr,
                                                      volume=vol, first_page=pg))):
                    cause = "5-METADATA-route-would-hit"
                else:
                    cause = "6-COVERAGE (absent from both indexes)"

    CAUSES[cause] += 1
    if len(EX[cause]) < 5:
        EX[cause].append((t[:66] or "(no title)", jr or "-", yr, bool(_MOJI.search(r.get("raw") or ""))))

n = sum(CAUSES.values()) or 1
print("=" * 74)
print("WHY EACH FALSE ALARM FAILED")
print("=" * 74)
for c, k in sorted(CAUSES.items(), key=lambda kv: -kv[1]):
    print("%-46s %4d  %5.1f%%" % (c, k, 100.0 * k / n))

print("\n" + "=" * 74)
print("EXAMPLES BY CAUSE  (moji = mojibake present in raw)")
print("=" * 74)
for c in sorted(EX, key=lambda c: -CAUSES[c]):
    print("\n--- %s ---" % c)
    for t, j, y, mo in EX[c]:
        print("   %-66s" % t)
        print("      journal=%-34s year=%s%s" % (str(j)[:34], y, "  [moji]" if mo else ""))

fixable = sum(v for k, v in CAUSES.items() if k[0] in "012345")
print("\n" + "=" * 74)
print("addressable without a new data source : %d of %d  (%.0f%%)"
      % (fixable, n, 100.0 * fixable / n))
print("genuine coverage gap                  : %d  (%.0f%%)"
      % (CAUSES.get("6-COVERAGE (absent from both indexes)", 0)
         + CAUSES.get("6-no-title-and-metadata-absent", 0),
         100.0 * (CAUSES.get("6-COVERAGE (absent from both indexes)", 0)
                  + CAUSES.get("6-no-title-and-metadata-absent", 0)) / n))
