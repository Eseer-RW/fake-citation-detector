#!/bin/bash
# Run parse_refs.py on all OpenAlex PDFs, write results to results_openalex_v2.txt
cd /home/rwang/fake-citation-detector/scripts
OUT=results_openalex_v2.txt
echo '' > $OUT

find samples/openalex_pdfs -name '*.pdf' | sort | while read pdf; do
    echo '' >> $OUT
    echo '══════════════════════════════════════════════════════════════════════' >> $OUT
    echo "FILE: $pdf" >> $OUT
    echo '══════════════════════════════════════════════════════════════════════' >> $OUT
    python3 parse_refs.py "$pdf" >> $OUT 2>&1
done

echo '' >> $OUT
echo 'BATCH COMPLETE' >> $OUT
