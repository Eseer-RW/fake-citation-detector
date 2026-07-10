#!/usr/bin/env python3
"""
build_faiss_index.py — Build a FAISS IVFPQ index from OpenAlex titles in Solr.

Architecture
------------
  Index type : IndexIVFPQ  (nlist=8192, M=48, nbits=8)
  Metric     : METRIC_INNER_PRODUCT  (= cosine sim for L2-normalised vecs)
  Model      : all-MiniLM-L6-v2 (384-dim, ~80 MB)
  Corpus     : papers with cited_by_count >= MIN_CITES (default 10, ~32 M)

Output files in --outdir:
  titles.index   — FAISS binary index   (~1.5 GB)
  meta.db        — SQLite: pos → wid, doi, title, year

Resumable: each step checks for its output before running.

Usage:
  python3 build_faiss_index.py [--min-citations 10] \\
      [--outdir /home/rwang/fake-citation-detector/index] \\
      [--workers 64] [--train-size 1000000] [--chunk 200000]
"""

from __future__ import annotations
import argparse
import json
import logging
import os
import pathlib
import sqlite3
import time
import urllib.parse
import urllib.request

import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

# ── Defaults ────────────────────────────────────────────────────────────────────
SOLR_URL    = "http://galaxy:8983/solr/openalexWorks"
MODEL_NAME  = "all-MiniLM-L6-v2"
DIM         = 384
NLIST       = 8192    # IVF clusters; ~3900 vecs/cluster at 32 M
M_PQ        = 48      # PQ sub-vectors  (384 / 48 = 8 dims each)
NBITS       = 8       # 256 centroids per sub-quantizer
NPROBE      = 128     # clusters probed at query time
SOLR_BATCH  = 50_000  # docs per Solr cursor page


# ── Arg parsing ──────────────────────────────────────────────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--min-citations", type=int, default=10,
                   help="Minimum cited_by_count to include (default 10)")
    p.add_argument("--outdir", default="/home/rwang/fake-citation-detector/index",
                   help="Output directory for index and metadata")
    p.add_argument("--workers", type=int, default=64,
                   help="CPU workers for sentence-transformers encoding")
    p.add_argument("--train-size", type=int, default=1_000_000,
                   help="Training sample size for FAISS IVF clustering")
    p.add_argument("--chunk", type=int, default=200_000,
                   help="Titles per encoding chunk (controls peak RAM)")
    p.add_argument("--solr", default=SOLR_URL)
    return p.parse_args()


# ── Step 1: Solr export → SQLite ─────────────────────────────────────────────────

def _solr_count(solr: str, q: str) -> int:
    url = f"{solr}/select?" + urllib.parse.urlencode(
        {"q": q, "rows": 0, "wt": "json"})
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)["response"]["numFound"]


def _solr_scroll(solr: str, q: str):
    """Yield Solr docs via cursor pagination (avoids deep-pagination OOM)."""
    params_base = {
        "q":    q,
        "fl":   "id,title,doi,publication_year",
        "rows": SOLR_BATCH,
        "sort": "id asc",
        "wt":   "json",
    }
    cursor = "*"
    while True:
        url = f"{solr}/select?" + urllib.parse.urlencode(
            {**params_base, "cursorMark": cursor})
        with urllib.request.urlopen(url, timeout=60) as r:
            data = json.load(r)
        docs = data["response"]["docs"]
        if not docs:
            break
        yield from docs
        next_cursor = data.get("nextCursorMark", cursor)
        if next_cursor == cursor:
            break
        cursor = next_cursor


