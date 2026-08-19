#!/usr/bin/env python3
"""
doi_fabrication.py — can the pipeline catch DOI-based fabrication?

The headline bound is measured on DOI-BEARING references, but the existing eval
set's 114 fabrications carry NO DOIs. So sensitivity has been demonstrated for
fabricated TITLES and merely assumed for fabricated DOIs. Two conditions close it:

  C) INVENTED DOI  -- real registrant prefix, fabricated suffix, on a fabricated
     title. Expected: fails to resolve -> NOT_FOUND. Easy case; confirms the DOI
     path does not hallucinate a match.

  D) HIJACKED DOI  -- a REAL DOI (borrowed from a genuine citation in the same
     corpus) attached to a FABRICATED title/authors. This is the dangerous class
     and the one the unmatched-rate analysis is BLIND to: it resolves by DOI, so
     it is counted FOUND and never enters not_found at all. The only thing that
     can catch it is validate_metadata -> FOUND_MISMATCH.
     If D is not caught, the bound on DOI-bearing citations has a real hole.
"""
import json, glob, re, sys, types, collections, os, random

sys.path.insert(0, "/space/rwang/fake-citation-detector/scripts")
os.chdir("/space/rwang/fake-citation-detector/scripts")
os.environ.setdefault("SKIP_OPENALEX_API", "1")
os.environ.setdefault("SKIP_CROSSREF_API", "1")
import batch_verify_years as bvy
import solr_lookup
from solr_lookup import SolrLookup

solr_lookup.SOLR_TIMEOUT = 60
EVAL = "/space/rwang/fake-citation-detector/eval/fake_injection"
COND = os.environ.get("DOI_COND", "C").upper()
rng = random.Random(31337)

# Real registrant prefixes, so the fabricated DOI is syntactically plausible.
PREFIXES = ["10.1038", "10.1016", "10.1103", "10.1093", "10.1073",
            "10.1126", "10.1021", "10.1109", "10.1001", "10.1371"]


def invented_doi(title):
    h = 0
    for ch in title or "":
        h = (h * 131 + ord(ch)) % 99999989
    p = PREFIXES[h % len(PREFIXES)]
    return "%s/%s%d" % (p, "abcdefgh"[h % 8], 100000 + h % 899999)


def base_ref(c):
    o = types.SimpleNamespace()
    o.raw = c.get("citation") or ""
    o.title = c.get("title") or None
    o.journal = c.get("journal") or None
    o.volume = c.get("volume") or None
    yr = c.get("year")
    try:
        o.year = int(str(yr)[:4]) if yr else None
    except Exception:
        o.year = None
    pg = str(c.get("pages") or "")
    o.first_page = (re.split(r"[-–]", pg)[0].strip() or None) if pg else None
    auth = c.get("authors") or ""
    if isinstance(auth, list):
        auth = ";".join(str(x) for x in auth)
    o.first_author = auth.split(";")[0].strip() or None
    o.doi = None
    try:
        m = bvy._DOI_RE.search(o.raw)
        if m:
            o.doi = m.group(1).rstrip(".,;)").lower()
    except Exception:
        pass
    return o


# ---- harvest real DOIs from GENUINE citations, for condition D ----
files = sorted(glob.glob(os.path.join(EVAL, "cited_sent", "*.json")))
real_dois = []
for f in files:
    for c in json.load(open(f)):
        if c.get("_is_fake"):
            continue
        r = base_ref(c)
        if r.doi:
            real_dois.append(r.doi)
print("real DOIs harvested from genuine citations: %d" % len(real_dois))
if COND == "D" and not real_dois:
    sys.exit("condition D needs real DOIs; none harvested")

solr = SolrLookup()
tot = collections.Counter()
meth = collections.Counter()
examples = []

for f in files:
    cits = json.load(open(f))
    refs = []
    flags = []
    for c in cits:
        r = base_ref(c)
        fake = bool(c.get("_is_fake"))
        if fake:
            if COND == "C":
                r.doi = invented_doi(r.title)           # plausible but nonexistent
            else:
                r.doi = rng.choice(real_dois)           # REAL doi, fabricated title
        refs.append(r)
        flags.append(fake)
    res = bvy.verify_refs(refs, solr)
    per = {p["i"]: p for p in res.get("per_ref", [])}
    # mismatch issues are reported per matched ref; rebuild them for the fakes
    mm = {}
    for i, r in enumerate(refs):
        p = per.get(i)
        if p and p.get("found"):
            rec = None
            try:
                rec = solr.by_doi(r.doi).record if r.doi else None
            except Exception:
                rec = None
            if rec:
                mm[i] = bvy.validate_metadata(r, rec)
    for i, fake in enumerate(flags):
        p = per.get(i)
        if p is None or not fake:
            continue
        tot["fake"] += 1
        meth[p.get("method")] += 1
        found = bool(p.get("found"))
        issues = mm.get(i) or []
        if not found:
            tot["caught_notfound"] += 1
        elif issues:
            tot["caught_mismatch"] += 1
        else:
            tot["MISSED_silent"] += 1
            if len(examples) < 8:
                examples.append((refs[i].doi, (refs[i].title or "")[:62], p.get("method")))

print("\n" + "=" * 68)
print("CONDITION %s — %s" % (COND, "invented DOI" if COND == "C"
                             else "REAL DOI + fabricated title (hijacked)"))
print("=" * 68)
fk = tot["fake"]
print("fabrications tested        : %d" % fk)
print("  caught as NOT_FOUND      : %d" % tot["caught_notfound"])
print("  caught as FOUND_MISMATCH : %d" % tot["caught_mismatch"])
print("  SILENTLY ACCEPTED        : %d   <-- invisible to the unmatched-rate analysis"
      % tot["MISSED_silent"])
det = tot["caught_notfound"] + tot["caught_mismatch"]
print("\ntotal detection rate       : %d/%d = %.1f%%" % (det, fk, 100.0 * det / fk if fk else 0))
print("method mix:", dict(meth))
if examples:
    print("\nSILENTLY ACCEPTED examples (fabricated title resolved, no mismatch raised):")
    for d, t, m in examples:
        print("   doi=%s via %s" % (d, m))
        print("      %s" % t)
