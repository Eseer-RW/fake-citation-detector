#!/bin/bash
for i in $(seq 1 240); do [ -f /dev/shm/v8copy.flag ] && break; sleep 15; done
[ -f /dev/shm/v8copy.flag ] || exit 1
o1=$(stat -c %s /space/rwang/oa_index/oa_index.db); n1=$(stat -c %s /dev/shm/oa_index.db)
o2=$(stat -c %s /space/rwang/crossref/biblio_index.db); n2=$(stat -c %s /dev/shm/biblio_index.db)
[ "$o1" = "$n1" ] && [ "$o2" = "$n2" ] || exit 1
bash /space/rwang/fake-citation-detector/scripts/run_v8_sharded.sh 16 1 5000
