"""
Test pipeline using real Crossref records sampled from the data files.
Reads directly from the tar — no need to extract first.

Run with:
    python3 scripts/test_real_citations.py
"""

import gzip
import tarfile
from pathlib import Path
from itertools import cycle

import orjson

from parser import parse_citation, CitationStyle
from mongo_lookup import MongoLookup, MatchMethod, extract_title_text, extract_year


# config

TAR_PATH = str(__import__("pathlib").Path.home() / "crossref" / "data" / "March_2026_Public_Data_File_from_Crossref.tar")

TEST_FILES = [
    "March_2026_Public_Data_File_from_Crossref/222.jsonl.gz",
    "March_2026_Public_Data_File_from_Crossref/6322.jsonl.gz",
    "March_2026_Public_Data_File_from_Crossref/16638.jsonl.gz",
    "March_2026_Public_Data_File_from_Crossref/35219.jsonl.gz",
    "March_2026_Public_Data_File_from_Crossref/19309.jsonl.gz",
    "March_2026_Public_Data_File_from_Crossref/23033.jsonl.gz",
    "March_2026_Public_Data_File_from_Crossref/4500.jsonl.gz",
    "March_2026_Public_Data_File_from_Crossref/29004.jsonl.gz",
    "March_2026_Public_Data_File_from_Crossref/28275.jsonl.gz",
]

RECORDS_PER_FILE = 3   # how many records to sample from each file
STYLES = cycle(["apa", "mla", "chicago", "vancouver", "ieee"])

# produce plain-text citation from raw Crossref record

def _get_authors(record: dict) -> list[dict]:
    return record.get("author") or []


def _apa_author(a: dict) -> str:
    family = a.get("family", "")
    given = a.get("given", "")
    initials = " ".join(f"{p[0]}." for p in given.split() if p) if given else ""
    return f"{family}, {initials}".strip(", ")


def _mla_author(a: dict) -> str:
    family = a.get("family", "")
    given = a.get("given", "")
    return f"{family}, {given}".strip(", ")


def _vancouver_author(a: dict) -> str:
    family = a.get("family", "")
    given = a.get("given", "")
    initials = "".join(p[0] for p in given.split() if p) if given else ""
    return f"{family} {initials}".strip()


def _ieee_author(a: dict) -> str:
    family = a.get("family", "")
    given = a.get("given", "")
    initials = " ".join(f"{p[0]}." for p in given.split() if p) if given else ""
    return f"{initials} {family}".strip()


def _get_year(record: dict) -> str:
    try:
        return str(record["issued"]["date-parts"][0][0])
    except (KeyError, IndexError, TypeError):
        return "n.d."


def _get_title(record: dict) -> str:
    t = record.get("title")
    if isinstance(t, list) and t:
        return t[0]
    return str(t or "Untitled")


def _get_journal(record: dict) -> str:
    ct = record.get("container-title")
    if isinstance(ct, list) and ct:
        return ct[0]
    return str(ct or "")


def format_apa(record: dict) -> str:
    authors = _get_authors(record)
    if not authors:
        author_str = "Unknown"
    elif len(authors) == 1:
        author_str = _apa_author(authors[0])
    else:
        parts = [_apa_author(a) for a in authors[:-1]]
        author_str = ", ".join(parts) + ", & " + _apa_author(authors[-1])

    year    = _get_year(record)
    title   = _get_title(record)
    journal = _get_journal(record)
    volume  = record.get("volume", "")
    issue   = record.get("issue", "")
    page    = record.get("page", "")
    doi     = record.get("DOI", "")

    vol_issue = f"{volume}({issue})" if volume and issue else volume or ""
    parts = [p for p in [journal, vol_issue, page] if p]

    return (
        f"{author_str} ({year}). {title}. "
        f"{', '.join(parts)}. "
        f"https://doi.org/{doi}"
    )


def format_mla(record: dict) -> str:
    authors = _get_authors(record)
    if not authors:
        author_str = "Unknown"
    elif len(authors) == 1:
        author_str = _mla_author(authors[0])
    else:
        author_str = _mla_author(authors[0]) + ", et al."

    title   = _get_title(record)
    journal = _get_journal(record)
    volume  = record.get("volume", "")
    issue   = record.get("issue", "")
    year    = _get_year(record)
    page    = record.get("page", "")
    doi     = record.get("DOI", "")

    parts = [p for p in [
        f"vol. {volume}" if volume else "",
        f"no. {issue}" if issue else "",
        year,
        f"pp. {page}" if page else "",
    ] if p]

    return (
        f'{author_str}. "{title}." '
        f"{journal}, {', '.join(parts)}. "
        f"https://doi.org/{doi}"
    )


def format_chicago(record: dict) -> str:
    authors = _get_authors(record)
    if not authors:
        author_str = "Unknown"
    elif len(authors) == 1:
        a = authors[0]
        author_str = f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
    else:
        first = authors[0]
        rest  = authors[1:]
        first_str = f"{first.get('family', '')}, {first.get('given', '')}".strip(", ")
        rest_str  = " and ".join(
            f"{a.get('given', '')} {a.get('family', '')}".strip()
            for a in rest
        )
        author_str = f"{first_str}, and {rest_str}"

    title   = _get_title(record)
    journal = _get_journal(record)
    volume  = record.get("volume", "")
    issue   = record.get("issue", "")
    year    = _get_year(record)
    page    = record.get("page", "")
    doi     = record.get("DOI", "")

    issue_str = f", no. {issue}" if issue else ""
    page_str  = f": {page}" if page else ""

    return (
        f'{author_str}. "{title}." '
        f"{journal} {volume}{issue_str} ({year}){page_str}. "
        f"https://doi.org/{doi}"
    )


