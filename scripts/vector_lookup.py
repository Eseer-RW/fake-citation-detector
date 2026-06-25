"""
vector_lookup.py — semantic vector re-ranking for NOT_FOUND citations.

How it works
------------
When Solr phrase-search and Crossref both fail to find a citation, this module
provides a fallback that is robust to minor title variations (word-order swaps,
OCR artifacts, truncation, ligature mis-encoding, etc.):

  1. Run a *broad* Solr edismax query for the citation title with a wider result
     window (40 candidates, no year filter).
  2. Embed the query title AND all candidate titles with a lightweight sentence
     transformer (all-MiniLM-L6-v2, 384-dim).  Embedding 41 short strings takes
     ~15 ms on CPU.
  3. Re-rank by cosine similarity.  If the best candidate clears the threshold
     (default 0.82), return it as a VECTOR match.

Integration
-----------
Add to solr_lookup.py MatchMethod:
    VECTOR = "vector"

Add VectorLookup to grobid_verify.py Phase 4 (after Crossref fallback).

Usage as a module
-----------------
    from vector_lookup import VectorLookup
    vl = VectorLookup()                   # loads model once (~80 MB)
    result = vl.by_title(title, solr_lookup, year=2020)
    # result is a SolrResult with method=MatchMethod.VECTOR

Standalone test
---------------
    python3 vector_lookup.py
"""

from __future__ import annotations

import re
import sys
import pathlib
import types
from typing import Optional

import numpy as np
import requests

# ---------------------------------------------------------------------------
# Defer heavy import until first use so importing this module is fast even if
# sentence-transformers isn't installed.
# ---------------------------------------------------------------------------
_model = None
_MODEL_NAME = "all-MiniLM-L6-v2"   # 80 MB, 384-dim, fast on CPU


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SOLR_URL             = "http://galaxy:8983/solr/openalexWorks/select"
VECTOR_THRESHOLD     = 0.82   # cosine similarity to accept as a match
BROAD_CANDIDATES     = 40     # how many Solr candidates to re-rank
YEAR_TOLERANCE       = 2      # max allowed |year_cited - year_db| for a vector hit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity for pre-normalised vectors."""
    return float(np.dot(a, b))


def _get_title(doc: dict) -> str:
    t = doc.get("title")
    if isinstance(t, list):
        return t[0] if t else ""
    return t or ""


