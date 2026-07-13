"""
journal_synonym_validation.py — comprehensive validation of the journal-name
authority across ALL its synonyms.

A) RECALL   : for known variants (ISO-4 abbreviations + alternate titles) from the
              OpenAlex sources data, does same_journal(variant, canonical) == True?
B) PRECISION: does a variant of journal A wrongly match a different journal B?
C) COVERAGE : of real journal names as they appear in citations, what % resolve?
"""
import glob, gzip, json, random, sys, collections
sys.path.insert(0, "/home/rwang/fake-citation-detector/scripts")
from journal_authority import same_journal, resolve
import batch_verify_years as bvy

RAW = glob.glob("/home/rwang/journal_authority/raw/**/*.gz", recursive=True)

# ---- collect journals that carry variants ----
journals = []   # (canonical, [variants], identity)
for f in RAW:
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        for line in fh:
            try: s = json.loads(line)
            except Exception: continue
            disp = s.get("display_name")
            if not disp: continue
            variants = []
            if s.get("abbreviated_title"): variants.append(s["abbreviated_title"])
            for a in (s.get("alternate_titles") or []):
                if a and a != disp: variants.append(a)
            if variants:
                ident = s.get("issn_l") or (s.get("id") or "").rsplit("/",1)[-1]
                journals.append((disp, variants, ident))
print(f"journals with >=1 known variant: {len(journals):,}", flush=True)
random.seed(0); random.shuffle(journals)

# ---- A) RECALL ----
sampleA = journals[:30000]
rec_tot = rec_ok = 0
via = collections.Counter()
for disp, variants, ident in sampleA:
    for v in variants:
        rec_tot += 1
        if same_journal(v, disp):
            rec_ok += 1
            via["match_via_resolve" if resolve(v) else "match_via_heuristic"] += 1
        else:
            via["MISS"] += 1
print(f"\n[A] RECALL on known variants (n={rec_tot:,} variant->canonical pairs)")
print(f"    correctly matched: {rec_ok:,}  ({100*rec_ok/rec_tot:.2f}%)")
print(f"    breakdown: {dict(via)}")

# ---- B) PRECISION (variant of A vs a different journal B) ----
prec_tot = prec_fp = 0
for _ in range(30000):
    a = random.choice(journals); b = random.choice(journals)
    if a[2] == b[2]:      # same journal -> skip
        continue
    va = random.choice(a[1])
    prec_tot += 1
    if same_journal(va, b[0]):   # variant of A should NOT match B's canonical
        prec_fp += 1
print(f"\n[B] PRECISION (variant of A vs different journal B, n={prec_tot:,})")
print(f"    false positives: {prec_fp:,}  ({100*prec_fp/prec_tot:.2f}%)")
print(f"    precision: {100*(prec_tot-prec_fp)/prec_tot:.2f}%")

# ---- C) COVERAGE on real citation journal names ----
V11 = "/home/rwang/cross_year_study/results_v11.jsonl"
papers = [json.loads(l) for l in open(V11) if '"ok"' in l]
random.seed(5); random.shuffle(papers)
seen = set(); resolved = 0; total = 0; heur_only = 0
for p in papers[:800]:
    refs = bvy.crossref_refs(p["doi"])
    if not refs: continue
    for r in refs:
        j = getattr(r, "journal", None)
        if not j: continue
        key = j.lower().strip()
        if key in seen: continue
        seen.add(key)
        total += 1
        if resolve(j):
            resolved += 1
    if total >= 8000:
        break
print(f"\n[C] COVERAGE on real citation journal names (distinct n={total:,})")
print(f"    resolve() to an identity: {resolved:,}  ({100*resolved/total:.1f}%)")
print(f"    (unresolved names still match via the same_journal heuristic at query time)")
print("\nDONE", flush=True)
