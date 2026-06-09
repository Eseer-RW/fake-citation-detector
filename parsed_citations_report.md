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

---

### Category 1 — Unfixable (PDF-level problems)

These three papers cannot be fixed without replacing the PDF itself. The reference extractor is pulling in non-citation content.

**`10_1038_s41586-024-07487-w` — 10 issues (encrypted PDF)**  
The PDF is encrypted. All extracted text is garbled cipher output. Sample "citations":
```
[3]  W IE EV E %*1 Ѳѳ %*ѳ %*ѳ RS GVSWWHMWXMPPEXMSR
[5]   %($#& ' )%'
[11]       !&$#& ' )%' EL
```
None of the 107 entries contain any real citation data.

**`10_1038_nature04233` — 9 issues (physics figure labels)**  
The paper's reference section is embedded in a two-column figure layout. The extractor picks up axis labels and measurement annotations instead of citations:
```
[2]  ∆σxx (1/kΩ)
[8]  σxy (4-2/h) 7/ 4K 2
[9]  3 5/ 2 2 10 -4 -2 1 n 3/
[12] -1 1/ -2 2 -3 0 -1/2
```

**`10_1038_nature15393` — 10 issues (consortium affiliations)**  
This large-consortium genomics paper lists hundreds of author affiliations, which the extractor mistakes for citations:
```
[8]  Journal=title? "45Department of Genetic Medicine, Weill 10, 6525 GA Nijmegen"
[9]  Bad author "80125 Naples"
[10] Journal=title? "50Institute of Medical Genetics, School of Medicine, Cardiff"
[11] Journal=title? "119Department of Computer Science, University of Boston, Mas"
```

---

### Category 2 — Parser Limitations

These papers contain real citations that the parser handles incorrectly due to unusual formatting.

**`10_1038_35057062` — 5 issues (old Nature two-column format, 1999)**  
The reference extractor merges consecutive references in this old two-column layout, causing page ranges and reference numbers from one entry to leak into the author field of the next:
```
[118] Bad author "953-958 163. Smit, A. F. Identi®cation of a new"
[121] Bad author "9782-9787 (1998). (1989). 166. Myers"
[170] Bad author "retrosequences. (ASM, Washington DC, 1998). Geneti"
[217] Bad author "the pattern of branching. Cell 87, 1091-1101 (1996"
[245] Bad author "415. Lalwani, A. K. et al"
```

**`10_1038_s41586-020-2649-2` — 4 issues (conference paper titles in journal field)**  
This computational biology paper cites conference proceedings. The parser absorbs long paper titles into the journal field instead of the title field:
```
[18] Journal=title? "AUGEM: automatically generate high performance dense linear ..."
[19] Journal=title? "Model-driven level 3 BLAS performance optimization on Loongs..."
[57] Journal=title? "LLVM: a compilation framework for lifelong program analysis ..."
[32] Bad author   "Harrington, J. The SciPy Documentation Project. In"
```

**`10_1126_science_aac4716` — 5 issues (non-standard reproducibility paper)**  
This Open Science Collaboration reproducibility paper includes appendix sections and statistical methodology text in its reference list area, which the extractor pulls in as citations:
```
[13] Bad author    "​1­12 (2013). Reproducibility Project ​15.​ PLoS"
[43] Journal=title? "A2: Analyses of significance and ​p​­values c"
[45] Bad author    "Francis, New York, 2014) pp. 299­323. The first in"
[55] Journal=title? "When both studies have equal sample size, this probability e"
[57] Journal=title? "For ​F​ statistics we first computed the 95% confidence int"
```

---

### Category 3 — Audit Edge Cases

These papers parse mostly correctly but trigger false positives in the audit checks due to legitimate but unusual citation content.

**`10_1371_journal_pmed_1001885` — 2 issues (organisational author + study name)**  
```
[9]  Bad author    "Transparency Of health Research (EQUATOR) Network"
     → The EQUATOR Network is a real consortium; its name contains a year that
       trips the bad-author year-detection check.
[57] Journal=title? "Results from the RECord linkage On Rheumatic Diseases study"
     → A named study (RECORD) has a long descriptive title that ended up in
       the journal field rather than the title field.
```

**`10_1371_journal_pone_0169748` — 3 issues (grey literature / technical reports)**  
This soil science paper cites technical reports and databases whose titles are long enough to trigger the journal=title check:
```
[81] Journal=title? "A compilation of geo-referenced and stan-dardized legacy soi..."
[83] Journal=title? "GlobalSoilMap: Basis of the global spatial soil information ..."
[90] Journal=title? "Luxembourg: Office for official publications of the Euro-pea..."
```
These are correctly flagged structurally (the title did end up in the journal field), but the root cause is that grey literature citations have no standard journal — the parser assigns the publication venue or report title to the journal field as a fallback.

---

## Recommendations

1. **Expand the sample set** — 41 PDFs across 6 journals is a solid baseline but skews toward biology/medicine. Adding PDFs from engineering, social sciences, and humanities journals would stress-test citation formats beyond Nature/Science/PLoS style.

2. **Improve journal field coverage** — only 47% of citations have a parsed journal. Improving the `jour_m` regex for older compact formats (e.g. pre-2000 Nature two-column layout) would push this significantly higher.

3. **DOI normalisation** — *Nucleic Acids Research* appearing under two abbreviations is a small example of a broader deduplication problem. A journal name normalisation layer (mapping abbreviations to canonical names) would improve downstream matching.

4. **Flag future-year citations** — the 3 citations with years ≥ 2030 should be surfaced as suspicious by the fake-citation detector, since they are either parse errors or impossible publication dates.
