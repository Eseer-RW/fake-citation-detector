# Citation Verification Report

**Project:** Detection and measurement of fabricated citations in arXiv preprints
**Status:** Study concluded — final results (rewritten 2026-08-18; the previous chronological report, sections 1–22 through July 22, is preserved at `citation_verification_report.md.bak_20260818_preRewrite`)
**Corpus:** 2.0M paper-samples over ~1.7M distinct arXiv papers, 2007–2026 · 80M+ references processed · 1,900+ flagged citations hand/web-verified

---

## Executive summary

1. **The published claim of a large post-ChatGPT fabrication surge (Zhao et al., +0.39pp) is rejected.** On 9.55M DOI-bearing references — the parse-independent arbiter — the post-2022 excess is at most +0.23pp under *any* baseline form, and its sign flips with the analyst's choice of baseline: the signature of an artifact, not an effect.
2. **Fabricated citations are nonetheless real and verified: 39 specimens in hand.** 2.5% of refined fabrication candidates are genuine fabrications (25/1000 verified; 95% CI 1.7–3.7%), giving **~5,400 fabricated citations in our 2023–25 sample** (CI 3,700–8,000) — **≈1 in 1,000 references** on average.
3. **The phenomenon is LLM-era-specific, verified directly:** 0 of 300 refined pre-LLM (2019–21) candidates were fabricated (expected ~7.5 if eras were equal; **p ≈ 5×10⁻⁴**).
4. **It is accelerating:** verified fabrication fractions by citing year are 1.4% (2023), 1.2% (2024), **5.3% (2025)**, 4.1% (2026H1) — a ~4× jump in 2025 (p ≈ 2×10⁻⁴), sustained into 2026. Current per-reference rate ≈ **1 in 550–800**.
5. **It is concentrated in computer science** (+17% rise in candidate rate; physics flat, math/statistics declining) — the fields act as an internal control: an instrument artifact would move all of them.
6. **Fabrications are propagating:** one specimen's invented title now exists as a phantom Google Scholar entry with two propagated citations; another phantom citation has been repeated verbatim by a second 2026 paper.

All headline numbers replicate across two independent samples (v8: 1.07M papers 2007–26; v8b: 934k papers 2019–26) to within 0.02pp.

---

## 1. Data and scale

| Run | Papers | Refs | Coverage | Role |
|---|---|---|---|---|
| v2–v6 (June–Aug) | 9k–17k | 0.4–0.6M | pilots | method development; documented in the archived report |
| **v7** | 208,656 | 8.06M | 2009–2026, K=1000/mo | first full-scale run; not-found decomposition |
| **v8** | 1,072,606 | 41.1M | 2007–2026, K=5000/mo | definitive run with upgraded detector |
| **v8b** | 934,333 | 42M | 2019–2026, K=12000/mo | independent LLM-era replication |

Pipeline: monthly arXiv v1 papers sampled from the GROBID-TEI corpus → reference extraction → multi-phase **exact** matching (per project directive: no fuzzy matching in the detector) against local indexes: OpenAlex (486M works), Crossref biblio (179M), Crossref title (66M+), with Solr fallback. Local indexes in RAM (tmpfs/page cache) give ~0.5s/paper; a 1M-paper run completes overnight on 16 single-worker shards.

---

## 2. The detector: three channels, ground-truth validated

A fabricated citation can present three ways, and each needs a different detector:

| Channel | Fabrication type | Mechanism | Validated performance |
|---|---|---|---|
| `not_found` → `fab_candidate` | fully invented work | exact-match failure + classification of the failure reason | candidate tier is 2.5% true-fab after refinement |
| `author_hijack` | **real title, invented authors** | matched work's author must appear in the citation's raw text | recall 57%, FPR 6.9% |
| `title_hijack` | **real DOI/identifier, invented title** | cited title vs. resolved work's title, bidirectional containment | verified true-fraction ~1.7% of flags |

**Ground-truth validation** against two external, human-confirmed fabrication datasets:

