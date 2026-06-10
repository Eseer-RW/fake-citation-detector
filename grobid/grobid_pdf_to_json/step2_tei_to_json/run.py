#!/usr/bin/env python3
# -*- coding: utf8 -*-
"""
CLI wrapper around grobid_tool.py — turns GROBID TEI-XML into per-paper JSON.

This is Stage 2 of the citation pipeline: it consumes the TEI-XML that GROBID
produces from a PDF (Stage 1) and emits the three per-paper JSON products
described in README.md.

Usage
-----
    # one TEI-XML file
    python run.py --xml path/to/2006.06096v3.tei.xml --out ./out

    # a directory of *.tei.xml / *.xml files (non-recursive)
    python run.py --dir path/to/tei_xml_folder/ --out ./out

Output layout (under --out):
    out/cited_sent/<name>.json      # references + the sentences that cite them
    out/model_dataset/<name>.json   # every sentence + context + citation label
    out/header_info/<name>.json     # title / authors / doi / issn

A paper is silently skipped (prints a reason) when GROBID gave us no body,
no title, no authors, <10 sentences, or no citing sentence — this mirrors the
original pipeline behaviour.
"""
import argparse
import glob
import os
import sys

# make the bundled NLPtools package importable regardless of CWD
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import grobid_tool


def main():
    ap = argparse.ArgumentParser(description="GROBID TEI-XML -> per-paper JSON")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--xml", help="a single TEI-XML file")
    src.add_argument("--dir", help="a directory of *.xml / *.tei.xml files")
    ap.add_argument("--out", default="./out", help="output directory (default ./out)")
    ap.add_argument("--overwrite", action="store_true",
                    help="reprocess files even if their cited_sent JSON already exists")
    args = ap.parse_args()

    cited = os.path.join(args.out, "cited_sent")
    model = os.path.join(args.out, "model_dataset")
    header = os.path.join(args.out, "header_info")
    for d in (cited, model, header):
        os.makedirs(d, exist_ok=True)

    if args.xml:
        paths = [args.xml]
    else:
        paths = sorted(glob.glob(os.path.join(args.dir, "*.xml")))

    ok = 0
    for p in paths:
        name = os.path.basename(p)
        # grobid_tool names its outputs with the original name minus the last 4
        # chars (".xml"), so match that here for the resume/skip check.
        stem = name[:-4]
        if not args.overwrite and os.path.isfile(os.path.join(cited, stem + ".json")):
            continue
        ok += grobid_tool.process_xml_fromPDF_singlefile(p, cited, model, header)

    print(f"\nDone. {ok}/{len(paths)} paper(s) produced output under {args.out}")


if __name__ == "__main__":
    main()
