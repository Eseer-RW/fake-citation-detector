"""
sample_papers_solr.py — sample OA papers by journal+year from the LOCAL OpenAlex
Solr index (galaxy:8983), avoiding the now-metered OpenAlex REST API entirely.

For each (journal, year): cursorMark-paginate over
    q=venue_id:<id>, fq=[publication_year:<y>, is_oa:true, doi:*]
collecting up to CAP DOIs, and write one manifest per journal.

Manifest line matches the v10 schema (oa_url left blank — the fast path only needs
the DOI; papers lacking a Crossref reference list are skipped downstream).

Usage:
    python3 sample_papers_solr.py --outdir manifests_v11 --cap 60000
"""
from __future__ import annotations
import argparse, json, pathlib, sys, time
import requests

SOLR = "http://galaxy:8983/solr/openalexWorks/select"
YEARS = list(range(2020, 2026))

JOURNALS = [
    # ── original 9 ──
    {"name":"PLOS ONE","id":"S202381698","field":"biology_medicine","tier":"standard"},
    {"name":"Nature Communications","id":"S64187185","field":"multidisciplinary","tier":"high"},
    {"name":"eLife","id":"S1336409049","field":"life_sciences","tier":"high"},
    {"name":"JAMA Network Open","id":"S4210217848","field":"clinical_medicine","tier":"standard"},
    {"name":"IEEE Access","id":"S2485537415","field":"cs_engineering","tier":"standard"},
    {"name":"ACS Omega","id":"S4210239500","field":"chemistry","tier":"standard"},
    {"name":"Cureus","id":"S2738950867","field":"clinical_medicine","tier":"megajournal"},
    {"name":"F1000Research","id":"S4210239046","field":"multidisciplinary","tier":"megajournal"},
    {"name":"Frontiers in Psychology","id":"S9692511","field":"psychology","tier":"megajournal"},
    # ── expansion 12 (correct Solr venue_ids) ──
    {"name":"Scientific Reports","id":"S196734849","field":"multidisciplinary","tier":"megajournal"},
    {"name":"Sensors","id":"S101949793","field":"cs_engineering","tier":"megajournal"},
    {"name":"Sustainability","id":"S10134376","field":"multidisciplinary","tier":"megajournal"},
    {"name":"IJERPH","id":"S15239247","field":"clinical_medicine","tier":"megajournal"},
    {"name":"Int J Molecular Sciences","id":"S10623703","field":"biology_medicine","tier":"megajournal"},
    {"name":"Frontiers in Immunology","id":"S2595292759","field":"biology_medicine","tier":"megajournal"},
    {"name":"Frontiers in Public Health","id":"S2595931848","field":"clinical_medicine","tier":"megajournal"},
    {"name":"Applied Sciences","id":"S4210205812","field":"cs_engineering","tier":"megajournal"},
    {"name":"Materials","id":"S4210189194","field":"cs_engineering","tier":"megajournal"},
    {"name":"PeerJ","id":"S1983995261","field":"biology_medicine","tier":"standard"},
    {"name":"RSC Advances","id":"S2481244646","field":"chemistry","tier":"standard"},
    {"name":"Frontiers in Neuroscience","id":"S115201632","field":"life_sciences","tier":"megajournal"},
]


def solr_get(params, retries=5):
    params.setdefault("wt","json"); params.setdefault("facet","false")
    for a in range(retries):
        try:
            r = requests.get(SOLR, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            if a == retries-1:
                print(f"    solr error: {e}", file=sys.stderr)
        time.sleep(2*(a+1))
    return None


def sample_cell(journal, year, cap):
    """cursorMark-paginate DOIs for one (journal, year). Returns list of dicts."""
    out = []
    cursor = "*"
    while len(out) < cap:
        data = solr_get({
            "q":    f"venue_id:{journal['id']}",
            "fq":   [f"publication_year:{year}", "is_oa:true", "doi:*"],
            "fl":   "doi,title,cited_by_count",
            "rows": "1000",
            "sort": "id asc",
            "cursorMark": cursor,
        })
        if not data:
            break
        docs = data.get("response",{}).get("docs",[])
        if not docs:
            break
        for d in docs:
            doi = (d.get("doi") or "").replace("https://doi.org/","").strip().lower()
            if not doi:
                continue
            title = d.get("title")
            if isinstance(title, list):
                title = title[0] if title else ""
            out.append({
                "journal_name":   journal["name"],
                "journal_id":     journal["id"],
                "field":          journal["field"],
                "tier":           journal["tier"],
                "year":           year,
                "doi":            doi,
                "title":          (title or "").strip(),
                "oa_url":         "",   # not needed for fast path
                "cited_by_count": d.get("cited_by_count", 0),
            })
            if len(out) >= cap:
                break
        nxt = data.get("nextCursorMark")
        if not nxt or nxt == cursor:
            break
        cursor = nxt
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="/home/rwang/cross_year_study/manifests_v11")
    ap.add_argument("--cap", type=int, default=60000, help="max papers per journal-year cell")
    ap.add_argument("--years", nargs="+", type=int, default=YEARS)
    args = ap.parse_args()

    outdir = pathlib.Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
    grand = 0
    for j in JOURNALS:
        slug = (j["name"].lower().replace(" ","_").replace("/","_")
                .replace("(","").replace(")","").replace("__","_"))
        fp = outdir / f"manifest_{slug}.jsonl"
        if fp.exists() and fp.stat().st_size > 0:
            n = sum(1 for _ in fp.open())
            print(f"[skip] {j['name']:<28} already has {n:,} papers", flush=True)
            grand += n
            continue
        jtotal = 0
        with fp.open("w") as fh:
            for y in sorted(args.years):
                rows = sample_cell(j, y, args.cap)
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
                jtotal += len(rows)
                print(f"  {j['name']:<28} {y}  {len(rows):>7,}  (journal running {jtotal:,})", flush=True)
        grand += jtotal
        print(f"[done] {j['name']:<28} {jtotal:,} papers -> {fp.name}", flush=True)

    print(f"\nGRAND TOTAL sampled: {grand:,} papers across {len(JOURNALS)} journals", flush=True)


if __name__ == "__main__":
    main()
