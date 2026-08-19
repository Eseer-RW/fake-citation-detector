import os, sys, collections, random, requests
from difflib import SequenceMatcher
sys.path.insert(0, "/space/rwang/fake-citation-detector/scripts")
from title_normalize import normalize_title_key
from pymongo import MongoClient
from dotenv import load_dotenv
load_dotenv("/space/rwang/fake-citation-detector/.env")
random.seed(42)
col = MongoClient(os.getenv("MONGO_URI"), serverSelectionTimeoutMS=15000)["crs"]["crossref"]
H = {"User-Agent": "FakeCitationValidator/1.0 (mailto:rwang@insilicom.com)"}

PAPERS_PER_YEAR = 1000
FUZZY_SUBSAMPLE = 400            # exact-misses fuzzy-checked per year, then rate is scaled
YEARS = range(2016, 2025)        # exclude 2025 (indexing-lag buffer)

# 1) sample a big pool once, bucket papers by CITING-paper year, cap PAPERS_PER_YEAR
print("sampling paper pool...", flush=True)
pool = col.aggregate([{"$sample": {"size": 300000}},
    {"$match": {"reference-count": {"$gt": 10}, "reference": {"$exists": True}}},
    {"$project": {"issued": 1, "reference.article-title": 1}}], allowDiskUse=True, batchSize=2000)
papers_by_year = collections.defaultdict(int)
titles_by_year = collections.defaultdict(list)
for d in pool:
    try: yr = d["issued"]["date-parts"][0][0]
    except Exception: continue
    if yr not in YEARS or papers_by_year[yr] >= PAPERS_PER_YEAR:
        continue
    papers_by_year[yr] += 1
    for r in d.get("reference", []):
        t = r.get("article-title")
        if t and len(t) > 12:
            titles_by_year[yr].append(t)

def exact_found(title):
    return col.find_one({"title_norm": normalize_title_key(title)}, {"_id": 1}) is not None

def fuzzy_found(title):
    try:
        items = requests.get("https://api.crossref.org/works",
            params={"query.bibliographic": title, "rows": 3, "select": "title"},
            headers=H, timeout=12).json()["message"]["items"]
        for x in items:
            c = (x.get("title") or [""])[0]
            if c and SequenceMatcher(None, title.lower(), c.lower()).ratio() >= 0.85:
                return True
    except Exception:
        pass
    return False

print("\nyear  papers  refs   exact_unverif%  fuzzy_unverif%(est)  variance%(fuzzy-recovers)", flush=True)
rows = []
for yr in sorted(titles_by_year):
    titles = titles_by_year[yr]
    n = len(titles)
    if n < 100:
        continue
    ex_nf = [t for t in titles if not exact_found(t)]
    ex_rate = len(ex_nf) / n * 100
    # subsample exact-misses for the fuzzy arm, estimate P(fuzzy also fails | exact fails)
    sub = random.sample(ex_nf, min(FUZZY_SUBSAMPLE, len(ex_nf)))
    p_fuzzy_fail = (sum(1 for t in sub if not fuzzy_found(t)) / len(sub)) if sub else 0.0
    fz_rate = ex_rate * p_fuzzy_fail
    rows.append((yr, papers_by_year[yr], n, ex_rate, fz_rate))
    print(f"{yr}  {papers_by_year[yr]:5}  {n:5}  {ex_rate:8.2f}      {fz_rate:8.2f}           {ex_rate - fz_rate:8.2f}", flush=True)

import numpy as np
ys = np.array([r[0] for r in rows]); ex = np.array([r[3] for r in rows]); fz = np.array([r[4] for r in rows])
se = np.polyfit(ys, ex, 1)[0]; sf = np.polyfit(ys, fz, 1)[0]
print(f"\n=== SLOPES (pp/yr, linear fit over {len(rows)} year-points) ===", flush=True)
print(f"exact_unverif      : {se:+.3f}", flush=True)
print(f"fuzzy_unverif (est): {sf:+.3f}", flush=True)
print(f"method-attributable difference (exact - fuzzy): {se - sf:+.3f}", flush=True)
print(f"title-variance gap slope (fuzzy recovers)     : {np.polyfit(ys, ex - fz, 1)[0]:+.3f}", flush=True)
print("\nInterpretation: if exact rises but fuzzy is flat, the extra slope is matching-method artifact.", flush=True)
print("DONE", flush=True)
