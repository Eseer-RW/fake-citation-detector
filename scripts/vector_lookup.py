"""
vector_lookup.py — semantic vector re-ranking for NOT_FOUND citations.

Two modes (auto-selected at startup):

  FAISS mode  (fast)
    Requires: index/titles.index + index/meta.db  (built by build_faiss_index.py)
    Per-citation: encode 1 title (~5 ms) + FAISS IVFPQ search (~20 ms) = ~25 ms
    vs. old path: Solr fetch (40 docs) + encode 41 titles = ~4-5 s

  Solr-rerank mode  (fallback, original behaviour)
    No pre-built index needed.
    Fires a broad edismax Solr query, embeds query + 40 candidates, re-ranks.
    Used automatically when titles.index does not exist.

Configuration
-------------
Set INDEX_DIR to the directory containing titles.index and meta.db.
Default: <this script's parent>/index/

Usage
-----
    from vector_lookup import VectorLookup

    vl = VectorLookup()                             # loads model + index once
    result = vl.by_title("A novel coronavirus…", year=2020)
    recs   = vl.recommend("A novel coronavirus…", year=2020, n=3)
    recs   = vl.batch_recommend([title1, title2], years=[2020, 2021])

Batch API
---------
    vl.batch_recommend(titles, years=None, n=3)
    → list[list[dict]]   (one list of recs per input title)

    Encodes all titles in a single model.encode() call and does one FAISS
    search for all queries — amortises overhead when multiple NOT_FOUND
    citations are processed together.
"""

from __future__ import annotations

import pathlib
import re
import sqlite3
import sys
import time
import types
from typing import Optional

import numpy as np
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_HERE = pathlib.Path(__file__).parent
INDEX_DIR        = _HERE.parent / "index"
INDEX_PATH       = INDEX_DIR / "titles.index"
META_DB_PATH     = INDEX_DIR / "meta.db"

SOLR_URL         = "http://galaxy:8983/solr/openalexWorks/select"
MODEL_NAME       = "all-MiniLM-L6-v2"

VECTOR_THRESHOLD = 0.82   # minimum cosine similarity to accept as a match
YEAR_TOLERANCE   = 2      # max |year_cited − year_db|
BROAD_CANDIDATES = 40     # Solr candidates (Solr-rerank fallback only)
NPROBE           = 128    # IVF clusters to probe at query time (FAISS mode)
TOP_K            = 10     # FAISS candidates to fetch before year-filtering


# ---------------------------------------------------------------------------
# Lazy-loaded globals
# ---------------------------------------------------------------------------
_model       = None   # SentenceTransformer
_faiss_index = None   # faiss.Index   (None if not built yet)
_meta_conn   = None   # sqlite3.Connection to meta.db


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def _get_faiss():
    """Return (index, conn) if the pre-built index exists, else (None, None)."""
    global _faiss_index, _meta_conn
    if _faiss_index is not None:
        return _faiss_index, _meta_conn

    if not INDEX_PATH.exists() or not META_DB_PATH.exists():
        return None, None

    import faiss
    t0 = time.perf_counter()
    _faiss_index = faiss.read_index(str(INDEX_PATH))
    _faiss_index.nprobe = NPROBE
    _meta_conn = sqlite3.connect(str(META_DB_PATH), check_same_thread=False)
    elapsed = time.perf_counter() - t0
    print(f"[vector_lookup] FAISS index loaded: {_faiss_index.ntotal:,} vectors "
          f"in {elapsed:.1f}s", file=sys.stderr)
    return _faiss_index, _meta_conn


# ---------------------------------------------------------------------------
# FAISS-mode helpers
# ---------------------------------------------------------------------------

def _embed(texts: list[str]) -> np.ndarray:
    """Embed and L2-normalise a list of strings; returns (n, 384) float32."""
    import faiss
    model = _get_model()
    embs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    embs = embs.astype(np.float32)
    faiss.normalize_L2(embs)
    return embs


def _faiss_search(queries: np.ndarray, k: int = TOP_K):
    """
    Search the FAISS index.
    Returns (distances, indices) arrays, shape (n_queries, k).
    Distances are cosine similarities (0–1 for normalised vectors with IP metric).
    """
    index, _ = _get_faiss()
    D, I = index.search(queries, k)
    return D, I


