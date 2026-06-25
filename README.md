# fake-citation-detector

A research pipeline for extracting, verifying, and recommending corrections for citations in scientific papers. Given a PDF, it uses GROBID to extract every reference, then verifies each one against OpenAlex (492M works) and Crossref (160M works) using a four-phase lookup — exact DOI, batch title search, individual fallback, and semantic vector similarity. Citations that cannot be verified get ranked recommendations of the closest real papers.

**Current results across 78 papers, 3,817 citations: 95.7% verified rate. 100% recall on injected hallucinated citations.**

---

## Project structure

```
fake-citation-detector/
├── scripts/                        ← primary pipeline (GROBID + Solr + vector)
│   │
│   │  ── Verification pipeline ──
│   ├── grobid_verify.py            ← batch verifier: folder of cited_sent JSONs → report
│   ├── verify_pdf.py               ← end-to-end: single PDF → GROBID → verify → report
│   ├── solr_lookup.py              ← OpenAlex Solr interface (DOI + batch title search)
│   ├── vector_lookup.py            ← Phase 4: sentence-transformer re-ranking + recommendations
│   ├── citation_parser.py          ← parse raw citation string → structured fields
│   ├── crossref_lookup.py          ← Crossref REST API fallback
│   │
│   │  ── Recommendation / interactive tools ──
│   ├── recommend_citation.py       ← CLI: paste a suspicious citation, get top-N real matches
│   │
│   │  ── Legacy regex pipeline (kept for reference) ──
│   ├── parse_refs.py               ← extract reference section from PDF via pdftotext
│   ├── parser.py                   ← regex citation parser (6 citation styles)
│   ├── mongo_lookup.py             ← Crossref MongoDB lookup (legacy backend)
│   ├── batch_audit.py              ← parse quality audit across sample PDFs
│   ├── fetch_openalex.py           ← download sample PDFs from OpenAlex
│   │
│   └── samples/                    ← sample PDFs (gitignored — run fetch_openalex.py)
│       ├── openalex_pdfs/          ← JAMA, Nature, Science, eLife, PLoS…
│       ├── diverse_pdfs/           ← AER, JACS, Physical Review Letters, PLoS CompBio…
│       └── arxiv_pdf/              ← arXiv preprints
│
├── grobid/
│   └── grobid_pdf_to_json/         ← GROBID PDF→TEI→cited_sent JSON pipeline
│       ├── step1_pdf_to_tei/       ← batch GROBID processing
│       └── step2_tei_to_json/      ← TEI-XML → cited_sent JSON converter
│
├── citation_verification_report.md ← full results, error analysis, fake detection evaluation
├── parsed_citations_report.md      ← legacy regex parser field coverage analysis
└── .env                            ← credentials (gitignored — never commit)
```

---

## How it works

The pipeline runs in four phases. Each phase hands off only the citations it could not resolve to the next.

```
PDF
 │
 ▼
GROBID (/api/processFulltextDocument)
 │  extracts all references as structured TEI-XML
 │  fields: title, year, DOI, authors, raw citation string
 │
 ▼
Phase 1 — DOI exact match (Solr)
 │  doi:"10.xxxx/..." → certain match
 │  ~13% of all citations resolved here
 │
 ▼
Phase 2 — Batch title search (Solr edismax, 15 citations/request)
 │  OR of Lucene phrase queries → fuzzy title+year client-side match
 │  ~78% of all citations resolved here
 │
 ▼
Phase 3 — Individual fallback + Crossref
 │  Retry with title variants (ligature fix, author prefix strip, subtitle split)
 │  If still unresolved: query Crossref REST API
 │  ~4% of all citations resolved here
 │
 ▼
Phase 4 — Vector similarity re-ranking
 │  Broad Solr edismax query (40 candidates, phrase boost pf=title^20, mm=3<70%)
 │  Embed query + candidates with all-MiniLM-L6-v2 (384-dim, ~15 ms on CPU)
 │  Accept if cosine similarity ≥ 0.82; otherwise surface top-N as suggestions
 │  Recovers ~2% of all citations; remaining NOT_FOUND get recommendations
 │
 ▼
Output
  ✓ FOUND        — citation verified (method + confidence score)
  ⚠ FOUND_MISMATCH — verified but year or journal doesn't match cited metadata
  ✗ NOT_FOUND    — with ranked list of top-3 closest real papers
```

Each citation that cannot be verified is passed to the recommendation engine, which returns the top-N most semantically similar papers from OpenAlex with cosine similarity scores. This lets a researcher quickly find what the author probably meant to cite.

---

## Setup