def _solr_broad_search(query_title: str, year: Optional[int] = None,
                       rows: int = BROAD_CANDIDATES) -> list[dict]:
    """
    Fire a broad edismax query with a relaxed year window.

    Uses phrase-boost (pf=title^20) so papers whose title closely matches the
    query phrase are boosted to the top of the 40-candidate window.
    minimum-match (mm=3<70%) filters out documents that share only a couple of
    common stopwords, narrowing the result set from millions to thousands.

    Returns a list of Solr docs (up to `rows`).
    """
    params = {
        "q":       query_title,
        "qf":      "title^4 abstract",
        "pf":      "title^20",   # phrase-boost: reward phrase-level matches
        "defType": "edismax",
        "mm":      "3<70%",      # min-match: >3 tokens → 70% must match
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
    """Embed a list of strings; returns shape (len, 384), L2-normalised."""
    model = _get_model()
    return model.encode(texts, normalize_embeddings=True, show_progress_bar=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class VectorLookup:
    """
    Semantic re-ranking fallback for citations not found by phrase search.

    Parameters
    ----------
    threshold : float
        Minimum cosine similarity to accept a match (default 0.82).
    candidates : int
        Number of Solr candidates to retrieve and re-rank (default 40).
    """

    def __init__(self, threshold: float = VECTOR_THRESHOLD,
                 candidates: int = BROAD_CANDIDATES):
        self.threshold  = threshold
        self.candidates = candidates

    def by_title(self, title: str, year: Optional[int] = None) -> "SolrResult":
        """
        Find the best matching paper for `title` using vector re-ranking.

        Returns a SolrResult (importing from solr_lookup at call time to avoid
        circular imports).  If no match clears the threshold, returns NOT_FOUND.
        """
        from solr_lookup import SolrResult, MatchMethod

        if not title or not title.strip():
            return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

        # ── Step 1: broad Solr search ──────────────────────────────────────
        docs = _solr_broad_search(title, year=year, rows=self.candidates)
        if not docs:
            return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

        candidate_titles = [_get_title(d) for d in docs]
        candidate_titles_clean = [t for t in candidate_titles if t]

        if not candidate_titles_clean:
            return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

        # ── Step 2: embed query + candidates ──────────────────────────────
        all_texts   = [title] + candidate_titles_clean
        embeddings  = _embed_batch(all_texts)
        query_emb   = embeddings[0]
        cand_embs   = embeddings[1:]

        # ── Step 3: cosine similarity re-ranking ──────────────────────────
        sims       = cand_embs @ query_emb   # shape (n_candidates,)
        best_idx   = int(np.argmax(sims))
        best_score = float(sims[best_idx])

        if best_score < self.threshold:
            return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

        # Map back to the original docs list (candidate_titles_clean may be shorter)
        # because we filtered out docs with no title.
        titled_docs = [d for d in docs if _get_title(d)]
        best_doc    = titled_docs[best_idx]

        # Year guard: when the caller provides a year, we require the DB
        # year to be present and within YEAR_TOLERANCE.  If the DB doc
        # has no year field we conservatively reject — it is better to
        # return NOT_FOUND than to match a wrong-era paper (e.g. a SARS
        # 2003 paper when the citation is a COVID-19 2020 paper).
        if year:
            db_year = best_doc.get("publication_year")
            if not db_year:
                # No year in DB doc — cannot verify; reject conservatively.
                return SolrResult(found=False, method=MatchMethod.NOT_FOUND)
            try:
                if abs(int(year) - int(db_year)) > YEAR_TOLERANCE:
                    # Year mismatch is large — likely a wrong paper.  Reject.
                    return SolrResult(found=False, method=MatchMethod.NOT_FOUND)
            except (ValueError, TypeError):
                # Unparseable year in DB — reject conservatively.
                return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

        return SolrResult(
            found=True,
            method=MatchMethod.VECTOR,
            record=best_doc,
            confidence=round(best_score, 4),
        )

    def by_citation(self, parsed) -> "SolrResult":
        """Convenience wrapper: accepts a citation namespace object."""
        return self.by_title(parsed.title, year=parsed.year)


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import time

    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from solr_lookup import SolrLookup, MatchMethod

    print(f"Loading model '{_MODEL_NAME}'…", flush=True)
    t0 = time.time()
    vl = VectorLookup()
    _get_model()  # warm up
    print(f"Model loaded in {time.time()-t0:.1f}s\n")

    test_cases = [
        # (query_title, year, description)
        # Exact title — should hit via Solr even without vector
        ("A novel coronavirus from patients with pneumonia in China", 2020,
         "exact title (control)"),
        # Word-order swap — phrase search fails, vector should catch it
        ("From patients with pneumonia in China: a novel coronavirus", 2020,
         "word-order swap"),
        # Truncated title (first 6 words) — phrase search may fail
        ("A novel coronavirus from patients with", 2020,
         "truncated (6 words)"),
        # Ligature artifact: 'fi' → 'ﬁ'
        ("Identiﬁcation of a novel coronavirus causing severe pneumonia", 2020,
         "ligature artifact (ﬁ)"),
        # Genuinely non-existent title
        ("Fabricated citation about quantum biology in ancient Rome", 2020,
         "non-existent title"),
    ]

    with SolrLookup() as sl:
        for query, year, desc in test_cases:
            print(f"Test: {desc}")
            print(f"  Query: {query[:70]}")

            # First try normal Solr (control)
            r_solr = sl.by_title(query, year=year)
            print(f"  Solr:   found={r_solr.found}  method={r_solr.method}  "
                  f"conf={r_solr.confidence:.3f}")

            # Then vector fallback
            t1 = time.time()
            r_vec = vl.by_title(query, year=year)
            elapsed = time.time() - t1
            print(f"  Vector: found={r_vec.found}  method={r_vec.method}  "
                  f"conf={r_vec.confidence:.3f}  ({elapsed*1000:.0f} ms)")
            if r_vec.found and r_vec.record:
                print(f"  → DB title: {_get_title(r_vec.record)[:70]}")
            print()
