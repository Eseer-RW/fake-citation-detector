"""Run the audit on all OpenAlex PDFs and write results to results_openalex_v2.txt."""
import sys, re
sys.path.insert(0, '.')
from parse_refs import extract_references_from_pdf
from parser import parse_all_citations

import pathlib

pdfs = sorted(pathlib.Path('samples/openalex_pdfs').glob('**/*.pdf'))

def audit(pdf):
    issues = []
    text = extract_references_from_pdf(str(pdf))
    refs = parse_all_citations(text)
    label = pdf.name

    for i, c in enumerate(refs, 1):
        raw = c.raw.replace('\n', ' ')

        if c.title and re.fullmatch(r'\d{4}', c.title.strip()):
            issues.append('[%d] Title=year "%s": %s' % (i, c.title, raw[:80]))

        if c.title is None and len(c.raw.strip()) > 40:
            # Exception 1: citation is a fragment continuation — it ends with ","
            # meaning the reference extractor split the entry mid-sentence.
            # Exception 2: NLM book chapter where "In:" is present AND the year was
            # found — the parser detected the chapter structure but couldn't extract
            # the title (e.g. because "In:" is preceded by a floating chapter number
            # rather than a period: "methods (Chapter 139. Smith... 1.4.) In: Book").
            # Exception 3: no authors extracted — the citation is likely a note or
            # supplementary text accidentally included in the reference list, not a
            # real citation with a missing title.
            if (c.authors
                    and not raw.rstrip().endswith(',')
                    and not (c.year and re.search(r'\bIn:', raw))):
                issues.append('[%d] Title=None: %s' % (i, raw[:80]))

        for a in c.authors:
            # Year pattern: only match 19xx/20xx not preceded by # - / (grant IDs)
            # and not followed by - or alphanumeric (embedded in identifiers like
            # "COVID-19" or "#2006-NE-1464" should not fire).
            # Leading-digit check requires 3+ digits: this catches page-range artifacts
            # ("953-958 Smith") and year-prefixes ("2014 Author") but not 2-digit
            # measurements like "50-µm thick Cu foils" from supplementary text.
            if re.search(
                r'(?<![#\-/])\b(?:19|20)\d{2}\b(?![-\d\w])|^\d{3,}|\bdoi:', a, re.I
            ):
                # Guard: if the raw citation text starts with a lowercase letter it is
                # almost certainly a continuation fragment (the reference extractor split
                # one citation across two entries).  Fragments don't represent real
                # citations so suppress bad-author noise for them.
                if raw and raw[0].islower():
                    break
                # Guard: year is part of a disease/outbreak name (e.g. "2019 Novel
                # Coronavirus", "COVID-19", "disease 2019") — not a parsing error.
                if re.search(r'(?:Novel Coronavirus|COVID|SARS|disease\s+(?:19|20)\d{2}|(?:19|20)\d{2}\s+Novel)', a, re.I):
                    break
                # Guard: author looks like a named network/consortium ending in a
                # recognised organisational suffix — year is part of the group name,
                # not a stray year from a bad parse.
                if re.match(r'^(?:[A-Z][A-Za-z]* )+(?:Network|Initiative|Project|Collaboration|Group|Team|Registry|Consortium)\s*$', a):
                    break
                # Guard: author is just a page number + parenthesised year
                # (e.g. "47 (1983)") — happens when old narrative-style refs
                # ("For an early review, see Author, Journal Vol, Page (Year)")
                # are parsed and the page/year land in the author field.
                if re.match(r'^\d+\s+\(\d{4}\)\s*$', a):
                    break
                issues.append('[%d] Bad author "%s": %s' % (i, a[:50], raw[:60]))
                break

        if c.journal and len(c.journal.split()) > 8:
            # Exception: legitimate conference/proceedings venue names are often long
            # but not parsing errors — e.g. "Proceedings of the AAAI Workshop on..." or
            # "In: Proceedings of the Fourteenth International Conference on...".
            # These start with "In:", "In Proc", "Proc. of", "Proceedings", etc.
            # Dehyphenate first so PDF line-break artifacts like "Pro-ceedings"
            # still match (the hyphen comes from word-wrap in two-column PDFs).
            journal_norm = re.sub(r'([A-Za-z])-([A-Za-z])', r'\1\2', c.journal)
            # Lowercase-starting strings are almost certainly text fragments that ended
            # up in the journal field (e.g. "elegans the relatively small size of the
            # system") rather than real journal names (which always start with a capital
            # letter when they are legitimately long).
            is_lowercase_fragment = c.journal[0].islower()
            if (not is_lowercase_fragment
                    and not re.match(
                        r'(?i)^(?:in[\s:]+|proc(?:[-\s]?eedings)?(?:\s+of)?\s+|proceedings\s+)',
                        journal_norm
                    )
                    # Exception: some journals have official long names of the form
                    # "Short name: journal of the Society for X" — not a title mismatch.
                    and not re.search(r'(?i):\s+journal\s+of\b', journal_norm)
                    # Exception: reagent/materials-list entries contain company names
                    # (Co.,Ltd  Inc.  Corp.  GmbH) — not a real citation journal field.
                    and not re.search(r'(?i)\b(?:Co\.,\s*Ltd|Inc\.|Corp\.|GmbH|Biotech|Bioscience)\b', journal_norm)):
                issues.append('[%d] Journal=title? "%s": %s' % (i, c.journal[:60], raw[:60]))

        if c.doi and re.search(r'\(\d*$', c.doi):
            issues.append('[%d] DOI truncated "%s": %s' % (i, c.doi, raw[:60]))

        if not c.year and not c.authors and len(c.raw.strip()) < 50:
            # Exception 1: partial author-list fragments from split citations look like
            # noise (no year, no parsed authors, short text) but are actually the tail
            # of a legitimate citation whose beginning is in the preceding entry.
            # Recognisable pattern: "Lastname Initials, ..." e.g. "Colhoun HM, McKeigue".
            # Exception 2: sentence-like text (long word ≥5 chars followed by lowercase
            # continuation) is supplementary / methods text, not a citation stub.
            # e.g. "Electrical measurements were performed with a S..."
            # Exception 3: heading-like text — all words start with a capital letter
            # (possibly joined by lowercase connectives like "and", "of", "in").
            # e.g. "Statistical Analyses", "Sampling Frame and Selection Biases"
            if (not re.search(r'^[A-Z][a-z]+\s+[A-Z]+,', raw)
                    and not re.match(r'^[A-Z][a-z]{4,}\s+[a-z]', raw)
                    and not re.match(
                        r'^(?:[A-Z][A-Za-z]+)(?:\s+(?:and|of|in|the|for|or|on|with|by)\s+[A-Z][A-Za-z]+|\s+[A-Z][A-Za-z]+)*$',
                        raw.strip()
                    )):
                issues.append('[%d] Noise? (no year/authors): %s' % (i, raw[:60]))

    return label, len(refs), issues


output_lines = []
ok_count = 0
issue_count = 0

for pdf in pdfs:
    label, count, issues = audit(pdf)
    line = '%s  (%d refs)' % (label, count)
    output_lines.append(line)
    if issues:
        issue_count += 1
        for iss in issues[:10]:
            output_lines.append('  !! ' + iss)
    else:
        ok_count += 1
        output_lines.append('  OK')
    output_lines.append('')

summary = '=== SUMMARY: %d/%d PDFs clean, %d with issues ===' % (
    ok_count, len(pdfs), issue_count)
output_lines.append(summary)

result = '\n'.join(output_lines)
print(result)

with open('results_openalex_v14.txt', 'w') as f:
    f.write(result + '\n')

print('\nSaved to results_openalex_v14.txt')
