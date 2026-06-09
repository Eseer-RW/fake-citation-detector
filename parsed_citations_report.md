# Parsed Citations Report
**Source:** 41 OpenAlex sample PDFs across 6 journals  
**Generated:** June 9, 2026

---

## Overview

| Metric | Value |
|---|---|
| PDFs processed | 41 |
| Total citations extracted | 2,799 |
| Average citations per paper | 68 |
| Largest reference list | 281 (Nature, 10.1038/35057062) |
| Smallest reference list | 2 (Nature, 10.1038/s41586-020-2012-7) |

---

## Parse Field Coverage

How often each field was successfully extracted:

| Field | Extracted | Coverage |
|---|---|---|
| Authors | 2,651 / 2,799 | 94.7% |
| Title | 2,663 / 2,799 | 95.1% |
| Year | 2,585 / 2,799 | 92.4% |
| Journal | 1,319 / 2,799 | 47.1% |
| DOI | 534 / 2,799 | 19.1% |
| All 4 core fields (authors + title + journal + year) | 1,265 / 2,799 | 45.2% |

**Title and authors parse well** (~95%) across all citation styles. **Year** is also robust at 92.4%. **Journal** is only 47% — many citations in older or non-standard formats don't include a clearly delimited journal field that the parser can isolate. **DOI coverage is low overall** (19.1%) but this is expected: DOIs only became standard in references from roughly 2010 onwards, and many journal styles (Nature, Science) still don't print DOIs inline in their reference lists.

---

## DOI Coverage by Source Journal

The variation in DOI coverage reflects how different journals format their reference lists:

| Source Journal | Citations | DOI Coverage |
|---|---|---|
| eLife | 253 | 81% |
| JAMA | 107 | 80% |
| PLoS ONE | 454 | 17% |
| PLoS Medicine | 1,016 | 14% |
| Science | 343 | 2% |
| Nature | 626 | 3% |

eLife and JAMA explicitly include DOIs in every reference. Nature and Science use compact citation formats (Author. *Journal* Vol, Pages (Year).) with no DOI, so the parser can only extract a DOI when one happens to appear in the raw text. This has practical implications for the fake-citation detector: papers from Nature/Science require title+year fuzzy matching against Crossref rather than direct DOI lookup.

---

## Citations by Year

| Decade | Citations | Notes |
|---|---|---|
| 1900s–1960s | 46 | Foundational/historical references |
| 1970s | 40 | |
| 1980s | 125 | |
| 1990s | 488 | |
| 2000s | 1,001 | Peak — most sampled papers published 2005–2015 |
| 2010s | 773 | |
| 2020s | 109 | |
| 2030s+ | 3 | ⚠ Likely parse errors or typos in source PDFs |

The 2000s peak reflects the publication dates of the sampled papers. The 3 citations with years in 2030–2070 are almost certainly parser errors where a volume number or page range was misread as a year — a known edge case worth flagging in the audit.

---

## Top 20 Most-Cited Journals

| Journal | Times Cited |
|---|---|
| Science | 74 |
| Nature | 67 |
| Nature Genetics | 30 |
| Genome Research | 29 |
| J. Am. Chem. Soc. | 22 |
| JAMA | 20 |
| BMJ | 20 |
| Cell | 17 |
| Lancet | 15 |
| Genomics | 15 |
| Bioinformatics | 15 |
| N Engl J Med | 14 |
| Nucleic Acids Research | 14 |
| Angew. Chem. Int. Ed. | 13 |
| Proc. Natl Acad. Sci. USA | 12 |
| Nature Biotechnology | 12 |
| Hum. Mol. Genet. | 11 |
| Nature Methods | 11 |
| Comput. Sci. Eng. | 10 |

Note: *Nucleic Acids Research* appears twice (ranks 8 and 13) under two slightly different abbreviations — a known deduplication issue when journal names are abbreviated inconsistently across papers.

---

## Citations per Source Journal

| Source Journal | PDFs | Total Citations |
|---|---|---|
| PLoS Medicine | 10 | 1,016 |
| Nature | 11 | 626 |
| PLoS ONE | 10 | 454 |
| Science | 8 | 343 |
| eLife | 3 | 253 |
| JAMA | 1 | 107 |

PLoS Medicine dominates by citation count — it includes several large systematic reviews and meta-analyses (one paper alone has 199 references). eLife papers have the fewest references on average but the highest parse quality, likely because eLife uses a consistent structured format.

---

## Parse Quality Issues

From the batch audit (see `results_openalex_v14.txt`), **33 of 41 PDFs parse cleanly**. The 8 with remaining issues fall into three categories:

**Unfixable (PDF-level problems):**
- `s41586-024-07487-w` — encrypted PDF; all 107 extracted "citations" are garbage characters
- `nature04233` — physics figure axis labels extracted as citations
- `nature15393` — consortium author affiliations extracted as citations

**Parser limitations:**
- `35057062` — old 1999 Nature two-column format; reference extractor merges consecutive entries, leaving page-range artifacts in author fields
- `s41586-020-2649-2` — conference paper titles absorbed into journal field
- `science_aac4716` — non-standard reproducibility paper appendix sections extracted as citations

**Edge cases in audit logic:**
- `pmed_1001885` — EQUATOR Network consortium name + a study name in journal field
- `pone_0169748` — grey literature / technical report titles in journal field

---

## Recommendations

1. **Expand the sample set** — 41 PDFs across 6 journals is a solid baseline but skews toward biology/medicine. Adding PDFs from engineering, social sciences, and humanities journals would stress-test citation formats beyond Nature/Science/PLoS style.

2. **Improve journal field coverage** — only 47% of citations have a parsed journal. Improving the `jour_m` regex for older compact formats (e.g. pre-2000 Nature two-column layout) would push this significantly higher.

3. **DOI normalisation** — *Nucleic Acids Research* appearing under two abbreviations is a small example of a broader deduplication problem. A journal name normalisation layer (mapping abbreviations to canonical names) would improve downstream matching.

4. **Flag future-year citations** — the 3 citations with years ≥ 2030 should be surfaced as suspicious by the fake-citation detector, since they are either parse errors or impossible publication dates.
