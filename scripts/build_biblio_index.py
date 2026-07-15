#!/usr/bin/env python3
"""
build_biblio_index.py — bibliographic index from the March 2026 Crossref dump, keyed
for exact metadata search WITH the page field (which OpenAlex Solr lacks).

Only records carrying year AND volume are indexed (metadata-searchable journal
articles). Enables local exact 5-field matching: journal + year + volume + page +
first-author, plus doi->page lookup for mismatch detection. Resumable via _done.

Schema:
  biblio(doi, journal_norm, year, volume, first_page, author1)
    index (journal_norm, year, volume)  -- metadata search
    index (doi)                          -- mismatch lookup
"""
import gzip, json, multiprocessing, re, sqlite3, time
from pathlib import Path

DUMP_DIR = Path('/home/rwang/crossref/data/March_2026_Public_Data_File_from_Crossref')
DB_PATH  = Path('/home/rwang/crossref/biblio_index.db')
WORKERS  = 16

_PUNCT = re.compile(r'[^\w\s]', re.U)
_WS    = re.compile(r'\s+')

def jnorm(name):
    """Journal normalization — identical to journal_authority._norm()."""
    if not name:
        return ''
    s = name.lower().replace('&', ' and ')
    s = _PUNCT.sub(' ', s)
    s = _WS.sub(' ', s).strip()
    if s.startswith('the '):
        s = s[4:]
    return s

def process_file(path):
    rows = []
    try:
        with gzip.open(path, 'rt', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                doi = (r.get('DOI') or '').strip().lower()
                if not doi:
                    continue
                ct = r.get('container-title') or []
                jn = jnorm(ct[0]) if ct else ''
                if not jn:
                    continue
                year = None
                for k in ('published', 'published-print', 'published-online', 'issued'):
                    dp = (r.get(k) or {}).get('date-parts')
                    if dp and dp[0]:
                        year = dp[0][0]
                        break
                vol = str(r.get('volume') or '').strip()
                if not (year and vol):
                    continue   # only index metadata-searchable records
                pg = str(r.get('page') or '').split('-')[0].strip()
                art = str(r.get('article-number') or '').strip()
                authors = r.get('author') or []
                a1 = (authors[0].get('family') or '').strip().lower() if authors else ''
                rows.append((doi, jn, year, vol, pg, art, a1))
    except Exception:
        pass
    return (str(path), rows)

def main():
    con = sqlite3.connect(str(DB_PATH))
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA synchronous=OFF')
    con.execute('PRAGMA cache_size=-2000000')
    con.execute('CREATE TABLE IF NOT EXISTS biblio(doi TEXT, journal_norm TEXT, '
                'year INTEGER, volume TEXT, first_page TEXT, article_num TEXT, author1 TEXT)')
    con.execute('CREATE TABLE IF NOT EXISTS _done(filename TEXT PRIMARY KEY)')
    con.commit()
    done = {r[0] for r in con.execute('SELECT filename FROM _done')}

    files = [p for p in sorted(DUMP_DIR.glob('*.jsonl.gz')) if str(p) not in done]
    print(f'{len(files):,} files to process ({len(done):,} already done)', flush=True)
    t0 = time.time(); nrows = 0
    with multiprocessing.Pool(WORKERS) as pool:
        for i, (path, rows) in enumerate(pool.imap_unordered(process_file, files), 1):
            if rows:
                con.executemany('INSERT INTO biblio VALUES(?,?,?,?,?,?,?)', rows)
                nrows += len(rows)
            con.execute('INSERT OR IGNORE INTO _done VALUES(?)', (path,))
            if i % 200 == 0:
                con.commit()
                el = time.time() - t0
                print(f'  [{i:,}/{len(files):,}] rows={nrows:,} {i/el:.1f} files/s '
                      f'ETA {(len(files)-i)/(i/el)/60:.0f}m', flush=True)
    con.commit()
    print('building indexes...', flush=True)
    con.execute('CREATE INDEX IF NOT EXISTS idx_biblio ON biblio(journal_norm, year, volume)')
    con.execute('CREATE INDEX IF NOT EXISTS idx_biblio_doi ON biblio(doi)')
    con.commit()
    print(f'DONE. rows={nrows:,}  DB={DB_PATH}', flush=True)
    con.close()

if __name__ == '__main__':
    main()
