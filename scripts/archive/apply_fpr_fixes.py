#!/usr/bin/env python3
"""
apply_fpr_fixes.py — two surgical fixes for the false-positive rate, each verified to
apply the expected number of times or the script aborts without writing.

FIX 1 (integrated_lookup.by_title_exact): add a final, unbounded-year fallback. Every
title attempt above is gated by publication_year:[year-1, year+1]; a real paper cited
with a year off by >=2 (preprint/published drift or a citation-year error) misses even
though its EXACT title is indexed. The fallback retries the exact normalized-key match
with NO year gate. Stays EXACT (normalize_title_key equality; no fuzzy threshold) --
year becomes a disambiguator, never a gate. Fabrications have hallucinated titles that
match no real key at any year, so recall is unaffected (gate re-verifies 114/114).

FIX 2 (batch_verify_years.solr_by_metadata, Phase 2.6): the author filter is built as
   fq = author_names:{author}   -- UNQUOTED.
For a two-token author Solr parses that as author_names:First OR <default>:Last, matching
~460k docs; behind the numFound==1 uniqueness guard an over-broad filter yields >1 hit
and REJECTS the genuine paper. Measured: 33% acceptance unquoted vs 77.5% on a surname
token, on 200 works all provably in the index. Switch to the surname token, mirroring the
fix already in integrated_lookup.oa_by_metadata. Precision is still held by numFound==1.
"""
import re, shutil, sys, os

IL = "/space/rwang/fake-citation-detector/scripts/integrated_lookup.py"
BV = "/space/rwang/fake-citation-detector/scripts/batch_verify_years.py"

# ---- FIX 1 ----------------------------------------------------------------
il = open(IL).read()
il_anchor = """        if not _repaired:                         # retry with cleaned title variants
            from text_repair import title_repair_variants  # ligature / author-strip / greek
            for _variant in title_repair_variants(title):
                _r = self.by_title_exact(_variant, year=year, journal=journal,
                                         author=author, _repaired=True)
                if _r.found:
                    return _r
        return LookupResult(found=False, method=MatchMethod.NOT_FOUND)"""

il_replace = """        if not _repaired:                         # retry with cleaned title variants
            from text_repair import title_repair_variants  # ligature / author-strip / greek
            for _variant in title_repair_variants(title):
                _r = self.by_title_exact(_variant, year=year, journal=journal,
                                         author=author, _repaired=True)
                if _r.found:
                    return _r
        # UNBOUNDED-YEAR FALLBACK. Every attempt above is gated by a +-1-year window, so a
        # real paper whose cited year is off by >=2 (preprint->published drift, or a plain
        # citation-year error) is reported not-found even though its EXACT title sits in the
        # index. Retry the exact normalized-key match with NO year gate. This stays EXACT
        # (title_norm / normalize_title_key equality -- no fuzzy similarity anywhere); year
        # is only ever a disambiguator, never a gate. A real paper cited with the wrong year
        # is FOUND (correct: it is not a fabrication); the year discrepancy is recorded
        # downstream as a mismatch. Injected fabrications carry hallucinated titles that
        # match no real key at ANY year, so recall is unchanged (gate: 114/114 held).
        if year is not None:
            hit = self.xr.by_title_exact(title, year=None, journal=journal, author=author)
            if hit and hit.found:
                return hit
            d = self._oa_title_phrase(title, year=None)
            if d:
                t = d.get("title")
                t = t[0] if isinstance(t, list) and t else t
                rec = {"doi": (d.get("doi") or "").replace("https://doi.org/", "") or None,
                       "openalex_id": d.get("id"), "title": t,
                       "year": d.get("publication_year"),
                       "publication_year": d.get("publication_year"),
                       "journal": d.get("venue_name"), "venue_name": d.get("venue_name"),
                       "author_names": d.get("author_names") or []}
                return LookupResult(found=True, method=MatchMethod.TITLE_ONLY,
                                    record=rec, confidence=1.0)
        return LookupResult(found=False, method=MatchMethod.NOT_FOUND)"""

n1 = il.count(il_anchor)
if n1 != 1:
    sys.exit("FIX1 ABORT: expected exactly 1 anchor in integrated_lookup.py, found %d" % n1)

# ---- FIX 2 ----------------------------------------------------------------
bv = open(BV).read()
bv_anchor = """        vq = " OR ".join(vids)
        params = {
            "q":     f"venue_id:({vq})",
            "fq":    [f"publication_year:{int(year)}",
                      f'volume:"{_esc(volume)}"',
                      f"author_names:{_esc(author)}"],"""

bv_replace = """        vq = " OR ".join(vids)
        # Author filter: SURNAME token, not the full cited string. An UNQUOTED multi-token
        # value (author_names:{author}) parses as author_names:First OR <default>:Last and
        # matches ~460k docs; behind the numFound==1 guard that yields >1 hit and REJECTS
        # the genuine paper (measured: 33% vs 77.5% acceptance). The surname token restores
        # recall; the numFound==1 uniqueness guard below still holds precision. Mirrors
        # integrated_lookup.oa_by_metadata.
        _atoks = [t for t in re.split(r"[^A-Za-z]+", str(author)) if len(t) >= 3]
        _surname = max(_atoks, key=len) if _atoks else str(author)
        params = {
            "q":     f"venue_id:({vq})",
            "fq":    [f"publication_year:{int(year)}",
                      f'volume:"{_esc(volume)}"',
                      f'author_names:{_esc(_surname)}'],"""

n2 = bv.count(bv_anchor)
if n2 < 1:
    sys.exit("FIX2 ABORT: author-filter anchor not found in batch_verify_years.py")
# there are two identical solr_by_metadata defs; patch BOTH so behavior is guaranteed
# regardless of which one Python binds (it binds the last, but be explicit).

# ---- write ---------------------------------------------------------------
shutil.copy(IL, IL + ".bak_unboundedyear")
shutil.copy(BV, BV + ".bak_authorquote")
open(IL, "w").write(il.replace(il_anchor, il_replace, 1))
open(BV, "w").write(bv.replace(bv_anchor, bv_replace))
print("FIX1 integrated_lookup.py : 1 site patched (backup .bak_unboundedyear)")
print("FIX2 batch_verify_years.py: %d site(s) patched (backup .bak_authorquote)" % n2)

# ---- compile check -------------------------------------------------------
import py_compile
for f in (IL, BV):
    py_compile.compile(f, doraise=True)
print("both files compile OK")
