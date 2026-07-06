"""
crossref_lookup.py — citation lookup via local Crossref index + REST API fallback.

Lookup order for each citation:
  1. Local SQLite index (~179M records from March 2026 Crossref public data file)
     → DOI exact match, then normalized-title exact match
  2. Crossref REST API (polite pool, circuit-break on 429/timeout)

The local index is a singleton opened once per process. It is read-only and
thread-safe (check_same_thread=False, WAL mode, PRAGMA query_only).

Index location: /home/rwang/crossref/crossref_index.db
Build script:   /home/rwang/crossref/scripts/build_crossref_index.py
"""
import re
import sqlite3
import threading
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import requests

from solr_lookup import SolrResult, MatchMethod

CROSSREF_API         = "https://api.crossref.org/works"
SIMILARITY_THRESHOLD = 0.85
LOCAL_DB_PATH        = Path('/home/rwang/crossref/crossref_index.db')

# Global rate limiter — shared across ALL threads and jobs in this process.
# Caps total Crossref API throughput at 45 req/s, staying under polite-pool limit (50/s)
# even when 30 concurrent workers are running.
_global_cr_lock     = threading.Lock()
_global_cr_last_req = 0.0
# 10 concurrent jobs × 4 req/s each = 40 req/s total → under 50/s polite-pool limit.
# Each job process independently enforces this; cross-process coordination via lower rate.
_GLOBAL_CR_DELAY    = 1.0 / 4    # 250 ms between requests per process


def _global_rate_limit() -> None:
    global _global_cr_last_req
    with _global_cr_lock:
        wait = _GLOBAL_CR_DELAY - (time.monotonic() - _global_cr_last_req)
        if wait > 0:
            time.sleep(wait)
        _global_cr_last_req = time.monotonic()


# ── title helpers ─────────────────────────────────────────────────────────────

def _normalize_title(t) -> str:
    """Flatten a list title and strip HTML tags Crossref sometimes includes."""
    if isinstance(t, list):
        t = " ".join(t)
    t = re.sub(r'<[^>]+>', '', t)
    return re.sub(r'\s+', ' ', t).strip()


