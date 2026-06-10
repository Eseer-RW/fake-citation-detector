# fake-citation-detector

A research pipeline for extracting, parsing, and verifying citations in scientific papers. Given a PDF, it pulls out every reference, parses it into structured fields (authors, title, journal, year, DOI), and looks each one up against a local Crossref database to determine whether the citation is real and correctly described.

---

## Project structure

```
fake-citation-detector/
├── scripts/                   ← core regex-based parser pipeline
│   ├── parse_refs.py          ← extract citation section from a PDF; entry point
│   ├── parser.py              ← parse raw citation strings into structured fields
│   ├── mongo_lookup.py        ← look up parsed citations in the Crossref database
│   ├── batch_audit.py         ← run parse quality audit across all sample PDFs
│   ├── store_parsed.py        ← parse all sample PDFs and write output to a text file
│   ├── fetch_openalex.py      ← download sample PDFs from OpenAlex by journal
│   ├── audit.py               ← single-paper audit helper
│   ├── checkabstract.py       ← check MongoDB connection and inspect records
│   ├── test_mongo.py          ← test MongoDB lookup against sample citations
│   ├── test_real_citations.py ← end-to-end test: parse PDFs and verify via Crossref
│   └── run_batch_openalex.sh  ← shell wrapper to run batch audit
├── grobid/                    ← ML-based parser pipeline (alternative to scripts/)
│   └── grobid_pdf_to_json/    ← see grobid/grobid_pdf_to_json/README.md
├── parsed_citations_report.md ← analysis of parse results across 41 sample PDFs
└── .env                       ← MongoDB credentials (gitignored — never commit)
```

---

## How it works

### Regex parser (`scripts/`)

The main pipeline has three stages:

```
PDF  ──►  parse_refs.py  ──►  parser.py  ──►  mongo_lookup.py
          (extract ref         (structure      (verify against
           section)             fields)         Crossref DB)
```

1. **`parse_refs.py`** — uses `pdftotext` to extract text from the PDF, then locates the references section by searching for standard headings ("References", "Bibliography", etc.). Returns the raw reference strings.

2. **`parser.py`** — applies a series of regex strategies to parse each raw string into structured fields: authors, title, journal, year, volume, pages, DOI. Handles six citation styles: Nature compact, Science compact, APA, NLM/Vancouver, eLife, and APS physics.

3. **`mongo_lookup.py`** — takes a parsed citation and searches a local MongoDB database loaded with Crossref metadata. Matches by DOI first, then falls back to fuzzy title+year matching. Returns whether the citation was found and how closely it matches.

### GROBID pipeline (`grobid/`)

An alternative parser that uses a machine-learning server (GROBID) instead of regex. Produces richer output — including the **body sentences that cited each reference** — at the cost of needing a Docker container to run. See [`grobid/grobid_pdf_to_json/README.md`](grobid/grobid_pdf_to_json/README.md) for setup and usage.

---

## Setup

### Requirements

- Python 3.9+
- `pdftotext` (poppler): `sudo apt install poppler-utils` / `brew install poppler`
- MongoDB instance loaded with Crossref data (host: `jupiter2:27017`, db: `crs`, collection: `crossref`)

### Install Python dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pymongo python-dotenv requests fitz PyMuPDF
```

### Configure credentials

Create a `.env` file in the repo root (never commit this):

```
MONGO_URI=mongodb://user:password@jupiter2:27017/
```

---

## Usage

### Parse a single PDF

```bash
cd scripts
python3 parse_refs.py paper.pdf
```

Also accepts a plain-text reference list or stdin:

```bash
python3 parse_refs.py references.txt
python3 parse_refs.py          # paste text, then Ctrl+D
```

### Parse all sample PDFs and save output

```bash
cd scripts
python3 store_parsed.py        # writes parsed_citations.txt
```

### Run the batch parse quality audit

```bash
cd scripts
python3 batch_audit.py         # writes results_openalex_v14.txt
```

### Download more sample PDFs from OpenAlex

```bash
cd scripts
python3 fetch_openalex.py                              # default journals, 10 per journal
python3 fetch_openalex.py "Nature" "Cell" "PLOS ONE"   # custom journals
python3 fetch_openalex.py --per-journal 5              # 5 papers per journal
```

PDFs are saved to `samples/openalex_pdfs/<JournalName>/` (gitignored).

---

## Sample data

41 PDFs across 6 journals were downloaded from OpenAlex and used to develop and validate the parser:

| Journal | PDFs | Citations | DOI coverage |
|---|---|---|---|
| PLoS Medicine | 10 | 1,016 | 14% |
| Nature | 11 | 626 | 3% |
| PLoS ONE | 10 | 454 | 17% |
| Science | 8 | 343 | 2% |
| eLife | 3 | 253 | 81% |
| JAMA | 1 | 107 | 80% |
| **Total** | **41** | **2,799** | **19%** |

Parse quality: 33 of 41 PDFs parse cleanly. The remaining 8 have issues from PDF-level problems (encryption, figure-label bleed), unusual formatting, or audit edge cases. See [`parsed_citations_report.md`](parsed_citations_report.md) for the full breakdown.

---

## Key files explained

| File | Purpose |
|---|---|
| `scripts/parser.py` | Core regex parser — edit this to improve citation field extraction |
| `scripts/batch_audit.py` | Runs all 41 PDFs and flags bad-author, journal=title, noise, and future-year anomalies |
| `scripts/mongo_lookup.py` | Crossref database interface — DOI lookup + fuzzy title/year matching |
| `scripts/fetch_openalex.py` | Expands the sample set by downloading more PDFs from OpenAlex |
| `parsed_citations_report.md` | Analysis of parse field coverage, DOI rates, and known failure cases |
| `.env` | MongoDB URI — **never commit**, listed in `.gitignore` |

---

## Notes

- The `scripts/samples/` directory (PDFs) is gitignored — run `fetch_openalex.py` to repopulate it.
- DOI coverage is low overall (19%) because Nature and Science don't print DOIs inline in their reference lists. Title+year fuzzy matching is used as a fallback for those journals.
- The GROBID pipeline (`grobid/`) is the preferred approach for new work — it handles more citation formats and also extracts citing sentences, which are useful for detecting fabricated references.
