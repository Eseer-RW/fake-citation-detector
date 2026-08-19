#!/usr/bin/env python3
"""
snapshot_vs_solr.py — is the Solr index missing works that the OpenAlex snapshot has?

WHY THIS DIRECTION. Looking up our unmatched references IN the snapshot would need a
full scan of 596GB of gzip (no index) -- hours of NFS reads. Sampling works FROM the
snapshot and checking them against Solr answers the more decisive question at a tiny
fraction of the cost: if works that exist in OpenAlex are absent from the Solr index,
then some share of our "not found" is INDEX COVERAGE, not missing scholarship -- and
that share would inflate the unmatched rate we are treating as a hallucination signal.

Deliberately reads only SMALL partitions (median 24KB) and skips the 331GB base
partition, to stay light on a mount that collapsed earlier today.
"""
import gzip, json, os, random, sys, time, argparse, collections
import requests

WORKS = "/space/donghu/openAlex_data/data/works"
SOLR = "http://galaxy:8983/solr/openalexWorks/select"


def solr_has_doi(sess, doi):
    """Exact DOI lookup, same shape the pipeline uses."""
    d = (doi or "").strip().lower()
    for pre in ("https://doi.org/", "http://dx.doi.org/", "doi:"):
        if d.startswith(pre):
            d = d[len(pre):]
    if not d:
        return None
    try:
        r = sess.get(SOLR, params={"q": 'doi:"%s"' % d, "rows": 1, "fl": "id",
                                   "facet": "false", "hl": "false"}, timeout=60)
        return r.json().get("response", {}).get("numFound", 0) > 0
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=1500, help="works to sample")
    ap.add_argument("--max-part-kb", type=int, default=60000,
                    help="skip partitions bigger than this (avoids the 331GB base)")
    a = ap.parse_args()
    rng = random.Random(4242)

    parts = []
    for d in sorted(os.listdir(WORKS)):
        f = os.path.join(WORKS, d, "part_0000.gz")
        try:
            kb = os.path.getsize(f) // 1024
        except OSError:
            continue
        if 0 < kb <= a.max_part_kb:
            parts.append((d, f, kb))
    print("usable small partitions: %d (skipped %d oversized)"
          % (len(parts), 381 - len(parts)), flush=True)
    rng.shuffle(parts)

    sess = requests.Session()
    sess.mount("http://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8))

    seen = 0
    by_year = collections.defaultdict(lambda: [0, 0])   # year -> [checked, missing]
    missing_examples = []
    checked = missing = nodoi = err = 0
    t0 = time.time()

    for d, f, kb in parts:
        if checked >= a.target:
            break
        try:
            with gzip.open(f, "rt", encoding="utf-8", errors="replace") as fh:
                for ln in fh:
                    if checked >= a.target:
                        break
                    try:
                        w = json.loads(ln)
                    except Exception:
                        continue
                    seen += 1
                    doi = w.get("doi")
                    if not doi:
                        nodoi += 1
                        continue
                    hit = solr_has_doi(sess, doi)
                    if hit is None:
                        err += 1
                        continue
                    checked += 1
                    yr = w.get("publication_year")
                    by_year[yr][0] += 1
                    if not hit:
                        missing += 1
                        by_year[yr][1] += 1
                        if len(missing_examples) < 10:
                            missing_examples.append(
                                (w.get("id"), doi, yr,
                                 (w.get("title") or w.get("display_name") or "")[:70]))
        except Exception as e:
            print("  partition read failed %s: %s" % (d, type(e).__name__), flush=True)

    print("\nworks read from snapshot : %d" % seen)
    print("  had no DOI (skipped)   : %d" % nodoi)
    print("  Solr lookup errors     : %d" % err)
    print("DOI-bearing works checked: %d" % checked)
    print("MISSING from Solr index  : %d  (%.2f%%)"
          % (missing, 100.0 * missing / checked if checked else float("nan")))
    print("elapsed: %.0fs" % (time.time() - t0))

    if checked:
        print("\nby DECADE (checked / missing / rate) -- full range:")
        dec = collections.defaultdict(lambda: [0, 0])
        for yr, (c, m) in by_year.items():
            if isinstance(yr, int):
                d10 = (yr // 10) * 10
                dec[d10][0] += c; dec[d10][1] += m
        for d10 in sorted(dec):
            c, m = dec[d10]
            bar = "#" * int(40 * m / c) if c else ""
            print("  %ds  %5d  %4d  %6.2f%%  %s" % (d10, c, m, 100.0 * m / c if c else 0, bar))

    if missing_examples:
        print("\nexamples present in snapshot but absent from Solr:")
        for wid, doi, yr, t in missing_examples:
            print("  %s (%s) %s" % (wid, yr, t))
            print("      %s" % doi)


if __name__ == "__main__":
    main()