def _norm_for_index(t: str) -> str:
    """Normalize title for index lookup — must match build_crossref_index._norm()."""
    if not t:
        return ''
    t = unicodedata.normalize('NFKD', t.lower())
    t = t.encode('ascii', 'ignore').decode()
    t = re.sub(r'[^a-z0-9 ]', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()


def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _extract_year(msg: dict) -> Optional[int]:
    for key in ("published-print", "published-online", "published"):
        parts = msg.get(key, {}).get("date-parts", [])
        if parts and parts[0] and parts[0][0]:
            return int(parts[0][0])
    return None


def _to_record(msg: dict) -> dict:
    titles = msg.get("title") or []
    title  = _normalize_title(titles) if titles else ""
    year   = _extract_year(msg)
    ct     = msg.get("container-title") or []
    return {
        "title":            title,
        "doi":              msg.get("DOI", ""),
        "publication_year": year,
        "container-title":  [ct[0]] if ct else [],
        "source":           "crossref",
    }


def _row_to_record(row: tuple) -> dict:
    """Convert a (doi, title, year, journal, author1) DB row to the same shape as _to_record."""
    doi, title, year, journal, _ = row
    return {
        "title":            title or '',
        "doi":              doi or '',
        "publication_year": year,
        "container-title":  [journal] if journal else [],
        "source":           "local_crossref",
    }


# ── local SQLite index ────────────────────────────────────────────────────────

class _LocalCrossrefDB:
    """
    Singleton read-only connection to the local Crossref SQLite index.
    Opens once per process; thread-safe via check_same_thread=False + WAL.
    """
    _instance: Optional["_LocalCrossrefDB"] = None
    _lock = threading.Lock()

    def __init__(self):
        self.con = sqlite3.connect(str(LOCAL_DB_PATH), check_same_thread=False)
        self.con.execute('PRAGMA query_only=ON')
        self.con.execute('PRAGMA cache_size=-500000')  # 500 MB page cache
        # Detect whether title index exists yet (may still be building)
        idxs = {r[0] for r in self.con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
        self.has_title_idx = 'idx_title_norm' in idxs
        count = self.con.execute("SELECT COUNT(*) FROM refs").fetchone()[0]
        print(f"[local-crossref] opened: {count:,} rows, "
              f"title_idx={'yes' if self.has_title_idx else 'building'}",
              flush=True)

    @classmethod
    def get(cls) -> Optional["_LocalCrossrefDB"]:
        if not LOCAL_DB_PATH.exists():
            return None
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    try:
                        cls._instance = cls()
                    except Exception as e:
                        print(f"[local-crossref] open failed: {e}", flush=True)
        return cls._instance

    def by_doi(self, doi: str) -> Optional[tuple]:
        """Return (doi, title, year, journal, author1) or None."""
        doi = doi.strip().lower()
        return self.con.execute(
            'SELECT doi, title, year, journal, author1 FROM refs WHERE doi = ?',
            (doi,)
        ).fetchone()

    def by_title_norm(self, title_norm: str) -> Optional[tuple]:
        """Return first row matching normalized title, or None."""
        if not self.has_title_idx:
            # Index still building — refresh check
            idxs = {r[0] for r in self.con.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )}
            self.has_title_idx = 'idx_title_norm' in idxs
            if not self.has_title_idx:
                return None
        return self.con.execute(
            'SELECT doi, title, year, journal, author1 FROM refs WHERE title_norm = ?',
            (title_norm,)
        ).fetchone()


# ── CrossrefLookup ────────────────────────────────────────────────────────────

class CrossrefLookup:
    """
    Citation lookup: local SQLite index first, Crossref REST API as fallback.
    Same by_doi / by_title / by_citation interface as SolrLookup.
    """

    def __init__(
        self,
        email: Optional[str] = None,
        similarity_threshold: float = SIMILARITY_THRESHOLD,
        min_delay: float = 0.05,
    ):
        ua = "FakeCitationDetector/1.0"
        if email:
            ua += f" (mailto:{email})"
        self.headers   = {"User-Agent": ua}
        self.threshold = similarity_threshold
        self.min_delay = min_delay
        self._last_req = 0.0
        self._net_error = False

    # ── HTTP helper ───────────────────────────────────────────────────────────

    def _get(self, url: str, params: Optional[dict] = None) -> Optional[dict]:
        # Global rate limit first (shared across all threads), then per-instance jitter
        _global_rate_limit()
        elapsed = time.monotonic() - self._last_req
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        try:
            resp = requests.get(url, params=params or {},
                                headers=self.headers, timeout=8)
            self._last_req = time.monotonic()
            resp.raise_for_status()
            self._net_error = False
            return resp.json()
        except Exception as e:
            msg = str(e)
            if ("429" in msg or "timed out" in msg.lower()
                    or "timeout" in msg.lower() or "500" in msg):
                self._net_error = True
            print(f"    [crossref] request failed: {e}")
            return None

    # ── public lookup methods ─────────────────────────────────────────────────

    def by_doi(self, doi: str) -> SolrResult:
        doi_clean = re.sub(r'^https?://(?:dx.)?doi.org/', '', doi.strip(), flags=re.I).split('?')[0].split('#')[0].strip()

        # 1. Local index
        ldb = _LocalCrossrefDB.get()
        if ldb:
            row = ldb.by_doi(doi_clean)
            if row:
                return SolrResult(
                    found=True, method=MatchMethod.XREF_DOI,
                    record=_row_to_record(row), confidence=1.0,
                )

        # 2. API fallback
        data = self._get(f"{CROSSREF_API}/{doi_clean}")
        if not data or data.get("status") != "ok":
            return SolrResult(found=False, method=MatchMethod.NOT_FOUND)
        return SolrResult(
            found=True, method=MatchMethod.XREF_DOI,
            record=_to_record(data["message"]), confidence=1.0,
        )

    def by_title(self, title: str, year: Optional[int] = None,
                 candidates: int = 5) -> SolrResult:
        title_norm = _norm_for_index(title)

        # 1. Local index (exact normalized title match)
        ldb = _LocalCrossrefDB.get()
        if ldb and title_norm:
            row = ldb.by_title_norm(title_norm)
            if row:
                sim = _title_similarity(title, row[1] or '')
                if sim >= self.threshold:
                    # Year compatibility check (±1 year)
                    row_year = row[2]
                    year_ok = (not year or not row_year
                               or abs(int(row_year) - int(year)) <= 1)
                    if year_ok:
                        method = (MatchMethod.XREF_TITLE_YEAR if year
                                  else MatchMethod.XREF_TITLE_ONLY)
                        return SolrResult(
                            found=True, method=method,
                            record=_row_to_record(row),
                            confidence=round(sim, 4),
                        )
            else:
                # Title absent from 179M-record local dump → gray literature,
                # news articles, reports, or datasets with no Crossref DOI.
                # Skip the API call to avoid burning rate-limit quota on misses.
                return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

        # 2. API fallback (only when local index is unavailable or title_norm empty)
        params: dict = {
            "query.title": title,
            "rows": candidates,
            "select": "DOI,title,published,published-print,published-online,container-title",
        }
        if year:
            params["filter"] = (
                f"from-pub-date:{year - 1}-01,until-pub-date:{year + 1}-12"
            )
        data = self._get(CROSSREF_API, params)
        if not data or data.get("status") != "ok":
            return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

        items = data["message"].get("items", [])
        best_item, best_sim = None, 0.0
        for item in items:
            raw = item.get("title") or item.get("short-title") or []
            if not raw:
                continue
            candidate = _normalize_title(raw)
            sim = _title_similarity(title, candidate)
            if sim > best_sim:
                best_sim = sim
                best_item = item

        if best_item and best_sim >= self.threshold:
            method = MatchMethod.XREF_TITLE_YEAR if year else MatchMethod.XREF_TITLE_ONLY
            return SolrResult(
                found=True, method=method,
                record=_to_record(best_item), confidence=round(best_sim, 4),
            )
        return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

    @staticmethod
    def _title_variants(title: str) -> list:
        variants = []
        normalized = (title
                      .replace('®', 'fi').replace('ð', '-')
                      .replace('Ð', '-').replace('±', '-')
                      .replace('“', '"').replace('”', '"')
                      .replace('‘', "'").replace('’', "'"))
        if normalized != title:
            variants.append(normalized)
        if '. ' in title:
            before, after = title.split('. ', 1)
            if len(after.strip()) > 20:
                variants.append(after.strip())
            if len(before.strip()) > 20:
                variants.append(before.strip())
        return variants

    def by_citation(self, parsed) -> SolrResult:
        doi   = getattr(parsed, 'doi',   None)
        title = getattr(parsed, 'title', None)
        year  = getattr(parsed, 'year',  None)
        self._net_error = False

        if doi:
            r = self.by_doi(doi)
            if r.found:
                return r
            if self._net_error:
                return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

        if title and year:
            r = self.by_title(title, year=year)
            if r.found:
                return r
            if self._net_error:
                return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

        if title:
            r = self.by_title(title)
            if r.found:
                return r
            if self._net_error:
                return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

        if title:
            for variant in self._title_variants(title):
                if year:
                    r = self.by_title(variant, year=year)
                    if r.found:
                        return r
                    if self._net_error:
                        return SolrResult(found=False, method=MatchMethod.NOT_FOUND)
                r = self.by_title(variant)
                if r.found:
                    return r
                if self._net_error:
                    return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

        return SolrResult(found=False, method=MatchMethod.NOT_FOUND)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ── standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Testing CrossrefLookup (local index + API fallback)...")
    ldb = _LocalCrossrefDB.get()
    if ldb:
        print(f"Local DB open. Testing DOI lookup...")
        row = ldb.by_doi("10.1056/nejmoa2001017")
        print(f"  DOI 10.1056/nejmoa2001017 → {row}")
    else:
        print("  Local DB not available yet.")

    with CrossrefLookup(email="rwang@insilicom.com") as cr:
        r = cr.by_doi("10.1056/NEJMoa2001017")
        print(f"\nby_doi: found={r.found}  method={r.method}  source={r.record.get('source') if r.record else None}")
        if r.record:
            print(f"  title: {r.record.get('title')[:80]}")

        r2 = cr.by_title("A novel coronavirus from patients with pneumonia in China", year=2020)
        print(f"\nby_title: found={r2.found}  method={r2.method}  conf={r2.confidence}")


# ── concurrent batch lookup ───────────────────────────────────────────────────

import threading
from concurrent.futures import ThreadPoolExecutor

_thread_local = threading.local()


def _thread_crossref() -> CrossrefLookup:
    if not hasattr(_thread_local, "lookup"):
        _thread_local.lookup = CrossrefLookup(email="rwang@insilicom.com")
    return _thread_local.lookup


def batch_crossref(parsed_list: list, max_workers: int = 4) -> list:
    if not parsed_list:
        return []

    def _lookup(parsed):
        return _thread_crossref().by_citation(parsed)

    n = min(max_workers, len(parsed_list))
    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(_lookup, parsed_list))
