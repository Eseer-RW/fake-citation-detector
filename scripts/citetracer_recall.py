#!/usr/bin/env python3
"""citetracer_recall.py — recall of the UPGRADED detector on 807 chair-confirmed fabricated
citations (CiteTracer / ICLR-2026 desk rejections). Every input IS a fabrication, so
flag-rate = recall. Channels: not_found (existence), author_hijack, title_hijack."""
import json, re, sys, os, types, collections
sys.path.insert(0, "/space/rwang/fake-citation-detector/scripts")
os.environ.setdefault("OA_LOCAL_INDEX", "/space/rwang/oa_index/oa_index.db")
import batch_verify_years as bvy, arxiv_sweep as asw

data = json.load(open("/space/rwang/_speedtest/citetracer_structured.json"))
refs = []
for p in data:
    for c in p["hallucinated_citations"]:
        o = types.SimpleNamespace()
        o.raw = c.get("raw_text") or ""
        o.title = c.get("title") or None
        try: o.year = int(c.get("year")) if c.get("year") else None
        except Exception: o.year = None
        o.journal = c.get("venue") or None
        d = (c.get("doi") or "").strip().lower()
        d = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", d).rstrip(".,;)")
        o.doi = d or None
        o.volume = str(c.get("volume")) if c.get("volume") else None
        pg = str(c.get("pages") or "")
        o.first_page = re.split(r"[-–]", pg)[0].strip() if pg else None
        au = c.get("authors") or []
        first = au[0] if au else ""
        o.first_author = (first.split()[-1] if first else None)
        refs.append(o)
print("constructed %d fabricated refs" % len(refs), flush=True)

solr = asw._solr()
B = 100
rows = []
for i in range(0, len(refs), B):
    rows += bvy.verify_refs(refs[i:i+B], solr).get("per_ref") or []
    print("verified %d/%d" % (min(i+B, len(refs)), len(refs)), flush=True)

cnt = collections.Counter(); nf_reason = collections.Counter(); missed = []
for r in rows:
    if not r.get("found"):
        cnt["caught_not_found"] += 1; nf_reason[r.get("not_found_reason")] += 1
    elif r.get("fab_flag") == "author_hijack":
        cnt["caught_author_hijack"] += 1
    elif r.get("fab_flag") == "title_hijack":
        cnt["caught_title_hijack"] += 1
    else:
        cnt["MISSED"] += 1
        missed.append((r.get("method"), (r.get("raw") or "")[:80], (r.get("matched_title") or "")[:60]))
n = len(rows)
print("\n=== CITETRACER RECALL (807 confirmed fabrications) ===")
for k in ("caught_not_found", "caught_author_hijack", "caught_title_hijack", "MISSED"):
    print("  %-22s %4d  (%.1f%%)" % (k, cnt[k], 100*cnt[k]/n))
caught = n - cnt["MISSED"]
print("  TOTAL RECALL: %d/%d = %.1f%%" % (caught, n, 100*caught/n))
print("\n  not_found_reason mix on caught:", dict(nf_reason))
print("\n=== 20 MISSED (method | citation | matched-to) ===")
for m in missed[:20]: print("  [%s] %s -> %s" % m)
json.dump(rows, open("/space/rwang/_speedtest/citetracer_rows.json", "w"))
print("CITETRACER_DONE")
