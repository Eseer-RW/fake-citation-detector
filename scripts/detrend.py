import csv, sys, collections
rows = []
for d in csv.DictReader(open(sys.argv[1])):
    mo = d["month"]; yy = 2000 + int(mo[:2]); mm = int(mo[2:])
    refs = int(d["sum_refs"]); nfa = int(d["sum_nf_academic"])
    if refs < 200: continue
    rows.append((yy + (mm - 1) / 12.0, yy, refs, nfa, nfa / refs))
rows.sort()

def ols(xs, ys):
    n = len(xs); mx = sum(xs) / n; my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs); sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx; return my - b * mx, b

pre = [r for r in rows if r[0] < 2023.0]
post = [r for r in rows if r[0] >= 2023.0]
a, b = ols([r[0] for r in pre], [r[4] * 100 for r in pre])
print(f"pre-LLM (2019-2022) trend: {b:+.3f} pp/yr  (declining pre-existing)")
print(f"extrapolated pre-LLM rate at 2023.0 = {a + b*2023:.2f}%, at 2025.0 = {a + b*2025:.2f}%\n")

print("year  actual%  extrap-trend%  EXCESS-vs-trend(pp)")
by = collections.defaultdict(list)
for r in rows: by[r[1]].append(r)
for yy in sorted(by):
    rs = by[yy]
    act = sum(x[3] for x in rs) / sum(x[2] for x in rs) * 100
    ext = a + b * (yy + 0.5)
    print(f"{yy}   {act:6.2f}   {ext:6.2f}      {act-ext:+6.2f}")

pe = [r[4] * 100 - (a + b * r[0]) for r in post]
ae, be = ols([r[0] for r in post], pe)
print(f"\npost-2022 mean excess vs pre-LLM trend = {sum(pe)/len(pe):+.2f} pp")
print(f"post-2022 excess TREND = {be:+.3f} pp/yr")
# 2024+ only (Zhao's steep-rise window)
p24 = [r[4]*100 - (a + b*r[0]) for r in rows if r[0] >= 2024.0]
print(f"2024+ mean excess vs pre-LLM trend = {sum(p24)/len(p24):+.2f} pp")
print("(Zhao: arXiv +0.39pp excess by Aug-2025, steep rise from mid-2024.)")
