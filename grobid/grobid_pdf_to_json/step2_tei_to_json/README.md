# Step 2 — TEI-XML → per-paper JSON

**Goal:** take the TEI-XML produced by **Step 1** (`../step1_pdf_to_tei/`) and
turn it into three structured JSON products per paper — the citing sentences,
the labeled sentence dataset, and the header metadata. This step is pure Python
(no GROBID, no server); it re-structures what GROBID already extracted.

```
   <name>.tei.xml  ──grobid_tool.py──▶  cited_sent / model_dataset / header_info  (JSON)
```

The core logic lives in [`grobid_tool.py`](grobid_tool.py); [`run.py`](run.py)
is a thin CLI around it.

---

## What it produces

For each input `<name>.tei.xml` you get three files:

| File | Shape | Contents | Used for |
|------|-------|----------|----------|
| `cited_sent/<name>.json` | list | Each **reference that was actually cited** in the body, with its bibliographic record **and the sentences that cited it** (marker normalized to `[CITATION]`). | Building a *keyword → citations* dictionary. **The persistent product.** |
| `model_dataset/<name>.json` | list | **Every sentence** in the body, with before/after context and a `label` (1 = contained an in-text citation, 0 = didn't). | Training a "does this sentence need a citation?" classifier. *Regenerable.* |
| `header_info/<name>.json` | object | `title`, `authors`, and `doi`/`issn` when present. | Paper-level metadata. |

See [`sample_data/expected_output/`](sample_data/expected_output/) for real
examples produced from [`sample_data/example.tei.xml`](sample_data/example.tei.xml).

### How a citation becomes a record

1. Every `<ref type="bibr" target="#b12">` in the body is rewritten inline to
   the token `[CITATION #b12]`. Ranges like `[2]–[6]` are expanded to the
   individual references in between.
2. The body is split into sentences. A sentence containing any `[CITATION …]`
   marker is **label = 1**; everything else is **label = 0**.
3. For each citing sentence, the `#b12` target is looked up in the reference
   list, the bibliographic fields (title/authors/year/journal/volume/pages/raw
   string) are pulled from GROBID's `<biblStruct>`, and the sentence (marker
   reduced to a bare `[CITATION]`) is attached to that reference.
4. Author/year names and leftover bracket markers are scrubbed from the stored
   sentence text (`remove_ref`) so it reads naturally.

---

## Install & run

```bash
pip install -r requirements.txt
python -c "import nltk; nltk.download('punkt'); nltk.download('words')"

# a single TEI-XML file
python run.py --xml sample_data/example.tei.xml --out ./out
# a whole folder of *.xml / *.tei.xml (e.g. Step 1's output)
python run.py --dir ../step1_pdf_to_tei/tei/ --out ./out
```

Re-runs skip papers whose `cited_sent` JSON already exists (`--overwrite` to
force). Self-test (reproduces `sample_data/expected_output/`):

```bash
python run.py --xml sample_data/example.tei.xml --out /tmp/selftest --overwrite
```

---

## Things to know (caveats)

- **Papers are dropped silently.** `parse_tei_xml` is wrapped in a bare
  `try/except` — any parse error just prints `Cannot parse file:` and returns 0.
  A paper is also dropped if there's no title, no authors, <10 sentences, or no
  citing sentence. Usually legitimate, but **measure the drop rate on a sample**
  before assuming full coverage.
- **GROBID quality is the ceiling.** This step only re-structures Step 1's
  output; it cannot recover what GROBID got wrong, and there is no CrossRef
  cross-check.
- **The citation marker prefix is hard-coded to `#b`** (GROBID's scheme).
- **Input must be real GROBID TEI** — `grobid_tei_xml` requires the
  `<appInfo><application ident="GROBID">` version stamp (the sample has one).
- Formulas, tables, and figures are stripped from the body before parsing.

---

## Files

```
grobid_tool.py                  # the parser (unmodified from the citation_pipeline repo)
run.py                          # CLI wrapper
NLPtools/SentenceTokenizer/     # bundled sentence tokenizer (NLTK punkt + custom post-processing)
sample_data/example.tei.xml     # minimal synthetic GROBID TEI input
sample_data/expected_output/    # the three JSON files produced from it
requirements.txt
```