- **GPTZero NeurIPS-2025** (100 verified fabrications in 51 papers; 68 mappable to arXiv): combined recall **85%** (36 caught as not-found + 9 by author-hijack; 8 missed; 15 existed only in camera-ready versions — structurally invisible to any arXiv study).
- **CiteTracer / ICLR-2026 desk-rejections** (807 chair-confirmed fabricated citations): combined recall **95.3%** (57.0% existence + 31.7% author-hijack + 6.6% title-hijack).

The 32% of ground-truth fabrications missed by existence checking alone are attribute-corruption/hijack types — matching GPTZero's independent taxonomy (27% + 4%) almost exactly, and demonstrating why single-channel (existence-only) studies underestimate.

Every reference record now carries `not_found_reason` (no_title / parse_junk / non_article / foreign_language / datacite_preprint / short_title / **fab_candidate**), `author_hijack`, and a combined `fab_flag`.

---

## 3. What "not found" actually is

The naive not-found rate (23–24% of academic references) is **not** fabrication. Full-population classification of v8's 1.86M not-found refs:

| Bucket | Share | Meaning |
|---|---|---|
| no_title | 72.6% | untitled physics-style refs (journal+vol+page) — cannot be LLM fabrications (those invent titles) |
| parse junk | 8.1% | GROBID extracted body text, captions, LLM prompt fragments, code as "references" |
| confirmed real in index | 7.6% | resolved on second pass |
| **fab_candidate** | 11.6% | titled, well-formed, resolves nowhere — the only tier where fabrication hides |

Even the fab_candidate tier is ~97.5% noise (real works invisible to indexes: mangled titles, non-English originals, books, standards, workshop papers). Measuring true fabrication therefore requires **refinement and verification**, below.

---

## 4. Refinement and verification methodology

**Refinement** (`refine_fc.py`): candidates are cleared by title-repair + exact rematch, extended non-article patterns, or fuzzy title match **with author agreement**. Validated on 150 human-labeled candidates: retains the confirmed fabrication (1/1), clears 30% of known noise.

> **Methodological warning (hard-won):** fuzzy-existence matching *without* the author gate systematically clears exactly the fabrications being hunted — Frankenstein citations resemble real papers by construction. A refiner built on fuzzy title match alone cleared our first confirmed specimen. Any pipeline (including future ones) must require author agreement before dismissing a candidate.

**Verification**: LLM search-agent fleets, 10 citations per agent, 2–3 varied searches per citation across Google/Scholar/Crossref/OpenAlex/DBLP/arXiv, conservative criteria (FABRICATED only on clear absence, including checking whether the named authors ever wrote anything similar), all evidence notes retained. Totals: 1,000 LLM-era deep candidates, 300 pre-LLM controls, 300 from 2026, plus channel samples (60 title-hijack, 150 mixed) and ~50 ad-hoc audits.

---

## 5. Results

### 5.1 No Zhao-scale surge (the arbiter)
DOI-bearing references (v8: 5.58M; v8b: 9.55M): overall non-resolution 0.38–0.45%, flat for 18 years, **no step at 2022–23**. Post-ChatGPT excess under flat / linear / exponential baselines: −0.09 / +0.23 / +0.20pp (v8b) — sign-flips across forms ⇒ artifact. Zhao's +0.39pp is excluded under every specification. The same sign-flip verdict holds for the all-references channel at 38M refs.

### 5.2 Verified fabrication rate and count
Of 1,000 verified refined candidates (2023–25, lag-robust): **25 FABRICATED (2.5%, CI 1.7–3.7%)**, 951 real works invisible to indexes, 14 extraction junk, 10 unclear.
- In-pool count: 2.5% × 217,145 = **~5,400** (CI 3,700–8,000); +~380 via title-hijack; ≈8–9k after channel-coverage correction.
- Per-reference: **≈0.10% (1 in 1,000)** of 2023–25 references; arXiv-wide order of **40–50k fabricated citations** in 2023–25 preprints.
- Context: ~25× the Lancet/CITADEL biomedical rate (0.0042%); ~3–4× below Zhao's claim.

### 5.3 Era-specificity (direct verification)
300 refined pre-LLM candidates (2019–21; refiner keep-rate identical to LLM era, 72%): **0 fabricated**. Expected ~7.5 under era-equality → **p ≈ 5×10⁻⁴**. The pre-LLM candidate pool is coverage noise; the LLM-era fabrication signal is new.

