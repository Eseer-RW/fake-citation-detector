# Step 1 — PDF → TEI-XML (GROBID)

**Goal:** turn a raw scientific **PDF** into structured **TEI-XML** — a machine
-readable representation in which the title, authors, body paragraphs, the
in-text citation markers, and the reference list are all tagged. This is the
*only* step that has to deal with the messy PDF format; everything downstream
works on the clean XML.

```
   PDF  ──▶  [ GROBID server ]  ──▶  <name>.tei.xml
            (ML models: header,
             body, citations, refs)
```

GROBID is a machine-learning service (CRF / deep-learning sequence labelers)
that reads a PDF's layout and text and emits TEI-XML. It runs as a **server**;
the script here is just a client that ships PDFs to it and saves the XML.

## What this step does *not* do

- It does **not** extract citing sentences or build the dictionary — that's
  Step 2.
- With `consolidate_*=False` (the pipeline default, kept here) it does **not**
  call CrossRef to clean up metadata. Whatever GROBID parses from the PDF is
  what you get. This is faster and avoids rate-limited external calls, at the
  cost of some noisy reference strings.

## Prerequisites

**1. A running GROBID server.** Easiest via Docker (matches the version the
pipeline used):

```bash
docker run --rm -p 8070:8070 grobid/grobid:0.7.1
# GPU (much faster on large batches):
docker run --rm --gpus all -p 8070:8070 grobid/grobid:0.7.1
```

Check it's up: open http://localhost:8070 in a browser.

**2. The Python client deps:**

```bash
pip install -r requirements.txt        # just `requests`
```

## Run

```bash
python run_grobid.py --input ./pdfs --output ./tei
```

- `--input`  — a directory of PDFs (searched recursively).
- `--output` — where `<name>.tei.xml` files are written.
- `--server` — GROBID URL (default `http://localhost:8070`).
- `--n`      — concurrent requests (default 10; raise it toward the server's
  thread count for throughput).

The output `.tei.xml` files are the input to **Step 2**
(`../step2_tei_to_json/`).

## Notes / caveats

- **This is the expensive step.** GROBID is CPU/GPU-bound; converting millions
  of PDFs is a multi-day/multi-week job and is best sharded across a fleet. For
  a quick look, run a few hundred PDFs.
- **Quality is PDF-dependent.** Clean, single-column, modern PDFs parse well;
  scanned, two-column, or math-heavy PDFs degrade. Every error here propagates
  to Step 2, which cannot recover information GROBID missed.
- The `config.json` here controls client-side timeout / batch size; the
  server-side models and limits live in the Docker image.

## Files

```
run_grobid.py          # CLI wrapper (cleaned up from the pipeline's run_grobid_python.py)
config.json            # GROBID client config (server URL, timeout, batch size)
grobid_client/         # the standard kermitt2 grobid_client_python package (bundled)
requirements.txt
```
