#!/usr/bin/env python3
"""
v7_adjudicate.py — shrink the 220 gold-verify FAB_CANDIDATEs to a tight residual for human/web check.
Two filters, both conservative (only REMOVE a candidate when we're confident it's real/not-an-article):
  1. non_article: books, handbooks, lecture notes, theses, standards, datasets, and PARSING JUNK
     (proof/theorem/lemma fragments, section text) — real-but-not-a-journal-article, cannot be a
     hallucinated paper.
  2. local FTS recheck against oa_fts.db (484M OpenAlex titles) with token-containment >=0.6 — clears
     the real preprints/articles that the THROTTLED OpenAlex API failed to return.
Whatever survives BOTH is a genuine fabrication candidate -> web-verify each by hand.
"""
import sqlite3, re, csv, collections, os
TSV="/space/rwang/fake-citation-detector/scripts/arxiv_sweep_v7/gold_verify.tsv"
FTS="/space/rwang/oa_index/oa_fts.db"
STOP=set("the a an of for and or to in on with using via based from into over under between is are we our this that new".split())
def toks(s):
    s=re.sub(r'(\w)-\s*(\w)',r'\1\2',s or "")
    return [w for w in re.split(r'[^a-z0-9]+',s.lower()) if len(w)>2 and w not in STOP]
def contain(ct,cand):
    cs=set(toks(cand)); return sum(1 for w in ct if w in cs)/len(ct) if ct else 0
NONART=re.compile(r'\bhandbook\b|\bencyclopedia\b|lecture notes|\bproof\b|\btheorem\b|\blemma\b|\bcorollary\b|'
                  r'\bvol\.|\bchapter\b|\bpress\b|springer|elsevier|wiley|\bthesis\b|dissertation|'
                  r'to appear|in press|private communication|preprint$|\bbook\b|monograph|'
                  r'\bstandard\b|\bstandards\b|\bRFC\b|datasheet|user guide|\bmanual\b|technical report|'
                  r'tractatus|commentationes|comm\. |\bed\.\b|editor|proceedings of the', re.I)
def looks_nonarticle(title, raw):
    if NONART.search(raw) or NONART.search(title): return True
    if len(toks(title))<4: return True            # too short to be a real article title / parsing frag
    if re.match(r'^(proof|remark|definition|lemma|theorem|note|figure|table|eq)', title.strip(), re.I): return True
    # heavy non-ASCII (non-English) -> can't reliably check, treat as real-uncertain, exclude from fab
    letters=[c for c in title if c.isalpha()]
    if letters and sum(1 for c in letters if ord(c)>127)/len(letters) > 0.15: return True
    return False

con=sqlite3.connect("file:%s?mode=ro"%FTS,uri=True)
def in_local(title):
    ct=toks(title); content=[t for t in ct if t not in STOP]
    for sel in (content[:8], sorted(content,key=len,reverse=True)[:5], sorted(content,key=len,reverse=True)[:3]):
        if len(sel)<2: continue
        q=" ".join('"%s"'%t for t in sel)
        try: rows=[r[0] for r in con.execute("SELECT title_norm FROM docs WHERE docs MATCH ? LIMIT 60",(q,)).fetchall()]
        except Exception: rows=[]
        if rows and max(contain(ct,c) for c in rows)>=0.6: return True
    return False

rows=[r for r in csv.DictReader(open(TSV),delimiter="\t") if r["verdict"]=="FAB_CANDIDATE"]
cls=collections.Counter(); residual=[]; per_era=collections.defaultdict(lambda:[0,0])
for r in rows:
    yr=r["year"]; t=r["ref_title"]; raw=r["raw"]
    era="control(16-19)" if yr<"2020" else "LLM(23-26)"; per_era[era][0]+=1
    if looks_nonarticle(t,raw): cls["non_article_or_parsejunk"]+=1; continue
    if in_local(t): cls["real_in_local_484M"]+=1; continue
    cls["RESIDUAL_fab_candidate"]+=1; residual.append((yr,t,raw)); per_era[era][1]+=1
print("=== adjudicating %d raw FAB_CANDIDATEs ==="%len(rows))
for k in ("real_in_local_484M","non_article_or_parsejunk","RESIDUAL_fab_candidate"):
    print("  %-26s %4d"%(k,cls[k]))
print("\n  residual by era (survivors need web-check):")
for e in ("control(16-19)","LLM(23-26)"): print("    %-16s %d survive / %d candidates"%(e,per_era[e][1],per_era[e][0]))
print("\n=== RESIDUAL to web-verify (%d) ==="%len(residual))
for yr,t,raw in residual: print("  [%s] %s :: %s"%(yr,t[:75],raw[:120]))
with open("/space/rwang/_speedtest/fab_residual_final.tsv","w") as o:
    o.write("year\ttitle\traw\n")
    for yr,t,raw in residual: o.write("%s\t%s\t%s\n"%(yr,t.replace(chr(9),' '),raw.replace(chr(9),' ')))
print("\nwrote fab_residual_final.tsv")
