#!/bin/bash
# v7: 200k-paper campaign. K=1000 x months 0901..2606 (~210 months). Local index. Resumable.
cd /space/rwang/fake-citation-detector/scripts
export OA_LOCAL_INDEX=/space/rwang/oa_index/oa_index.db
TEI=/space/eric/citation_data/arxiv/tei/new
OUT=/space/rwang/fake-citation-detector/scripts/arxiv_sweep_v7
mkdir -p $OUT
echo "V7_START $(date -u +%Y-%m-%dT%H:%M:%SZ) epoch=$(date +%s)" >> $OUT/run.log
python3 arxiv_sweep.py --tei-source $TEI --start 0901 --end 2606 --k 1000 \
  --workers 48 --solr-workers 48 --outdir $OUT >> $OUT/run.log 2>&1
echo "V7_ALL_DONE $(date -u +%Y-%m-%dT%H:%M:%SZ) epoch=$(date +%s)" >> $OUT/run.log
