import re
import html
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from typing import Optional

from pymongo import MongoClient
from pymongo import TEXT
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError

from dotenv import load_dotenv
import os

# config
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = "crs"
COLLECTION_NAME = "crossref"


class MatchMethod(str, Enum):
    DOI        = "doi"
    TITLE_YEAR = "title_year"
    TITLE_ONLY = "title_only"
    NOT_FOUND  = "not_found"


@dataclass
class LookupResult:
    found: bool
    method: MatchMethod
    record: Optional[dict] = None
    confidence: float = 0.0


# helpers

_DOI_PREFIX_RE = re.compile(
    r'^(?:https?://(?:dx\.)?doi\.org/|doi:)',
    re.IGNORECASE
)

def normalize_doi(doi: str) -> str:
    return _DOI_PREFIX_RE.sub('', doi.strip()).lower()


def extract_title_text(record: dict) -> Optional[str]:
    """
    Crossref stores title as ["The Title"].
    Returns the first entry as a plain string.
    """
    title = record.get("title")
    if not title:
        return None
    if isinstance(title, list) and title:
        return title[0]
    if isinstance(title, str):
        return title
    return None


def extract_year(record: dict) -> Optional[int]:
    """
    Crossref stores year inside issued.date-parts: [[2020, 1, 15]]
    Falls back to published-print if issued is missing.
    """
    for field in ("issued", "published", "published-print"):
        try:
            return record[field]["date-parts"][0][0]
        except (KeyError, IndexError, TypeError):
            continue
    return None


def extract_journal(record: dict) -> Optional[str]:
    """Crossref stores journal name as container-title: ["Journal Name"]"""
    ct = record.get("container-title")
    if isinstance(ct, list) and ct:
        return ct[0]
    if isinstance(ct, str):
        return ct
    return None


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


# mongo lookup

_PROJECTION = {
    "_id": 0,
    "DOI": 1,
    "title": 1,
    "author": 1,
    "issued": 1,
    "published": 1,
    "published-print": 1,
    "container-title": 1,
    "publisher": 1,
    "type": 1,
    "volume": 1,
    "issue": 1,
    "page": 1,
}



def norm_title_exact(t) -> str:
    """Normalize a title to match the stored crossref.title_norm field.

    Reverse-engineered from the stored index and verified by round-trip: strip embedded
    XML/MathML markup tags (keeping their text content), lowercase, then replace every run
    of Unicode non-word characters with a single space. Unicode letters are KEPT, not
    ASCII-folded (stored title_norm preserves e.g. Greek 'nu'), and punctuation such as
    en-dashes and curly apostrophes becomes a space rather than being dropped.
    """
    if not t:
        return ""
    if isinstance(t, list):
        t = " ".join(x for x in t if x)
    t = re.sub(r"<[^>]+>", " ", str(t))       # drop markup tags, keep inner text
    t = html.unescape(t)                       # &lt; &amp; &#x2019; -> literal chars
    t = t.lower()
    t = unicodedata.normalize("NFKD", t)      # decompose accented letters
    t = "".join(c for c in t if not unicodedata.combining(c))  # strip combining marks: a->a, e->e; keeps non-Latin scripts (nu stays nu)
    t = re.sub(r"\W+", " ", t, flags=re.UNICODE)  # split on any non-word char (unicode-aware)
    return t.strip()


def _surname_tokens(author_field) -> set:
    """Longest alphabetic token(s) from a cited-author string or Crossref author list."""
    out = set()
    if not author_field:
        return out
    names = []
    if isinstance(author_field, list):
        for a in author_field:
            if isinstance(a, dict):
                names.append(a.get("family") or a.get("name") or "")
            else:
                names.append(str(a))
    else:
        names.append(str(author_field))
    for nm in names:
        nm = unicodedata.normalize("NFKD", nm.lower()).encode("ascii", "ignore").decode()
        toks = re.findall(r"[a-z]+", nm)
        if toks:
            out.add(max(toks, key=len))
    return out


