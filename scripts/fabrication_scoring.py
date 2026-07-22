"""fabrication_scoring.py — rank a NOT-FOUND citation by how likely it is genuinely
fabricated vs. merely uncovered (old / regional / gray literature).

A "not found" verdict is not itself proof of fabrication — real but poorly-indexed works
(books, proceedings, old or non-English papers) also fail to match. This prior separates the
two so a reviewer can triage: fabrications (LLM-style) look *clean* — a title-bearing article
in a real, major journal with complete and recent metadata — whereas real-but-uncovered works
skew title-less, older, or in venues the authority can't resolve.

Score in [0,1]; band low(<0.45) / medium / high(>=0.7). Applies only to unmatched refs.
"""
from journal_authority import resolve as _resolve


def fabrication_likelihood(ref, is_nonacademic=None):
    reasons = []
    title = getattr(ref, "title", None)
    if (is_nonacademic and is_nonacademic(ref)) or not title:
        return 0.05, "low", ["no title / non-academic → gray-lit, not a fabricated article"]

    score = 0.30                                  # baseline for a title-bearing miss
    jr = getattr(ref, "journal", None)
    if jr and _resolve(jr):
        score += 0.25; reasons.append("journal resolves to a known venue")
    else:
        reasons.append("journal unresolved (regional/obscure or invented)")

    if getattr(ref, "year", None) and getattr(ref, "volume", None) and getattr(ref, "first_page", None):
        score += 0.20; reasons.append("complete metadata (year+vol+page)")
    else:
        reasons.append("incomplete metadata")

    try:
        y = int(getattr(ref, "year", 0) or 0)
        if y >= 2020:
            score += 0.20; reasons.append("recent year (well-indexed era)")
        elif 0 < y < 2005:
            score -= 0.15; reasons.append("old year (indexing gaps expected)")
    except Exception:
        pass

    score = max(0.0, min(1.0, score))
    band = "high" if score >= 0.70 else ("medium" if score >= 0.45 else "low")
    return round(score, 2), band, reasons
