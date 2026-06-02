"""
parse_refs.py — parse a reference section and look up each DOI via Crossref.

Usage:
    python3 parse_refs.py references.txt        # read from file
    python3 parse_refs.py                        # paste text, then press Ctrl+D
"""
import sys
import requests
sys.path.insert(0, ".")
from parser import parse_all_citations


def lookup_crossref(title: str, year: int = None) -> dict:
    """Query the Crossref API and return structured metadata for the top match.

    First tries with a +-1-year filter; if that returns nothing, retries without
    any date filter so papers with imprecise publication dates still resolve.
    """
    def _query(params):
        resp = requests.get(
            "https://api.crossref.org/works",
            params=params,
            timeout=10,
        )
        return resp.json()["message"]["items"]

    try:
        params = {"query.title": title, "rows": 3}
        if year:
            params["filter"] = f"from-pub-date:{year - 1},until-pub-date:{year + 1}"
        items = _query(params)

        # Fallback: drop the date filter entirely
        if not items and year:
            items = _query({"query.title": title, "rows": 3})

        if not items:
            return {}

        top = items[0]

        authors = []
        for a in top.get("author", []):
            given  = a.get("given", "")
            family = a.get("family", "")
            authors.append(f"{family}, {given}".strip(", "))

        year_found = None
        try:
            year_found = top["issued"]["date-parts"][0][0]
        except (KeyError, IndexError, TypeError):
            pass

        ct = top.get("container-title", [])
        return {
            "doi":       top.get("DOI"),
            "title":     top.get("title", [None])[0],
            "authors":   authors,
            "year":      year_found,
            "journal":   ct[0] if ct else None,
            "volume":    top.get("volume"),
            "issue":     top.get("issue"),
            "pages":     top.get("page"),
            "publisher": top.get("publisher"),
            "type":      top.get("type"),
            "score":     top.get("score"),
        }

    except Exception as e:
        print(f"  [API error: {e}]")
        return {}


# ── get the text ──────────────────────────────────────────────────────────
if len(sys.argv) > 1:
    path = sys.argv[1]
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        print(f"Reading from: {path}\n")
    except FileNotFoundError:
        print(f"Error: file not found: {path}")
        sys.exit(1)
else:
    print("Paste your reference section below, then press Ctrl+D (Mac/Linux) or Ctrl+Z + Enter (Windows):")
    print("-" * 60)
    text = sys.stdin.read()
    print("-" * 60 + "\n")

# ── parse + lookup ────────────────────────────────────────────────────────
citations = parse_all_citations(text)
print(f"Found {len(citations)} citations — looking up DOIs via Crossref...\n")

print("=" * 70)
for i, c in enumerate(citations, 1):
    print(f"\n[{i}] PARSED ({c.style.value.upper()})")
    print(f"     Title:   {c.title}")
    authors_str = ", ".join(c.authors[:3]) + ("..." if len(c.authors) > 3 else "")
    print(f"     Authors: {authors_str}")
    print(f"     Year:    {c.year}   Journal: {c.journal}")

    if c.doi:
        # DOI was embedded in the citation text itself — no API call needed
        print(f"     DOI:     {c.doi}  (found in text)")
    elif c.title:
        meta = lookup_crossref(c.title, year=c.year)
        if meta.get("doi"):
            print(f"     CROSSREF MATCH  (score={meta.get('score', '-')})")
            print(f"     DOI:       {meta['doi']}")
            print(f"     Title:     {meta.get('title')}")
            match_authors = ", ".join(meta.get("authors", [])[:3])
            if len(meta.get("authors", [])) > 3:
                match_authors += "..."
            print(f"     Authors:   {match_authors}")
            print(f"     Year:      {meta.get('year')}   Journal: {meta.get('journal')}")
            print(f"     Volume:    {meta.get('volume')}   Issue: {meta.get('issue')}   Pages: {meta.get('pages')}")
            print(f"     Publisher: {meta.get('publisher')}")
        else:
            print("     DOI:       not found in Crossref")
    else:
        print("     DOI:       (could not look up — no title parsed)")