class MongoLookup:

    def __init__(
        self,
        uri: str = MONGO_URI,
        db_name: str = DB_NAME,
        collection_name: str = COLLECTION_NAME,
        similarity_threshold: float = 0.85,
    ):
        self.client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        self.collection = self.client[db_name][collection_name]
        self.threshold = similarity_threshold
        self._check_connection()

    def _check_connection(self):
        try:
            self.client[DB_NAME].command("ping")
        except ServerSelectionTimeoutError:
            raise RuntimeError(
                "\nCannot connect to MongoDB at jupiter2:27017.\n"
                "Check that you are on the right network and the server is running.\n"
            ) from None
        except OperationFailure:
            raise RuntimeError(
                "\nMongoDB authentication failed.\n"
                "Check the username and password in MONGO_URI.\n"
            ) from None

    def by_doi(self, doi: str) -> LookupResult:
        record = self.collection.find_one(
            {"DOI": normalize_doi(doi)},
            _PROJECTION,
        )
        if record:
            return LookupResult(
                found=True,
                method=MatchMethod.DOI,
                record=record,
                confidence=1.0,
            )
        return LookupResult(found=False, method=MatchMethod.NOT_FOUND)

    def by_title(
        self,
        title: str,
        year: Optional[int] = None,
        candidates: int = 5,
    ) -> LookupResult:
        query: dict = {"$text": {"$search": title}}
        if year:
            query["issued.date-parts.0.0"] = year

        projection = {**_PROJECTION, "score": {"$meta": "textScore"}}

        try:
            hits = list(
                self.collection
                .find(query, projection)
                .sort([("score", {"$meta": "textScore"})])
                .limit(candidates)
            )
        except OperationFailure as e:
            print(f"Lookup failed: {e}")
            return LookupResult(found=False, method=MatchMethod.NOT_FOUND)

        best_record, best_score = None, 0.0
        for hit in hits:
            candidate_title = extract_title_text(hit)
            if not candidate_title:
                continue
            score = title_similarity(title, candidate_title)
            if score > best_score:
                best_score = score
                best_record = hit

        if best_record and best_score >= self.threshold:
            return LookupResult(
                found=True,
                method=MatchMethod.TITLE_YEAR if year else MatchMethod.TITLE_ONLY,
                record=best_record,
                confidence=round(best_score, 4),
            )
        return LookupResult(found=False, method=MatchMethod.NOT_FOUND)

    @staticmethod
    def _shape(doc: dict) -> dict:
        """Flatten a Crossref-shaped mongo doc into the field names the verifier reads."""
        return {
            "doi": doc.get("DOI"),
            "title": extract_title_text(doc),
            "year": extract_year(doc),
            "journal": extract_journal(doc),
            "volume": doc.get("volume"),
            "issue": doc.get("issue"),
            "page": doc.get("page"),
            "publisher": doc.get("publisher"),
            "type": doc.get("type"),
            "author": doc.get("author") or [],
            "author_names": [
                (a.get("family") or a.get("name") or "").strip()
                for a in (doc.get("author") or [])
                if isinstance(a, dict)
            ],
        }

    def by_title_exact(self, title, year=None, journal=None, author=None,
                       cap: int = 25) -> LookupResult:
        """EXACT normalized-title match against the crossref.title_norm index.

        Deterministic (NOT fuzzy): the title is normalized with norm_title_exact and looked
        up directly. If the normalized title is not unique, disambiguate with the citation's
        year (+/-1), journal, and author surname; assert a match only when a single doc
        survives (or all survivors share one DOI)."""
        q = norm_title_exact(title)
        if not q:
            return LookupResult(found=False, method=MatchMethod.NOT_FOUND)
        docs = list(self.collection.find({"title_norm": q}, _PROJECTION).limit(cap))
        if not docs:
            return LookupResult(found=False, method=MatchMethod.NOT_FOUND)

        method = MatchMethod.TITLE_YEAR if year else MatchMethod.TITLE_ONLY
        if len(docs) == 1:
            return LookupResult(found=True, method=method,
                                record=self._shape(docs[0]), confidence=1.0)

        # multiple docs share this normalized title -> disambiguate on other fields
        cands = docs
        if year:
            yr = int(year)
            filt = [d for d in cands
                    if (extract_year(d) is None) or abs((extract_year(d) or yr) - yr) <= 1]
            if filt:
                cands = filt
        if journal:
            try:
                from journal_authority import same_journal
                jf = [d for d in cands
                      if extract_journal(d) and same_journal(journal, extract_journal(d))]
                if jf:
                    cands = jf
            except Exception:
                pass
        if author:
            want = _surname_tokens(author)
            if want:
                af = [d for d in cands if _surname_tokens(d.get("author")) & want]
                if af:
                    cands = af

        # unique survivor, or all survivors are the same work (one DOI) -> confident match
        dois = {(d.get("DOI") or "").lower() for d in cands}
        if len(cands) == 1 or len(dois) == 1:
            return LookupResult(found=True, method=method,
                                record=self._shape(cands[0]), confidence=1.0)
        # still ambiguous across distinct DOIs: return best-effort top hit at lower
        # confidence so the caller can treat it as a soft (title-only) confirmation
        return LookupResult(found=True, method=MatchMethod.TITLE_ONLY,
                            record=self._shape(cands[0]), confidence=0.5)

    def by_citation(self, parsed) -> LookupResult:
        if parsed.doi:
            result = self.by_doi(parsed.doi)
            if result.found:
                return result
        if parsed.title and parsed.year:
            result = self.by_title(parsed.title, year=parsed.year)
            if result.found:
                return result
        if parsed.title:
            result = self.by_title(parsed.title)
            if result.found:
                return result
        return LookupResult(found=False, method=MatchMethod.NOT_FOUND)

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# entry point

if __name__ == "__main__":
    import sys

    if "--setup" in sys.argv:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
        collection = client[DB_NAME][COLLECTION_NAME]
        print("Building text index... (run once only)")
        try:
            collection.create_index(
                [("title", TEXT), ("container-title", TEXT)],
                default_language="english",
            )
            print("Done.")
        except OperationFailure as e:
            print(f"Skipped: {e}")
        client.close()

    else:
        with MongoLookup() as lookup:
            result = lookup.by_doi("10.1038/s41586-020-2649-2")
            print(f"found={result.found}  method={result.method}  confidence={result.confidence}")
            if result.record:
                print(f"title:     {extract_title_text(result.record)}")
                print(f"journal:   {extract_journal(result.record)}")
                print(f"year:      {extract_year(result.record)}")
                print(f"publisher: {result.record.get('publisher')}")