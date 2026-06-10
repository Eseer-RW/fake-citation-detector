# GROBID Citation Pipeline

A two-step pipeline that converts scientific PDFs into structured JSON describing every citation and the sentences that cite it. This is an alternative to the regex-based parser in `scripts/` — it uses a machine-learning server (GROBID) to parse PDFs and produces richer output, including **citing sentences** for each reference.

---

## How it fits into fake-citation-detector

```
scripts/ (regex parser)              grobid/ (this pipeline)
────────────────────────             ──────────────────────────────────
PDF → pdftotext → regex        vs.   PDF → GROBID ML → TEI-XML → JSON
                                                              ↓
                                              cited_sent: which sentences
                                              in the paper body cited each
                                              reference, with [CITATION] marker
```

The GROBID output is richer: alongside the structured citation fields (title, authors, year, journal, volume, pages), each reference record includes the **body sentences that cited it**. This is directly useful for fake-citation detection — a fabricated reference often gets cited in a sentence whose topic doesn't match the real paper.

---

## Pipeline overview

```
        STEP 1                              STEP 2
PDF ─▶ [GROBID server] ─▶ TEI-XML  ─▶  grobid_tool.py  ─▶  cited_sent /
       (ML models)                       (pure Python)        model_dataset /
                                                              header_info (JSON)
```

| | Step 1 — `step1_pdf_to_tei/` | Step 2 — `step2_tei_to_json/` |
|---|---|---|
| **Input** | PDF files | TEI-XML files (Step 1 output) |
| **Output** | `<name>.tei.xml` | 3 JSON files per paper |
| **Needs a server?** | Yes — GROBID via Docker | No |
| **Speed** | Slow (ML, CPU/GPU bound) | Fast (pure Python) |

---

## Prerequisites

### Step 1 only
A running GROBID server. Easiest via [Docker Desktop](https://www.docker.com/products/docker-desktop/):

```bash
docker run -d -p 8070:8070 grobid/grobid:0.7.1
```

Verify it's up: open http://localhost:8070 — you should see the GROBID web UI.

### Step 2 (on the server running the repo, e.g. galaxy4)
```bash
cd grobid/grobid_pdf_to_json/step2_tei_to_json
pip install -r requirements.txt
python3 -c "import nltk; nltk.download('punkt'); nltk.download('punkt_tab'); nltk.download('words')"
```

---

## Running the pipeline

### Step 1 — PDF → TEI-XML
Run this on the machine with Docker (e.g. your local Mac):

```bash
cd grobid/grobid_pdf_to_json/step1_pdf_to_tei
pip install -r requirements.txt
python3 run_grobid.py --input /path/to/pdfs --output ./tei
```

Options:
- `--input` — directory of PDFs (searched recursively)
- `--output` — where `.tei.xml` files are written
- `--server` — GROBID URL (default `http://localhost:8070`)
- `--n` — number of concurrent requests (default 10)

Then copy the TEI-XML files to wherever you run Step 2:
```bash
scp -r ./tei/ user@server:/path/to/fake-citation-detector/grobid/grobid_pdf_to_json/step1_pdf_to_tei/tei_flat/
```

### Step 2 — TEI-XML → JSON
```bash
cd grobid/grobid_pdf_to_json/step2_tei_to_json

# single file
python3 run.py --xml path/to/paper.tei.xml --out ./out

# whole directory (must be flat — all .xml files in one folder, no subdirectories)
python3 run.py --dir ../step1_pdf_to_tei/tei_flat/ --out ./out

# force reprocess already-done files
python3 run.py --dir ../step1_pdf_to_tei/tei_flat/ --out ./out --overwrite
```

> **Note:** `--dir` is non-recursive. If your TEI-XML files are in subdirectories (e.g. organised by journal), flatten them first:
> ```bash
> mkdir tei_flat && find tei/ -name "*.xml" -exec cp {} tei_flat/ \;
> ```

---

## Output format

For each paper, Step 2 writes three JSON files under `--out`:

### `cited_sent/<name>.json`
A list — one entry per reference that was actually cited in the body:

```json
[
  {
    "title": "A novel coronavirus from patients with pneumonia in China",
    "authors": "N Zhu;D Zhang;W Wang",
    "year": "2019",
    "journal": "N Engl J Med",
    "volume": "382",
    "pages": "727-733",
    "citation": "Zhu N, Zhang D, Wang W, et al. ... N Engl J Med. 2020;382(8):727-733. doi:10.1056/NEJMoa2001017",
    "sentences": [
      "[CITATION] The first coronavirus that caused severe disease was SARS, which resulted in the 2002-2003 pandemic."
    ]
  }
]
```

### `header_info/<name>.json`
Paper-level metadata: `title`, `authors`, `doi`, `issn`.

### `model_dataset/<name>.json`
Every sentence in the body with before/after context and a label (`1` = contained a citation, `0` = did not). Used for training a "does this sentence need a citation?" classifier.

---

## Results on 41 OpenAlex sample PDFs

| Outcome | Count | Details |
|---|---|---|
| ✅ Full JSON produced | 35 | cited_sent + model_dataset + header_info |
| ⚠️ No citing sentence | 2 | `s41586-020-2012-7` (only 2 refs), `science_270_5243_1789` (old format) |
| ⚠️ No title extracted | 1 | `nature11247` — GROBID couldn't read the title |
| ❌ Cannot parse | 3 | All 3 eLife papers — GROBID version incompatibility with eLife's XML structure |

The 3 eLife failures are likely fixable by upgrading to a newer GROBID version (`0.8.x`).

---

## No Docker? Quick test with sample data

Step 2 ships with a minimal TEI-XML sample so you can see the output format without running GROBID at all:

```bash
cd grobid/grobid_pdf_to_json/step2_tei_to_json
python3 run.py --xml sample_data/example.tei.xml --out /tmp/grobid_test --overwrite
cat /tmp/grobid_test/cited_sent/example.tei.json
```

The expected output is in `sample_data/expected_output/` for comparison.

---

## File structure

```
grobid/
└── grobid_pdf_to_json/
    ├── README.md                          ← this file
    ├── step1_pdf_to_tei/
    │   ├── run_grobid.py                  ← CLI: PDF → TEI-XML
    │   ├── config.json                    ← GROBID client config
    │   ├── grobid_client/                 ← bundled kermitt2 grobid_client_python
    │   └── requirements.txt
    └── step2_tei_to_json/
        ├── run.py                         ← CLI: TEI-XML → JSON
        ├── grobid_tool.py                 ← core parser logic
        ├── NLPtools/SentenceTokenizer/    ← bundled sentence tokenizer
        ├── sample_data/                   ← test TEI-XML + expected output
        └── requirements.txt
```

Generated directories (`tei/`, `tei_flat/`, `out/`) are gitignored — run the pipeline to regenerate them.
