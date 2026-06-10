#!/usr/bin/env python3
# -*- coding: utf8 -*-
"""
Step 1 of the citation pipeline: PDF -> TEI-XML via a running GROBID server.

This sends every PDF under --input to a GROBID server (default
http://localhost:8070) and writes one <name>.tei.xml per PDF under --output.
That TEI-XML is exactly what Step 2 (../step2_tei_to_json/) consumes.

It is a cleaned-up CLI version of the pipeline's original run_grobid_python.py
(which had hardcoded /data/yuan paths and a YYMM folder filter). The GROBID
flags below match what the pipeline used in production:
    include_raw_citations=True   -> keep the raw reference string in the TEI
    consolidate_citations=False  -> do NOT call CrossRef to "fix" references
    consolidate_header=False     -> do NOT call CrossRef to "fix" the header

Prerequisite: a GROBID server must be running. The easiest way is Docker:
    docker run --rm -p 8070:8070 grobid/grobid:0.7.1
    # or with GPU:
    docker run --rm --gpus all -p 8070:8070 grobid/grobid:0.7.1

Usage
-----
    python run_grobid.py --input ./pdfs --output ./tei
    python run_grobid.py --input ./pdfs --output ./tei --server http://localhost:8070 --n 10
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grobid_client.grobid_client import GrobidClient


def main():
    ap = argparse.ArgumentParser(description="PDF -> TEI-XML via GROBID")
    ap.add_argument("--input", required=True, help="directory of PDFs (searched recursively)")
    ap.add_argument("--output", required=True, help="directory to write *.tei.xml into")
    ap.add_argument("--server", default="http://localhost:8070",
                    help="GROBID server URL (default http://localhost:8070)")
    ap.add_argument("--n", type=int, default=10,
                    help="concurrent requests to the server (default 10; tune to server CPU)")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "config.json"),
                    help="GROBID client config.json (timeouts, batch size)")
    args = ap.parse_args()

    if not os.path.isdir(args.input):
        sys.exit(f"--input directory not found: {args.input}")
    os.makedirs(args.output, exist_ok=True)

    # config.json carries timeout/batch_size; we override the server URL from --server.
    host = args.server.replace("http://", "").replace("https://", "")
    server, _, port = host.partition(":")
    client = GrobidClient(grobid_server=server, grobid_port=port or "8070",
                          config_path=args.config)

    t0 = time.time()
    client.process(
        "processFulltextDocument",
        args.input,
        output=args.output,
        n=args.n,
        include_raw_citations=True,
        consolidate_citations=False,
        consolidate_header=False,
        force=True,
    )
    print(f"\nDone. TEI-XML written to {args.output} in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
