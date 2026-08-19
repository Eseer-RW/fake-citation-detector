#!/bin/bash
# wait for tmpfs copy to finish, verify integrity, then launch the 12 sharded run
for i in $(seq 1 240); do [ -f /dev/shm/copy.flag ] && break; sleep 15; done
[ -f /dev/shm/copy.flag ] || { echo "COPY NEVER FINISHED" > /dev/shm/launch.flag; exit 1; }
# integrity: sizes match originals?
o1=$(stat -c %s /space/rwang/oa_index/oa_index.db); n1=$(stat -c %s /dev/shm/oa_index.db)
o2=$(stat -c %s /space/rwang/crossref/biblio_index.db); n2=$(stat -c %s /dev/shm/biblio_index.db)
if [ "$o1" != "$n1" ] || [ "$o2" != "$n2" ]; then echo "SIZE MISMATCH oa:$o1/$n1 bib:$o2/$n2" > /dev/shm/launch.flag; exit 1; fi
# quick sqlite sanity on the shm copy
python3 - <<PY >> /dev/shm/launch.flag 2>&1
import sqlite3
c=sqlite3.connect("file:/dev/shm/oa_index.db?mode=ro",uri=True)
print("OA rows check:", c.execute("SELECT count(*) FROM (SELECT 1 FROM works LIMIT 1)").fetchone())
PY
echo "LAUNCHING $(date +%s)" >> /dev/shm/launch.flag
bash /space/rwang/fake-citation-detector/scripts/run_v7_sharded.sh
