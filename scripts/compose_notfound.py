#!/usr/bin/env python3
"""
compose_notfound.py — approximate composition of the unmatched bucket, by year.

PURPOSE: test whether the COMPOSITION of unmatched references shifts over time.
That is the fork in the road for this study:
  * composition shifts  -> that shift IS the month-to-month drift capping power
                           at ~25%, and cleaning the denominator unlocks the
                           measurement.
  * composition stable  -> drift comes from elsewhere (indexing lag, coverage),
                           and cleaning improves precision but does not change
                           what is resolvable.

IMPORTANT: these categories are REGEX HEURISTICS, not ground truth. They are
deliberately crude and will misclassify. They are here to detect a large
composition shift cheaply; they are not a substitute for labelled data, and no
published number should come from them.
"""
import json, re, sys, collections

PATH = sys.argv[1] if len(sys.argv) > 1 else "/space/rwang/_speedtest/notfound_sample.jsonl"

RE_URL = re.compile(r"https?://|www\.", re.I)
RE_PATENT = re.compile(r"\bpatent\b", re.I)
RE_TECHREP = re.compile(r"tech\.?\s*rep|technical report|white paper", re.I)
RE_THESIS = re.compile(r"\bdiss\b|dissertation|\bthesis\b|ph\.?\s?d\.?\s+thesis", re.I)
RE_ARXIVONLY = re.compile(r"arxiv[:\s]*\d{4}\.\d{4,5}", re.I)
RE_BOOK = re.compile(r"\bedition\b|\bpress\b|springer|wiley|elsevier|cambridge|oxford|"
                     r"pearson|mcgraw|academic press|\bchapter\b", re.I)
RE_STANDARD = re.compile(r"\bRFC\s*\d+|\bISO\b|\bIEEE Std\b|\bstandard\b", re.I)
# journal-abbrev-in-title-slot: short, dot-heavy, no sentence structure
RE_JABBREV = re.compile(r"^[A-Z][A-Za-z]*\.?(\s+[A-Z][A-Za-z]*\.?){0,5}$")
# hyphen-break artifact: lowercase, hyphen, space, lowercase (word split by layout)
RE_HYPHENBREAK = re.compile(r"[a-z]-\s+[a-z]")
# merged words: lowercase immediately followed by uppercase mid-token, or known glue
RE_MERGED = re.compile(r"[a-z]{2}[A-Z][a-z]{2}")


def categorise(d):
    raw = d.get("raw") or ""
    title = d.get("title") or ""
    low = raw.lower()

    if RE_URL.search(raw):
        return "web"
    if RE_PATENT.search(raw):
        return "patent"
    if RE_TECHREP.search(raw):
        return "techreport"
    if RE_THESIS.search(raw):
        return "thesis"
    if RE_STANDARD.search(raw):
        return "standard"
    if RE_BOOK.search(raw):
        return "book"
    if not title:
        if RE_ARXIVONLY.search(raw):
            return "arxiv_id_only"
        return "no_title_parsed"
    if RE_JABBREV.match(title.strip()) and len(title.split()) <= 6:
        return "titleless_journal_abbrev"
    if RE_HYPHENBREAK.search(raw) or RE_MERGED.search(title):
        return "parse_damage"
    return "article_like"


rows = [json.loads(l) for l in open(PATH)]
by_month = collections.defaultdict(collections.Counter)
overall = collections.Counter()
for d in rows:
    c = categorise(d)
    by_month[d["month"]][c] += 1
    overall[c] += 1

months = sorted(by_month)
cats = [c for c, _ in overall.most_common()]

print("unmatched references sampled: %d   months: %s" % (len(rows), ", ".join(months)))
print("\n(HEURISTIC categories -- diagnostic only, not ground truth)\n")
w = max(len(c) for c in cats) + 2
print("%-*s %8s   %s" % (w, "category", "overall", "  ".join("%7s" % m for m in months)))
for c in cats:
    tot = overall[c]
    cells = []
    for m in months:
        n = by_month[m][c]
        den = sum(by_month[m].values())
        cells.append("%6.1f%%" % (100.0 * n / den if den else 0))
    print("%-*s %7.1f%%   %s" % (w, c, 100.0 * tot / len(rows), "  ".join(cells)))

print("\n--- article_like share by month (the denominator that would survive cleaning) ---")
for m in months:
    den = sum(by_month[m].values())
    al = by_month[m]["article_like"]
    print("  %s  %5.1f%%  (%d of %d)" % (m, 100.0 * al / den if den else 0, al, den))

first, last = months[0], months[-1]
d0 = sum(by_month[first].values()); d1 = sum(by_month[last].values())
a0 = 100.0 * by_month[first]["article_like"] / d0 if d0 else 0
a1 = 100.0 * by_month[last]["article_like"] / d1 if d1 else 0
print("\nshift in article_like share, %s -> %s: %+.1f pp" % (first, last, a1 - a0))
print("If this shift is large, composition drift is real and cleaning the")
print("denominator should remove a meaningful part of the 25%% power ceiling.")