def _meta_lookup(pos: int) -> dict | None:
    """Fetch a single row from meta.db by FAISS position (= SQLite pos column)."""
    _, conn = _get_faiss()
    row = conn.execute(
        "SELECT wid, doi, title, year FROM meta WHERE pos = ?", (pos,)
    ).fetchone()
    if row is None:
        return None
    wid, doi, title, year = row
    return {
        "openalex_id": f"W{wid}" if wid else None,
        "doi":         doi,
        "title":       title,
        "year":        year,
    }


def _faiss_recommend(titles: list[str],
                     years: list[int | None] | None = None,
                     n: int = 3,
                     min_sim: float = 0.0) -> list[list[dict]]:
    """
    Batch FAISS-mode recommend.
    Returns one list of result dicts per input title.
    """
    if not titles:
        return []

    if years is None:
        years = [None] * len(titles)

    queries = _embed(titles)                     # (n_titles, 384)
    D, I = _faiss_search(queries, k=TOP_K)       # (n_titles, TOP_K)

    results: list[list[dict]] = []
    for i, (dists, idxs) in enumerate(zip(D, I)):
        year = years[i]
        matches: list[dict] = []
        for sim, pos in zip(dists.tolist(), idxs.tolist()):
            if pos < 0:           # FAISS pads with -1 when fewer than k results
                continue
            if sim < min_sim:
                break
            meta = _meta_lookup(pos)
            if meta is None:
                continue
            db_year = meta.get("year")
            if year and db_year:
                try:
                    if abs(int(year) - int(db_year)) > YEAR_TOLERANCE:
                        continue
                except (TypeError, ValueError):
                    continue
            matches.append({**meta, "similarity": round(float(sim), 4)})
            if len(matches) >= n:
                break
        results.append(matches)
    return results


def _faiss_by_title(title: str, year: int | None = None) -> "SolrResult":
    """Single-title FAISS lookup; returns SolrResult."""
    from solr_lookup import SolrResult, MatchMethod

    if not title or not title.strip():
        return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

    recs = _faiss_recommend([title], years=[year], n=1, min_sim=VECTOR_THRESHOLD)
    if not recs or not recs[0]:
        return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

    best = recs[0][0]
    # Build a minimal "record" dict that matches what SolrResult expects
    record = {
        "id":               best.get("openalex_id"),
        "doi":              best.get("doi"),
        "title":            best.get("title"),
        "publication_year": best.get("year"),
    }
    return SolrResult(
        found=True,
        method=MatchMethod.VECTOR,
        record=record,
        confidence=best["similarity"],
    )


# ---------------------------------------------------------------------------
# Solr-rerank fallback (original implementation)
# ---------------------------------------------------------------------------



def _get_title_str(doc: dict) -> str:
    t = doc.get("title")
    if isinstance(t, list):
        return t[0] if t else ""
    return t or ""


