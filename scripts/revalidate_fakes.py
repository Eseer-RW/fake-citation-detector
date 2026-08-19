#!/usr/bin/env python3
"""
revalidate_fakes.py — re-measure fabrication recall under the CURRENT exact-match config.

WHY. The published 114/114 (100% recall) was measured in the fuzzy era, at a 0.85
similarity threshold. The pipeline is now exact-match only. A null result is only as
strong as the demonstrated sensitivity of the instrument, so that number has to be
re-established under the configuration actually in use.

METHOD. Feed eval/fake_injection/cited_sent/ (38 papers, 2,495 citations, 114 injected
fabrications) through verify_refs exactly as the sweep does. Score against _is_fake.
  recall on fakes = share of fabrications that come back NOT FOUND  (sensitivity)
  FPR on reals    = share of genuine citations that come back NOT FOUND (false alarms)

FAIRNESS NOTES
  * `_is_fake` and the presence/absence of the `doi` key are used ONLY for scoring --
    never fed to the pipeline, since key-presence itself leaks the label here.
  * DOIs are extracted from the raw citation string with the pipeline's own regex,
    because the real pipeline does that too. Skipping it would disable the DOI phase
    and make the test unrealistically easy.
"""
import json, glob, re, sys, types, collections, os

sys.path.insert(0, "/space/rwang/fake-citation-detector/scripts")
os.chdir("/space/rwang/fake-citation-detector/scripts")
os.environ.setdefault("SKIP_OPENALEX_API", "1")
os.environ.setdefault("SKIP_CROSSREF_API", "1")
import batch_verify_years as bvy
import solr_lookup
from solr_lookup import SolrLookup

solr_lookup.SOLR_TIMEOUT = 60
EVAL = "/space/rwang/fake-citation-detector/eval/fake_injection"


def _synth_biblio(title):
    """Deterministic plausible volume/first_page from the title.

    Every injected fake is MISSING volume and pages, so none can satisfy Phase 3's
    eligibility gate (year AND journal AND volume AND page/author) -- fabrications are
    structurally barred from the metadata path that a realistic hallucination WOULD
    enter. Filling them in makes fakes structurally indistinguishable from reals, which
    is the only way to measure genuine sensitivity rather than an artifact of the
    injection script's field coverage.
    """
    h = 0
    for ch in title or "":
        h = (h * 131 + ord(ch)) % 1000003
    return str(10 + h % 90), str(100 + (h // 90) % 900)


def mkref(c, synth=False):
    """Build the same shape parse_tei_refs produces, from a cited_sent entry."""
    o = types.SimpleNamespace()
    o.raw = c.get("citation") or ""
    o.title = (c.get("title") or None)
    o.journal = (c.get("journal") or None)
    o.volume = (c.get("volume") or None)
    yr = c.get("year")
    try:
        o.year = int(str(yr)[:4]) if yr else None
    except Exception:
        o.year = None
    pg = str(c.get("pages") or "")
    o.first_page = (re.split(r"[-–]", pg)[0].strip() or None) if pg else None
    if synth and not o.volume and not o.first_page:
        o.volume, o.first_page = _synth_biblio(o.title)
    auth = c.get("authors") or ""
    if isinstance(auth, list):                 # fakes store a list, reals a string.
        auth = ";".join(str(x) for x in auth)  # normalise: the TYPE itself leaks the label
    o.first_author = (auth.split(";")[0].strip() or None)
    # DOI from raw text, same as the real pipeline
    o.doi = None
    try:
        m = bvy._DOI_RE.search(o.raw)
        if m:
            o.doi = m.group(1).rstrip(".,;)").lower()
    except Exception:
        pass
    return o


gt_files = sorted(glob.glob(os.path.join(EVAL, "cited_sent", "*.json")))
solr = SolrLookup()
SYNTH = os.environ.get("SYNTH_BIBLIO") == "1"
print("condition: %s\n" % ("B) fakes given synthetic volume/pages (structurally comparable)"
                            if SYNTH else "A) as-is (fakes lack volume/pages)"))

tot = collections.Counter()
method_real = collections.Counter()
method_fake = collections.Counter()
missed_fakes = []
false_alarms = []
solr_err = collections.Counter()   # a contended run inflates FPR: track and refuse to trust it

for f in gt_files:
    cits = json.load(open(f))
    refs = [mkref(c, SYNTH) for c in cits]
    flags = [bool(c.get("_is_fake")) for c in cits]
    res = bvy.verify_refs(refs, solr)
    for _k, _v in (res.get("solr_errors") or {}).items():
        solr_err[_k] += _v
    per = {p["i"]: p for p in res.get("per_ref", [])}
    for i, (c, is_fake) in enumerate(zip(cits, flags)):
        p = per.get(i)
        if p is None:
            continue
        found = bool(p.get("found"))
        meth = p.get("method")
        if is_fake:
            tot["fake"] += 1
            method_fake[meth] += 1
            if found:
                tot["fake_missed"] += 1          # fabrication matched something = MISS
                missed_fakes.append((os.path.basename(f), c.get("title", "")[:70], meth))
            else:
                tot["fake_caught"] += 1
        else:
            tot["real"] += 1
            method_real[meth] += 1
            if found:
                tot["real_ok"] += 1
            else:
                tot["real_flagged"] += 1         # genuine citation called not-found
                if len(false_alarms) < 12:
                    false_alarms.append((os.path.basename(f), c.get("title", "")[:70],
                                         bool(p.get("nonacademic"))))
    print("  %-38s refs=%4d fakes=%2d" % (os.path.basename(f), len(cits), sum(flags)),
          flush=True)

print("\n" + "=" * 66)
print("FABRICATION DETECTION under CURRENT exact-match config")
print("=" * 66)
fk, fc = tot["fake"], tot["fake_caught"]
rl, rf = tot["real"], tot["real_flagged"]
print("injected fabrications : %d" % fk)
print("  caught (NOT_FOUND)  : %d   -> RECALL %.1f%%" % (fc, 100.0 * fc / fk if fk else 0))
print("  missed (matched)    : %d" % tot["fake_missed"])
print()
print("genuine citations     : %d" % rl)
print("  verified (FOUND)    : %d" % tot["real_ok"])
print("  flagged NOT_FOUND   : %d   -> FALSE-POSITIVE RATE %.1f%%"
      % (rf, 100.0 * rf / rl if rl else 0))
print()
print("SOLR ERRORS during eval : %s" % (dict(solr_err) or "none"))
if solr_err:
    print("  *** WARNING: failed lookups become false NOT_FOUND -- the FPR above is")
    print("  *** INFLATED and this run must NOT be compared to a clean baseline. ***")
print()
print("published (fuzzy-era) : 114/114 = 100.0%% recall")
print("this run              : %d/%d = %.1f%% recall" % (fc, fk, 100.0 * fc / fk if fk else 0))

print("\nmatch-method mix, GENUINE citations:")
for m, c in method_real.most_common():
    print("   %-16s %5d  %5.1f%%" % (m, c, 100.0 * c / rl if rl else 0))
print("\nmatch-method mix, FABRICATIONS (anything but not_found is a miss):")
for m, c in method_fake.most_common():
    print("   %-16s %5d" % (m, c))

if missed_fakes:
    print("\nFABRICATIONS THAT MATCHED SOMETHING (investigate each):")
    for fn, t, m in missed_fakes:
        print("   [%s] via %s" % (fn, m))
        print("      %s" % t)

if false_alarms:
    print("\nsample genuine citations flagged not-found (nonacad flag in brackets):")
    for fn, t, na in false_alarms:
        print("   [%s] nonacademic=%s  %s" % (fn, na, t))
