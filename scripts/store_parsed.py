"""
Parse all OpenAlex sample PDFs and write every citation's structured fields
to a plain-text file (parsed_citations.txt).

Run from the scripts/ directory:
    python3 store_parsed.py
"""
import pathlib, sys, re
sys.path.insert(0, '.')
from parse_refs import extract_references_from_pdf
from parser import parse_all_citations

OUTPUT = 'parsed_citations.txt'
PDFS   = sorted(pathlib.Path('samples/openalex_pdfs').glob('**/*.pdf'))

lines = []
total_refs = 0

for pdf in PDFS:
    print(f'Processing {pdf.name} ...', flush=True)
    text = extract_references_from_pdf(str(pdf))
    refs = parse_all_citations(text)
    total_refs += len(refs)

    lines.append('=' * 70)
    lines.append(f'PDF: {pdf.name}  ({len(refs)} refs)')
    lines.append('=' * 70)

    for i, c in enumerate(refs, 1):
        raw_display = c.raw.replace('\n', ' ').strip()
        lines.append(f'[{i}]')
        lines.append(f'  raw     : {raw_display[:120]}')
        lines.append(f'  authors : {"; ".join(c.authors) if c.authors else "—"}')
        lines.append(f'  title   : {c.title or "—"}')
        lines.append(f'  journal : {c.journal or "—"}')
        lines.append(f'  year    : {c.year or "—"}')
        lines.append(f'  volume  : {c.volume or "—"}')
        lines.append(f'  pages   : {c.pages or "—"}')
        lines.append(f'  doi     : {c.doi or "—"}')
        lines.append('')

    lines.append('')

lines.append(f'TOTAL: {len(PDFS)} PDFs, {total_refs} citations parsed.')

result = '\n'.join(lines)
with open(OUTPUT, 'w') as f:
    f.write(result + '\n')

print(f'\nDone. {total_refs} citations written to {OUTPUT}')