def _solr_broad_search(query_title: str, year: int | None = None,
                       rows: int = BROAD_CANDIDATES) -> list[dict]:
    params = {
        "q":       query_title,
        "qf":      "title^4 abstract",
        "pf":      "title^20",
        "defType": "edismax",
        "mm":      "3<70%",
        "fl":      "id,title,doi,publication_year,primary_location",
        "rows":    str(rows),
        "wt":      "json",
        "facet":   "false",
    }
    if year:
        params["fq"] = f"publication_year:[{year - 3} TO {year + 3}]"
    try:
        resp = requests.get(SOLR_URL, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()["response"]["docs"]
    except Exception as e:
        print(f"  [vector] Solr broad search failed: {e}", file=sys.stderr)
        return []


def _embed_batch(texts: list[str]) -> np.ndarray:
    model = _get_model()
    return model.encode(texts, normalize_embeddings=True, show_progress_bar=False)


def _solr_rerank_by_title(title: str, year: int | None = None) -> "SolrResult":
    """Original Solr-rerank approach (fallback when FAISS index not built)."""
    from solr_lookup import SolrResult, MatchMethod

    if not title or not title.strip():
        return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

    docs = _solr_broad_search(title, year=year, rows=BROAD_CANDIDATES)
    if not docs:
        return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

    candidate_titles = [_get_title_str(d) for d in docs]
    titled_pairs = [(d, t) for d, t in zip(docs, candidate_titles) if t]
    if not titled_pairs:
        return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

    titled_docs, titled_titles = zip(*titled_pairs)
    all_texts  = [title] + list(titled_titles)
    embeddings = _embed_batch(all_texts)
    query_emb  = embeddings[0]
    cand_embs  = embeddings[1:]

    sims      = cand_embs @ query_emb
    best_idx  = int(np.argmax(sims))
    best_score = float(sims[best_idx])

    if best_score < VECTOR_THRESHOLD:
        return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

    best_doc = titled_docs[best_idx]
    if year:
        db_year = best_doc.get("publication_year")
        if not db_year:
            return SolrResult(found=False, method=MatchMethod.NOT_FOUND)
        try:
            if abs(int(year) - int(db_year)) > YEAR_TOLERANCE:
                return SolrResult(found=False, method=MatchMethod.NOT_FOUND)
        except (ValueError, TypeError):
            return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

    return SolrResult(
        found=True,
        method=MatchMethod.VECTOR,
        record=best_doc,
        confidence=round(best_score, 4),
    )


# ---------------------------------------------------------------------------
# Public API — VectorLookup class
# ---------------------------------------------------------------------------

class VectorLookup:
    """
    Semantic vector fallback for citations not found by Solr phrase search.

    Uses pre-built FAISS IVFPQ index when available (fast, ~25 ms/citation).
    Falls back to Solr-rerank when not (original behaviour, ~4-5 s/citation).

    To check which mode is active:
        vl = VectorLookup()
        print(vl.mode)   # "faiss" or "solr-rerank"
    """

    def __init__(self, threshold: float = VECTOR_THRESHOLD,
                 candidates: int = BROAD_CANDIDATES):
        self.threshold  = threshold
        self.candidates = candidates
        # Trigger lazy load of index (if present) and model
        index, _ = _get_faiss()
        self.mode = "faiss" if index is not None else "solr-rerank"
        if self.mode == "faiss":
            pass  # model loads on first encode call
        else:
            print("[vector_lookup] FAISS index not found — using Solr-rerank fallback",
                  file=sys.stderr)
            print(f"  Build with: python3 build_faiss_index.py  (index dir: {INDEX_DIR})",
                  file=sys.stderr)

    def by_title(self, title: str, year: Optional[int] = None) -> "SolrResult":
        """
        Find the best matching paper for `title` using vector similarity.
        Returns SolrResult (found=True/False, method=VECTOR/NOT_FOUND).
        """
        if self.mode == "faiss":
            return _faiss_by_title(title, year=year)
        return _solr_rerank_by_title(title, year=year)

    def by_citation(self, parsed) -> "SolrResult":
        """Convenience wrapper: accepts a citation namespace/object with .title/.year."""
        return self.by_title(parsed.title, year=parsed.year)

    # ------------------------------------------------------------------
    # Recommendation API
    # ------------------------------------------------------------------

    def recommend(self,
                  title: str,
                  year: Optional[int] = None,
                  n: int = 3,
                  min_sim: float = 0.0) -> list[dict]:
        """
        Return the top-N most similar papers for the given title.

        Each result dict has: title, doi, year, similarity, openalex_id.
        Applies year guard (±YEAR_TOLERANCE) when year is provided.
        """
        if self.mode == "faiss":
            recs = _faiss_recommend([title], years=[year], n=n, min_sim=min_sim)
            return recs[0] if recs else []
        return self._solr_recommend(title, year=year, n=n, min_sim=min_sim)

    def batch_recommend(self,
                        titles: list[str],
                        years: list[int | None] | None = None,
                        n: int = 3,
                        min_sim: float = 0.0) -> list[list[dict]]:
        """
        Recommend for multiple titles in one batched call.

        FAISS mode: encodes all titles in one model.encode() call + one index.search().
        Solr-rerank mode: falls back to sequential per-title calls.

        Parameters
        ----------
        titles : list of citation titles
        years  : optional list of years (same length as titles)
        n      : top-N results per title
        min_sim: minimum similarity threshold

        Returns
        -------
        list[list[dict]] — one list per input title
        """
        if self.mode == "faiss":
            return _faiss_recommend(titles, years=years, n=n, min_sim=min_sim)
        # Fallback: sequential
        if years is None:
            years = [None] * len(titles)
        return [self._solr_recommend(t, year=y, n=n, min_sim=min_sim)
                for t, y in zip(titles, years)]

    def _solr_recommend(self, title: str, year: Optional[int] = None,
                        n: int = 3, min_sim: float = 0.0) -> list[dict]:
        """Solr-rerank recommend (fallback)."""
        docs = _solr_broad_search(title, year=year, rows=max(self.candidates, n * 4))
        if not docs:
            return []
        titled_docs = [d for d in docs if _get_title_str(d)]
        if not titled_docs:
            return []
        candidate_titles = [_get_title_str(d) for d in titled_docs]
        embeddings = _embed_batch([title] + candidate_titles)
        query_emb  = embeddings[0]
        cand_embs  = embeddings[1:]
        sims = cand_embs @ query_emb
        ranked = sorted(zip(sims.tolist(), titled_docs), key=lambda x: x[0], reverse=True)
        results = []
        for sim, doc in ranked[:n]:
            if sim < min_sim:
                break
            pl = doc.get("primary_location") or {}
            journal = None
            if isinstance(pl, dict):
                journal = (pl.get("source") or {}).get("display_name")
            results.append({
                "title":       _get_title_str(doc),
                "doi":         doc.get("doi"),
                "year":        doc.get("publication_year"),
                "journal":     journal,
                "similarity":  round(float(sim), 4),
                "openalex_id": doc.get("id"),
            })
        return results

    def recommend_from_raw(self, raw_citation: str, n: int = 3,
                           min_sim: float = 0.0) -> tuple[dict, list[dict]]:
        """Parse a free-text citation, then recommend top-N matches."""
        from citation_parser import parse_citation
        p = parse_citation(raw_citation)
        parsed_dict = {"title": p.title, "year": p.year, "doi": p.doi,
                       "source": p.source, "raw": p.raw}
        recs = self.recommend(p.title or raw_citation, year=p.year, n=n, min_sim=min_sim)
        return parsed_dict, recs


# ---------------------------------------------------------------------------
# Standalone test / benchmark
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import time
    sys.path.insert(0, str(_HERE))

    print(f"Index dir: {INDEX_DIR}")
    print(f"Index exists: {INDEX_PATH.exists()}\n")

    vl = VectorLookup()
    print(f"Mode: {vl.mode}\n")

    test_cases = [
        ("A novel coronavirus from patients with pneumonia in China", 2020,
         "exact title (control)"),
        ("From patients with pneumonia in China: a novel coronavirus", 2020,
         "word-order swap"),
        ("A novel coronavirus from patients with", 2020,
         "truncated (6 words)"),
        ("Identiﬁcation of a novel coronavirus causing severe pneumonia", 2020,
         "ligature artifact"),
        ("Fabricated citation about quantum biology in ancient Rome", 2020,
         "non-existent title"),
    ]

    # Warm up model
    print("Warming up model…")
    vl.recommend("warm up", n=1)

    # Batch test (FAISS advantage)
    all_titles = [t for t, y, _ in test_cases]
    all_years  = [y for t, y, _ in test_cases]
    t0 = time.perf_counter()
    batch_recs = vl.batch_recommend(all_titles, years=all_years, n=1)
    batch_ms = (time.perf_counter() - t0) * 1000
    print(f"\nBatch ({len(all_titles)} titles): {batch_ms:.0f} ms total  "
          f"({batch_ms/len(all_titles):.0f} ms/title avg)\n")

    for (query, year, desc), recs in zip(test_cases, batch_recs):
        print(f"  {desc}")
        if recs:
            r = recs[0]
            print(f"    → sim={r['similarity']:.3f}  {r['title'][:70]}")
        else:
            print(f"    → NOT_FOUND")

    print()

    # Per-title timing
    print("Per-title timing:")
    for query, year, desc in test_cases:
        t0 = time.perf_counter()
        result = vl.by_title(query, year=year)
        ms = (time.perf_counter() - t0) * 1000
        status = f"found (sim={result.confidence:.3f})" if result.found else "NOT_FOUND"
        print(f"  {ms:6.0f} ms  {desc[:40]:40s}  {status}")