### 5.4 Acceleration
| Citing year | Verified fab fraction (refined pool) | Est. count | Per-reference |
|---|---|---|---|
| 2023 | 1.4% (5/353) | ~1,000 | ~1 in 2,100 |
| 2024 | 1.2% (4/344) | ~850 | ~1 in 2,500 |
| **2025** | **5.3% (16/303)** | **~3,800** | **~1 in 550** |
| 2026 H1 | 4.1% (12/293) | ~1,260 (half-year) | ~1 in 800 |

The 2025 jump: z = 3.7, **p ≈ 2×10⁻⁴** (exploratory), sustained into 2026. A uniform cross-field dip in the raw 2026 candidate rate is a corpus-edge artifact; candidate *purity* stayed at the accelerated level.

### 5.5 Field concentration
fab_candidate rate, pre-LLM (2019–22) → post (2023–25), citing-paper primary category (40k-paper stratified sample):

| Field | Δ | Change |
|---|---|---|
| **cs** | **+1.02pp** | **+17%** |
| astro / cond-mat / physics-other | +0.14 to +0.27pp | +7–8% |
| hep | ±0.00pp | 0% |
| math / eess / stat | −0.5 to −0.7pp | −7 to −15% |

Only the maximum-LLM-adoption field moves; an instrument artifact would move all fields. CS (~40% of recent arXiv) accounts for essentially the whole corpus-level break.

### 5.6 Propagation
Two specimens show fabrications entering the scholarly record: an invented title on a real *Adv. Chem. Phys.* locator that now has a **phantom Google Scholar entry with two propagated citations**, and a phantom citation **repeated verbatim by a second 2026 paper**. Fabricated citations are beginning to reproduce.

---

## 6. Statistical ledger

| Claim | Evidence | Significance / robustness |
|---|---|---|
| No Zhao-scale excess | 9.55M DOI refs | excluded under every baseline form; sampling SE ~0.01pp |
| Fabrication fraction 2.5% | 25/1000 verified | Wilson CI [1.7%, 3.7%]; specimens span 24 distinct papers (no clustering correction needed) |
| Era-specificity | 0/300 pre-LLM verified | **p ≈ 5×10⁻⁴ (direct)** |
| 2025 acceleration | 16/303 vs 9/697 | z = 3.7, p ≈ 2×10⁻⁴ (exploratory label) |
| 2023 trend-break | +0.6–0.9pp over pre-LLM trend, lag-controlled | sign-consistent across 3 baseline forms; ~10× baseline scatter; **replicated v8→v8b to 0.02pp** |
| Field concentration | cs +17%, others ≤±8% | internal-control logic |

---

## 7. Limitations