def export_to_db(args: argparse.Namespace, db_path: pathlib.Path) -> int:
    """
    Stream Solr → SQLite.  Returns number of rows inserted (skips if DB exists).
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            pos   INTEGER PRIMARY KEY,
            wid   INTEGER,
            doi   TEXT,
            title TEXT NOT NULL,
            year  INTEGER
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wid ON meta(wid)")
    conn.commit()

    existing = conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0]
    if existing > 0:
        log.info(f"  meta.db already has {existing:,} rows — skipping Solr export")
        conn.close()
        return existing

    q = (f"cited_by_count:[{args.min_citations} TO *] "
         f"AND title:[* TO *]")
    total_est = _solr_count(args.solr, q)
    log.info(f"  estimated docs: {total_est:,}")

    pos = 0
    buf: list = []
    t0 = time.time()

    for doc in _solr_scroll(args.solr, q):
        raw_id = doc.get("id", "")
        stripped = raw_id.lstrip("Ww")
        wid  = int(stripped) if stripped.isdigit() else 0
        raw_title = doc.get("title") or ""
        title = (raw_title[0] if isinstance(raw_title, list) else raw_title).strip()
        if not title:
            continue
        raw_doi = doc.get("doi"); doi = (raw_doi[0] if isinstance(raw_doi, list) else raw_doi) or None
        year = doc.get("publication_year") or None
        buf.append((pos, wid, doi, title, year))
        pos += 1

        if len(buf) >= 50_000:
            conn.executemany("INSERT OR IGNORE INTO meta VALUES (?,?,?,?,?)", buf)
            conn.commit()
            buf.clear()
            if pos % 500_000 == 0:
                rate = pos / (time.time() - t0)
                log.info(f"  exported {pos:,}  ({rate:.0f}/s)")

    if buf:
        conn.executemany("INSERT OR IGNORE INTO meta VALUES (?,?,?,?,?)", buf)
        conn.commit()

    conn.close()
    log.info(f"  export done: {pos:,} rows in {(time.time()-t0)/60:.1f} min")
    return pos


# ── Step 2 + 3: Encode chunks → train → add incrementally ────────────────────────

def _encode_chunk(titles: list[str], pool, batch_size: int = 512) -> np.ndarray:
    """Encode titles using a pre-started multi-process pool; returns L2-normalised float32."""
    model_stub = pool["model"]  # SentenceTransformer instance (main proc)
    embs = model_stub.encode_multi_process(
        titles, pool, batch_size=batch_size, show_progress_bar=False
    )
    embs = embs.astype(np.float32)
    faiss.normalize_L2(embs)
    return embs


def build_index(args: argparse.Namespace, db_path: pathlib.Path,
                index_path: pathlib.Path) -> faiss.Index:
    """
    Train + build FAISS IVFPQ index by streaming chunks from SQLite.
    Skips if index already exists.
    """
    if index_path.exists():
        log.info(f"  index already at {index_path} — loading")
        index = faiss.read_index(str(index_path))
        index.nprobe = NPROBE
        return index

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    total = conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0]
    log.info(f"  total rows to encode: {total:,}")

    # ── Load model + start pool ───────────────────────────────────────────────
    log.info(f"  loading model '{MODEL_NAME}'")
    model = SentenceTransformer(MODEL_NAME)
    n_workers = min(args.workers, os.cpu_count() or 8)
    log.info(f"  starting {n_workers}-worker encoding pool")
    pool = model.start_multi_process_pool(target_devices=["cpu"] * n_workers)
    pool["model"] = model  # keep ref for encode_multi_process calls

    # ── Phase A: encode training sample ──────────────────────────────────────
    train_n = min(args.train_size, total)
    log.info(f"  [train] sampling {train_n:,} titles for IVF clustering")
    # Use ORDER BY RANDOM() on a small fraction to avoid full-table scan
    sample_rows = conn.execute(
        "SELECT title FROM meta ORDER BY RANDOM() LIMIT ?", (train_n,)
    ).fetchall()
    sample_titles = [r["title"] for r in sample_rows]

    log.info(f"  [train] encoding {len(sample_titles):,} samples")
    t0 = time.time()
    train_embs = model.encode_multi_process(
        sample_titles, pool, batch_size=512, show_progress_bar=True
    )
    train_embs = train_embs.astype(np.float32)
    faiss.normalize_L2(train_embs)
    log.info(f"  [train] encoded in {time.time()-t0:.1f}s")

    # ── Build and train index ─────────────────────────────────────────────────
    faiss.omp_set_num_threads(os.cpu_count() or 8)
    quantizer = faiss.IndexFlatIP(DIM)
    index = faiss.IndexIVFPQ(
        quantizer, DIM, NLIST, M_PQ, NBITS, faiss.METRIC_INNER_PRODUCT
    )
    log.info(f"  [train] training IVF (nlist={NLIST})…")
    t0 = time.time()
    index.train(train_embs)
    log.info(f"  [train] done in {time.time()-t0:.1f}s")
    del train_embs, sample_titles  # free memory before streaming

    # ── Phase B: stream all rows → encode → add ───────────────────────────────
    chunk_size = args.chunk
    added = 0
    t0 = time.time()
    log.info(f"  [add] streaming {total:,} titles in chunks of {chunk_size:,}")

    for start in range(0, total, chunk_size):
        rows = conn.execute(
            "SELECT title FROM meta WHERE pos >= ? AND pos < ? ORDER BY pos",
            (start, start + chunk_size)
        ).fetchall()
        if not rows:
            break
        titles = [r["title"] for r in rows]

        embs = model.encode_multi_process(
            titles, pool, batch_size=512, show_progress_bar=False
        )
        embs = embs.astype(np.float32)
        faiss.normalize_L2(embs)
        index.add(embs)
        added += len(titles)

        elapsed = time.time() - t0
        rate = added / elapsed
        eta_min = (total - added) / rate / 60 if rate > 0 else 0
        log.info(f"  [add] {added:>12,} / {total:,}  "
                 f"({rate:.0f}/s, ETA {eta_min:.1f} min)")

    model.stop_multi_process_pool(pool)
    conn.close()

    log.info(f"  [add] finished {added:,} vectors in "
             f"{(time.time()-t0)/60:.1f} min")

    # ── Save ──────────────────────────────────────────────────────────────────
    index.nprobe = NPROBE
    faiss.write_index(index, str(index_path))
    size_gb = index_path.stat().st_size / 1e9
    log.info(f"  saved → {index_path}  ({size_gb:.2f} GB)")
    return index


# ── Main ─────────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    outdir = pathlib.Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    db_path    = outdir / "meta.db"
    index_path = outdir / "titles.index"

    t_total = time.time()

    log.info(f"=== Step 1: Solr export (cited >= {args.min_citations}) ===")
    n = export_to_db(args, db_path)
    log.info(f"  {n:,} papers in meta.db\n")

    log.info("=== Step 2+3: Encode + build FAISS IVFPQ index ===")
    index = build_index(args, db_path, index_path)
    log.info(f"  index.ntotal = {index.ntotal:,}\n")

    elapsed = (time.time() - t_total) / 60
    log.info(f"=== Done in {elapsed:.1f} min ===")
    log.info(f"  {outdir}/titles.index  ({index_path.stat().st_size/1e9:.1f} GB)")
    log.info(f"  {outdir}/meta.db       ({db_path.stat().st_size/1e9:.2f} GB)")
    log.info(f"\nTest with:")
    log.info(f"  python3 -c \""
             f"import sys; sys.path.insert(0,'{outdir}/..');"
             f" from vector_lookup import VectorLookup; "
             f"vl=VectorLookup(); "
             f"print(vl.recommend('A novel coronavirus from patients with pneumonia', year=2020))\"")


if __name__ == "__main__":
    log = logging.getLogger("build")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    main()
