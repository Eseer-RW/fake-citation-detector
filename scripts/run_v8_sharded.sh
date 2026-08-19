#!/bin/bash
# v8: 1M-paper campaign with the UPGRADED detector (fab_flag channels).
# Box-safety per feedback-shared-box-limits: tmpfs = the two proven indexes ONLY (168GB),
# 16 single-worker shards, no oa_fts in RAM.
cd /space/rwang/fake-citation-detector/scripts
export OA_LOCAL_INDEX=/dev/shm/oa_index.db
export BIBLIO_DB=/dev/shm/biblio_index.db
TEI=/space/eric/citation_data/arxiv/tei/new
OUT=/space/rwang/fake-citation-detector/scripts/arxiv_sweep_v8
mkdir -p $OUT
N=${1:-16}; W=${2:-1}; K=${3:-5000}
mapfile -t PAIRS < <(python3 - "$N" <<PY
import sys,math
months=[]; y,m=7,4
while (y,m)<=(26,6):
    months.append("%02d%02d"%(y,m)); m+=1
    if m>12: m=1;y+=1
N=int(sys.argv[1]); sz=math.ceil(len(months)/N)
for i in range(0,len(months),sz):
    c=months[i:i+sz]; print(c[0],c[-1])
PY
)
for p in "${PAIRS[@]}"; do
  set -- $p; S=$1; E=$2
  echo "SHARD_START $S-$E $(date +%s)" >> $OUT/run_${S}_${E}.log
  setsid python3 arxiv_sweep.py --tei-source $TEI --start $S --end $E --k $K \
    --workers $W --solr-workers $W --outdir $OUT >> $OUT/run_${S}_${E}.log 2>&1 < /dev/null &
  sleep 0.5
done