def format_vancouver(record: dict) -> str:
    authors = _get_authors(record)
    if not authors:
        author_str = "Unknown"
    else:
        author_str = ", ".join(_vancouver_author(a) for a in authors[:6])
        if len(authors) > 6:
            author_str += ", et al."

    title   = _get_title(record)
    journal = _get_journal(record)
    year    = _get_year(record)
    volume  = record.get("volume", "")
    issue   = record.get("issue", "")
    page    = record.get("page", "")
    doi     = record.get("DOI", "")

    vol_issue = f"{volume}({issue})" if volume and issue else volume or ""
    page_str  = f":{page}" if page else ""

    return (
        f"{author_str}. {title}. "
        f"{journal}. {year};{vol_issue}{page_str}. "
        f"https://doi.org/{doi}"
    )


def format_ieee(record: dict) -> str:
    authors = _get_authors(record)
    if not authors:
        author_str = "Unknown"
    elif len(authors) <= 3:
        author_str = " and ".join(_ieee_author(a) for a in authors)
    else:
        author_str = _ieee_author(authors[0]) + " et al."

    title   = _get_title(record)
    journal = _get_journal(record)
    volume  = record.get("volume", "")
    issue   = record.get("issue", "")
    page    = record.get("page", "")
    year    = _get_year(record)
    doi     = record.get("DOI", "")

    parts = [p for p in [
        f"vol. {volume}" if volume else "",
        f"no. {issue}" if issue else "",
        f"pp. {page}" if page else "",
        year,
    ] if p]

    return (
        f'{author_str}, "{title}," '
        f"{journal}, {', '.join(parts)}. "
        f"https://doi.org/{doi}"
    )


FORMATTERS = {
    "apa":       format_apa,
    "mla":       format_mla,
    "chicago":   format_chicago,
    "vancouver": format_vancouver,
    "ieee":      format_ieee,
}


# sampling

def is_usable(record: dict) -> bool:
    """Only test records that have enough fields to form a valid citation."""
    return bool(
        record.get("DOI")
        and record.get("title")
        and record.get("author")
        and record.get("issued")
    )


def sample_all_records(tar_path: str, member_names: list[str], n: int) -> dict[str, list[dict]]:
    """Open the tar once and sample n records from each requested member."""
    results = {m: [] for m in member_names}
    needed = set(member_names)

    with tarfile.open(tar_path) as tar:
        for member in tar.getmembers():
            if member.name not in needed:
                continue
            f = tar.extractfile(member)
            if f is None:
                continue
            with gzip.open(f, "rb") as gz:
                for line in gz:
                    if len(results[member.name]) >= n:
                        break
                    try:
                        record = orjson.loads(line)
                        if is_usable(record):
                            results[member.name].append(record)
                    except Exception:
                        continue
            needed.discard(member.name)
            if not needed:
                break

    return results


# running tests

def run():
    print("Starting...")
    passed = 0
    failed = 0
    errors = []

    print("Reading records from tar...")
    all_records = sample_all_records(TAR_PATH, TEST_FILES, RECORDS_PER_FILE)
    print("Done reading. Running tests...")

    with MongoLookup() as lookup:
        for member_name in TEST_FILES:
            records = all_records[member_name]
            file_label = Path(member_name).name
            
            if not records:
                print(f"\n[{file_label}] No usable records found, skipping.")
                continue

            print(f"\n{'='*60}")
            print(f"FILE: {file_label}  ({len(records)} records)")
            print(f"{'='*60}")

            for record in records:
                style = next(STYLES)
                formatter = FORMATTERS[style]
                doi = record["DOI"].lower()
                expected_title = _get_title(record)
                expected_year  = _get_year(record)

                citation_str = formatter(record)
                parsed = parse_citation(citation_str)
                result = lookup.by_citation(parsed)

                doi_ok   = parsed.doi == doi
                year_ok  = str(parsed.year) == expected_year
                found_ok = result.found
                title_ok = (
                    extract_title_text(result.record) == expected_title
                    if result.record else False
                )

                ok = doi_ok and year_ok and found_ok and title_ok
                status = "PASS" if ok else "FAIL"
                if ok:
                    passed += 1
                else:
                    failed += 1
                    errors.append({
                        "file": file_label,
                        "style": style,
                        "doi": doi,
                        "doi_ok": doi_ok,
                        "year_ok": year_ok,
                        "found_ok": found_ok,
                        "title_ok": title_ok,
                    })

                print(f"\n  [{status}] {style.upper()}")
                print(f"  DOI:    {doi}")
                print(f"  Title:  {expected_title[:70]}")
                print(f"  Year:   {expected_year}")
                print(f"  Citation: {citation_str[:100]}...")
                print(f"  Parser  → doi={'OK' if doi_ok else 'FAIL'} | year={'OK' if year_ok else 'FAIL'}")
                print(f"  Lookup  → found={'OK' if found_ok else 'FAIL'} | title={'OK' if title_ok else 'FAIL'}")

    print(f"\n{'='*60}")
    print(f"RESULTS:  {passed} passed  |  {failed} failed")
    print(f"{'='*60}")

    if errors:
        print("\nFailed cases:")
        for e in errors:
            print(f"  {e['file']} [{e['style']}] doi={e['doi']}")
            print(f"    doi_ok={e['doi_ok']} year_ok={e['year_ok']} "
                  f"found_ok={e['found_ok']} title_ok={e['title_ok']}")


if __name__ == "__main__":
    run()