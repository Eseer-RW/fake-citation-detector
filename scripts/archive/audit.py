import sys, re
sys.path.insert(0, '.')
from parse_refs import extract_references_from_pdf
from parser import parse_all_citations

pdfs = [
    'samples/openalex_pdfs/eLife/10_7554_elife_13410.pdf',
    'samples/openalex_pdfs/eLife/10_7554_elife_27041.pdf',
    'samples/openalex_pdfs/eLife/10_7554_elife_65088.pdf',
    'samples/openalex_pdfs/JAMA/10_1001_jama_2020_12839.pdf',
    'samples/openalex_pdfs/Nature/10_1038_35057062.pdf',
    'samples/openalex_pdfs/Nature/10_1038_nature04233.pdf',
    'samples/openalex_pdfs/Nature/10_1038_nature04235.pdf',
    'samples/openalex_pdfs/Nature/10_1038_nature11247.pdf',
    'samples/openalex_pdfs/PLoS_Medicine/10_1371_journal_pmed_0020073.pdf',
    'samples/openalex_pdfs/PLoS_Medicine/10_1371_journal_pmed_1001885.pdf',
    'samples/openalex_pdfs/PLoS_Medicine/10_1371_journal_pmed_1003583.pdf',
    'samples/openalex_pdfs/PLoS_ONE/10_1371_journal_pone_0009490.pdf',
    'samples/openalex_pdfs/PLoS_ONE/10_1371_journal_pone_0019379.pdf',
]

def audit(pdf):
    issues = []
    text = extract_references_from_pdf(pdf)
    refs = parse_all_citations(text)
    label = pdf.split('/')[-1]

    for i, c in enumerate(refs, 1):
        raw = c.raw.replace('\n', ' ')

        # 1. Title is bare year
        if c.title and re.fullmatch(r'\d{4}', c.title.strip()):
            issues.append('[%d] Title=year "%s": %s' % (i, c.title, raw[:80]))

        # 2. Title is None but citation has real content
        if c.title is None and len(c.raw.strip()) > 40:
            issues.append('[%d] Title=None: %s' % (i, raw[:80]))

        # 3. Author contains a year, digit-start, or doi
        for a in c.authors:
            if re.search(r'\b\d{4}\b|^\d|\bdoi:', a, re.I):
                issues.append('[%d] Bad author "%s": %s' % (i, a[:50], raw[:60]))
                break

        # 4. Journal has >8 words (looks like a title was put there)
        if c.journal and len(c.journal.split()) > 8:
            issues.append('[%d] Journal=title? "%s": %s' % (i, c.journal[:60], raw[:60]))

        # 5. Truncated DOI (parenthesis inside doi)
        if c.doi and re.search(r'\(\d*$', c.doi):
            issues.append('[%d] DOI truncated "%s": %s' % (i, c.doi, raw[:60]))

        # 6. Noise block (no year, no authors, short text)
        if not c.year and not c.authors and len(c.raw.strip()) < 50:
            issues.append('[%d] Noise? (no year/authors): %s' % (i, raw[:60]))

    return label, len(refs), issues


for pdf in pdfs:
    label, count, issues = audit(pdf)
    print('%s  (%d refs)' % (label, count))
    if issues:
        for iss in issues[:10]:
            print('  !! ' + iss)
    else:
        print('  OK')
    print()
