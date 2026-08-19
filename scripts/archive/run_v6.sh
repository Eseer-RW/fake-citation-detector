#!/bin/bash
# run_v6.sh — EXTENDED-BASELINE Zhao replication run.
#   source : Eric's GROBID-TEI corpus (--tei-source)  => no GROBID, free extraction
#   verify : local oa_index.db (486M works)            => no Solr, ~ms lookups
# Covers 2010-01 .. 2026-06 (pre-LLM baseline 2010-2018 + post-LLM 2019-2026) at K=100 --
# the long pre-LLM baseline is the piece needed to pin the baseline functional form (the
# thing that decides whether the post-2022 excess is real). Resumable (skips done months).
set -u
cd /space/rwang/fake-citation-detector || exit 1
source .venv/bin/activate
export OA_LOCAL_INDEX=/space/rwang/oa_index/oa_index.db
OUT=/space/rwang/fake-citation-detector/results/arxiv_sweep_v6
mkdir -p "$OUT"
echo "V6_START $(date '+%F %T')  (TEI-source + local index, 2010-2026, K=100)"
python3 -u scripts/arxiv_sweep.py \
  --start 1001 --end 2606 --k 100 \
  --workers 32 --solr-workers 16 \
  --tei-source /space/eric/citation_data/arxiv/tei/new \
  --endpoint processFulltextDocument --grobid-timeout 300 \
  --tei-cache /space/rwang/tei_cache \
  --outdir "$OUT"
echo "V6_SWEEP_DONE $(date '+%F %T')"

echo; echo "################ V6 ANALYSIS ################"
R="$OUT/refs_1001_2606_k100.jsonl"
for s in refquality.py stratify_citedyear.py analyze_power.py; do
  echo; echo "--- $s ---"; python3 -u "scripts/$s" "$R" 2>&1 | head -240 || echo "(failed)"
done
echo "V6_ALL_DONE $(date '+%F %T')"
