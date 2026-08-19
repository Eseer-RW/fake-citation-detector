#!/bin/bash
cd /space/rwang/fake-citation-detector/scripts
export OA_LOCAL_INDEX=/dev/shm/oa_index.db
export BIBLIO_DB=/dev/shm/biblio_index.db
TEI=/space/eric/citation_data/arxiv/tei/new
OUT=/space/rwang/fake-citation-detector/scripts/arxiv_sweep_v7
mkdir -p $OUT
N=${1:-30}; W=${2:-1}
mapfile -t PAIRS < <(python3 - "$N" <<PY
import sys,math
months=[]; y,m=9,1
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
  setsid python3 arxiv_sweep.py --tei-source $TEI --start $S --end $E --k 1000 \
    --workers $W --solr-workers $W --outdir $OUT >> $OUT/run_${S}_${E}.log 2>&1 < /dev/null &
  sleep 0.5
done