1. **arXiv preprints only.** Camera-ready-only fabrications (15/68 in ground truth) are invisible → our rates **undercount the published literature**. No biomedicine or humanities.
2. **Content-recombination is out of scope** — a real paper cited for a claim it never made requires claim-level NLP. Shared blind spot with Zhao, Lancet, and GPTZero.
3. **Detector recall is 85–95%, not 100%**; counts are recall-corrected but the correction (especially the unverified author-hijack channel's coverage factor) carries uncertainty.
4. **Verification is by LLM search agents,** conservative and evidence-logged, but not human experts; judgment error is unquantified. (Recommended before publication: human audit of the 39 specimens + a duplicate-verification reliability subsample.)
5. **Lag-control discards references to works <2 years old** — recent-work fabrications are unmeasured.
6. Per-year fractions rest on 4–16 events; paper-level prevalence ("X% of papers contain a fabrication") is not derivable from this design.
7. The per-field table shows candidate-rate deltas, not per-field verified rates (39 specimens are too few to split).
8. "2M papers" = 2.0M draws; ~1.7M distinct (v8/v8b overlap by design of the replication).

---

## 8. Relation to prior studies

| Study | Corpus | Method | Their number | Our verdict |
|---|---|---|---|---|
| Zhao et al. 2026 | arXiv | excess-unmatched statistic, no per-citation verification | +0.39pp post-ChatGPT | **rejected** — artifact of baseline choice; our verified rate is 3–4× lower and their pre-2022 "baseline" era shows zero verified fabrication |
| Lancet / CITADEL 2026 | PubMed | human-verified per-citation | 0.0042% of refs (1-in-277 papers) | consistent as a *published-biomedicine* floor; arXiv preprints run ~25× higher — unrefereed + CS-heavy + no camera-ready cleanup |
| GPTZero NeurIPS/ICLR | conference submissions | agentic web verification | ~100 fabs / 4,841 papers (~0.05%/ref) | matches our 2025 arXiv rate; their sets serve as our recall ground truth |
| CiteTracer (ICLR-2026 desk rejects) | OpenReview | program-chair confirmation | 957 fabricated citations | used as our largest recall benchmark (95.3%) |

---

## 9. Reproducibility map

- Sweeps: `scripts/arxiv_sweep.py` (+ `run_v8_sharded.sh` pattern); outputs `scripts/arxiv_sweep_v{7,8,8b}/` (per-month CSV + per-ref JSONL)
- Detector: `scripts/batch_verify_years.py` (`verify_refs`), `scripts/ref_classify.py` (channels), `scripts/integrated_lookup.py`, `scripts/oa_local.py`, `scripts/arxiv_authority.py` (local-first)
- Refiner: `scripts/refine_fc.py` (validate/pool modes)
- Analyses: `scripts/v8_analyze.py`, `v8b_analyze.py` (5-channel), `robust1.py` (lag control), `field_rates.py` (per-field), `validate_upgrade.py` + `citetracer_recall.py` + `neurips_test.py` (ground-truth recall)
- Verification samples/labels: `/space/rwang/_speedtest/` (`deep_verify_1000.json`, `ctrl2026_verify.json`, `fc_verdicts.json`, `fieldrates/`)
- Ground truths: GPTZero NeurIPS & ICLR sheets, CiteTracer structured JSON (`_speedtest/citetracer_structured.json`)
- Operational handbook for successors: `HANDOFF.md`

## 10. Selected specimen catalog (12 of 39)

| # | Fabricated citation (abbrev.) | Why it's confirmed fake |
|---|---|---|
| 1 | "AI in education: risks and opportunities of LLMs…" arXiv:2404.12218 | no such paper; the arXiv ID resolves to a condensed-matter colloids paper |
| 2 | Makransky & Petersen, *Virtual Reality* 23(2) 2019 | real VR-education researchers; paper doesn't exist; pages belong to other articles |
| 3 | "Marigold" cited to Kopf/Rombach/Geiger/Koltun, ICCV 2023 | famous real paper; entire author list, venue, and pages invented |
| 4 | Qiao/Deng/**Fei-Fei**, TPAMI 2021 | mimics the ImageNet author pattern; zero hits in Crossref/OpenAlex/DBLP |
| 5 | SemEval-2020 "Task 12" discourse parsing | Task 12 was OffensEval (offensive language); author untraceable |
| 6 | *Adv. Chem. Phys.* 65:115 with invented title | real locator holds a different 1986 paper; fake title has a phantom Scholar entry with 2 citations |
| 7 | "CollabCoder…" + invented subtitle, Sarkar et al. | splices a real CHI-2024 title; **repeated verbatim by another 2026 paper** |
| 8 | Imran & Ofli, PACM HCI 2016 | venue didn't exist until 2017 |
| 9 | Alcaraz & Lopez, *Algorithms* 16(8):378 | that slot holds a different real paper by different authors — metadata lifted wholesale |
| 10 | ICAC3-2021 paper, pp. 351–356 | proceedings end at page 323 |
| 11 | "Journal of Computational Diagnostics" | journal does not exist |
| 12 | Sanchez & Ahedo ion-propulsion review, *Prog. Aerosp. Sci.* 102 | volume TOC verified — no such article; Ahedo real, paper invented |

Full catalog with evidence notes: verification-workflow journals (see `HANDOFF.md` §7).

---

*Report history: the previous version of this document (sections 1–22, June 12 – July 22, 2026) recorded the method-development phase chronologically, including results later superseded (early "no evidence of fabrication" conclusions reflected detector blind spots corrected in §2 and sampling power documented in §4). It is preserved unmodified at `citation_verification_report.md.bak_20260818_preRewrite`.*