### Requirements

- Python 3.10+
- GROBID server running locally: `http://localhost:8070` (or update `GROBID_URL` in `verify_pdf.py`)
- OpenAlex Solr index: `http://galaxy:8983/solr/openalexWorks/select` (492M works)

### Install Python dependencies

```bash
cd fake-citation-detector
python3 -m venv .venv
source .venv/bin/activate
pip install requests numpy sentence-transformers
# Optional (Crossref fallback):
pip install crossref-commons
# Optional (legacy regex pipeline):
pip install pymongo python-dotenv PyMuPDF
```

### Configure credentials

Create a `.env` file in the repo root (never commit this):

```
MONGO_URI=mongodb://user:password@galaxy3:27017/
```

---

## Usage

### Verify a single PDF (recommended entry point)

```bash
cd scripts
python3 verify_pdf.py paper.pdf

# Options:
python3 verify_pdf.py paper.pdf --n 5           # top-5 suggestions per NOT_FOUND citation
python3 verify_pdf.py paper.pdf --show-found    # also print matched citations
python3 verify_pdf.py paper.pdf --no-vector     # skip Phase 4 (faster for large papers)
python3 verify_pdf.py paper.pdf --out report.txt
```

`verify_pdf.py` handles the full pipeline: GROBID → TEI parsing → 4-phase verification → recommendations. A typical 100-citation paper runs in 30–60 seconds.

### Find the closest real paper for a suspicious citation

```bash
# Interactive — paste citations one at a time:
python3 recommend_citation.py

# Single citation:
python3 recommend_citation.py --raw "Smith J et al. COVID-19 hospitalised patients. JAMA 2020." --n 3

# Batch mode from file, JSON output (for scripting):
python3 recommend_citation.py --batch --file suspicious.txt --json > matches.jsonl
```

### Batch verification across many papers

```bash
# Run grobid_verify.py on a folder of pre-processed cited_sent JSONs:
python3 grobid_verify.py \
  --cited-sent-dir ../grobid/grobid_pdf_to_json/step2_tei_to_json/out/cited_sent \
  --backend solr \
  --vector-threshold 0.75

# Filter to one paper:
python3 grobid_verify.py --paper jama_2020_12839

# Skip vector phase (faster):
python3 grobid_verify.py --no-vector
```

### Download more sample PDFs from OpenAlex

```bash
python3 fetch_openalex.py                              # default journals
python3 fetch_openalex.py "Nature" "Cell" "PLOS ONE"   # custom journals
python3 fetch_openalex.py --per-journal 5
```

---

## Results at a glance

| Dataset | Papers | Citations | Found | Not found |
|---------|-------:|----------:|------:|----------:|
| Original set (38 high-impact papers) | 38 | 2,381 | 95.7% | 4.3% |
| Diverse set (20 cross-field papers)  | 20 |   718 | 93.7% | 6.3% |
| JAMA COVID paper (live test)         |  1 |   102 | 98.0% | 2.0% |

NOT_FOUND breakdown: ~41% books/manuals, ~26% software/R packages, ~26% genuinely unindexed papers, ~7% GROBID parsing errors. **No fabricated citations detected** in any real paper tested.

Fake citation detection: **114 injected hallucinated citations detected — 100% recall, 0 misses.**

---

## Key files

| File | Purpose |
|------|---------|
| `verify_pdf.py` | End-to-end single-PDF tool — the main entry point |
| `grobid_verify.py` | Batch verifier for pre-processed cited_sent JSON folders |
| `solr_lookup.py` | OpenAlex Solr interface — DOI exact match + batched title search |
| `vector_lookup.py` | Phase 4 vector re-ranking; `recommend()` for top-N suggestions |
| `citation_parser.py` | Parse a raw citation string → title/year/doi (GROBID → heuristic cascade) |
| `recommend_citation.py` | Interactive/batch CLI for finding the closest real paper |
| `citation_verification_report.md` | Full results, error taxonomy, and evaluation methodology |

---

## Notes

- The `scripts/samples/` directory (PDFs) is gitignored — run `fetch_openalex.py` to repopulate.
- The vector model (`all-MiniLM-L6-v2`, ~80 MB) is downloaded automatically on first use by `sentence-transformers`. Subsequent runs load it from cache in ~1 second.
- The legacy regex pipeline (`parse_refs.py`, `parser.py`, `mongo_lookup.py`) is kept for reference. For new work, use `verify_pdf.py` — it is more accurate, handles more citation styles, and produces richer output.
- Credentials (MongoDB URI) must only ever be stored in `.env` — never committed or shared.
