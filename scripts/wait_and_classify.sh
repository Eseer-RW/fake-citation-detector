#!/bin/bash
cd /space/rwang/fake-citation-detector/scripts
export OA_FTS=/dev/shm/oa_fts.db
OUT=/space/rwang/_speedtest/fullclass
# wait for FTS in RAM
for i in $(seq 1 120); do [ -f /dev/shm/fts.flag ] && break; sleep 15; done
# the 30 canonical shard files (N=30 split)
mapfile -t FILES < <(python3 - <<PY
import math,os
months=[];y,m=9,1
while (y,m)<=(26,6):
    months.append("%02d%02d"%(y,m));m+=1
    if m>12:m=1;y+=1
N=30;sz=math.ceil(len(months)/N)
D="/space/rwang/fake-citation-detector/scripts/arxiv_sweep_v7"
for i in range(0,len(months),sz):
    f=os.path.join(D,"refs_%s_%s_k1000.jsonl"%(months[i],months[i:i+sz][-1]))
    if os.path.exists(f): print(f)
PY
)
echo "launching ${#FILES[@]} shards $(date +%s)" > $OUT/launch.log
for f in "${FILES[@]}"; do
  setsid python3 full_classify.py "$f" "$OUT" >> $OUT/launch.log 2>&1 < /dev/null &
done
wait
echo "ALL_SHARDS_DONE $(date +%s)" >> $OUT/launch.log
