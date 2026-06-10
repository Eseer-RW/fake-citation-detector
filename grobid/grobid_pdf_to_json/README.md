# PDF → citation JSON (GROBID pipeline, 2 steps)

This bundle is the **PDF branch** of a larger citation pipeline. It takes raw
scientific **PDFs** and turns them into structured **JSON** describing, for each
paper, which references were cited and in which sentences. It is the path used
for sources that only exist as PDF (e.g. arXiv); sources that are already XML
(PMC, bioRxiv) skip Step 1 and feed a different parser.

## The two steps and what each achieves

```
            STEP 1                              STEP 2
   PDF ─▶ [GROBID server] ─▶ TEI-XML  ─▶  grobid_tool.py  ─▶  cited_sent / model_dataset / header_info
          extract structure              re-structure into        (JSON)
          from the PDF                   citation products
```

| | Step 1 — `step1_pdf_to_tei/` | Step 2 — `step2_tei_to_json/` |
|---|---|---|
| **Input** | PDF files | TEI-XML files (Step 1's output) |
| **Output** | `<name>.tei.xml` | 3 JSON files per paper |
| **What it achieves** | Reads the messy PDF and produces a tagged XML: title, authors, body, in-text citation markers, reference list. | Pulls out every reference that was cited and the sentences citing it; labels every sentence for "needs a citation?"; extracts header metadata. |
| **How** | A GROBID ML server (run via Docker); the script is just an HTTP client. | Pure Python (BeautifulSoup + `grobid_tei_xml` + a sentence tokenizer). |
| **Cost** | Heavy — CPU/GPU bound, the bottleneck of the whole pipeline. | Light — fast, runs anywhere. |
| **Needs a server?** | Yes (GROBID Docker container). | No. |

**Why two steps?** PDF parsing is hard, slow, and best done by a specialized ML
service (GROBID). Once the content is clean XML, extracting citations is cheap,
deterministic Python. Splitting them lets you run the expensive Step 1 once on a
big machine/fleet and re-run the cheap Step 2 freely as the extraction logic
evolves.

## Quick start (end to end)

```bash
# --- Step 1: PDF -> TEI-XML ---
cd step1_pdf_to_tei
docker run --rm -p 8070:8070 grobid/grobid:0.7.1      # in a separate terminal
pip install -r requirements.txt
python run_grobid.py --input /path/to/pdfs --output ./tei

# --- Step 2: TEI-XML -> JSON ---
cd ../step2_tei_to_json
pip install -r requirements.txt
python -c "import nltk; nltk.download('punkt'); nltk.download('words')"
python run.py --dir ../step1_pdf_to_tei/tei/ --out ./out
```

No PDFs handy? **Step 2 ships with a runnable sample** so you can see the output
format immediately without standing up GROBID:

```bash
cd step2_tei_to_json
python run.py --xml sample_data/example.tei.xml --out /tmp/selftest --overwrite
```

Each step's own `README.md` has the full details and caveats.

## Origin

Extracted from the `citation_pipeline` repo:
- Step 1 ← `0.grobid/grobid_client_python/` + `1.data/code/run_grobid_python.py`
- Step 2 ← `1.data/code/NLPtools/grobid_tool.py` (+ its `SentenceTokenizer`)

The parser code (`grobid_tool.py`) and the GROBID client are unmodified from the
repo. The two CLI wrappers (`run_grobid.py`, `run.py`) and the sample data were
added for this share, because the originals had hardcoded internal paths.
