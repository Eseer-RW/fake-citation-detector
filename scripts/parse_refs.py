"""
parse_refs.py — parse a reference section from a text file or stdin.

Usage:
    python3 parse_refs.py references.txt        # read from file
    python3 parse_refs.py                        # paste text, then press Ctrl+D
"""
import sys
sys.path.insert(0, ".")
from parser import parse_all_citations

# ── get the text ──────────────────────────────────────────────────────────
if len(sys.argv) > 1:
    # filename passed as argument
    path = sys.argv[1]
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        print(f"Reading from: {path}\n")
    except FileNotFoundError:
        print(f"Error: file not found: {path}")
        sys.exit(1)
else:
    # no filename — read from stdin so the user can paste and press Ctrl+D
    print("Paste your reference section below, then press Ctrl+D (Mac/Linux) or Ctrl+Z + Enter (Windows):")
    print("-" * 60)
    text = sys.stdin.read()
    print("-" * 60 + "\n")

# ── parse ─────────────────────────────────────────────────────────────────
results = parse_all_citations(text)
print(f"Found {len(results)} citations\n")
print(f"{'#':<4} {'Style':<12} {'Year':<6} {'Title'}")
print("-" * 80)
for i, r in enumerate(results, 1):
    title = r.title or "(no title extracted)"
    if len(title) > 55:
        title = title[:52] + "..."
    print(f"{i:<4} {r.style.value:<12} {str(r.year or ''):<6} {title}")

print()
print("DETAILED RESULTS")
print("=" * 80)
for i, r in enumerate(results, 1):
    authors_str = ", ".join(r.authors[:3]) + ("..." if len(r.authors) > 3 else "")
    doi_str = r.doi or "(none in text)"
    print(f"\n[{i}] {r.style.value.upper()}  year={r.year}")
    print(f"     Title:   {r.title}")
    print(f"     Authors: {authors_str}")
    print(f"     Journal: {r.journal}   Vol: {r.volume}  Issue: {r.issue}  Pages: {r.pages}")
    print(f"     DOI:     {doi_str}")
