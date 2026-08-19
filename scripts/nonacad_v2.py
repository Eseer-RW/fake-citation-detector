"""
nonacad_v2.py — candidate replacement for is_likely_nonacademic, plus its evaluation.

The shipped heuristic catches 1 of 32 non-article references (3.1% recall) because
FIVE of its six rules require `not ref.title` -- so a website or book that GROBID gave
a title to sails straight through.

ASYMMETRY THAT DRIVES THE DESIGN. A false negative (missing a website) leaves noise in
the denominator. A false POSITIVE (calling a real paper non-academic) removes it from
the fabrication denominator entirely -- it would hide a genuine fabrication. So every
rule below keys on an explicit, unambiguous marker; nothing infers from absence.
"""
import re

_URL      = re.compile(r"https?://|www\.", re.I)
_ARXIVURL = re.compile(r"arxiv\.org|doi\.org|dx\.doi\.org", re.I)
_ACCESS   = re.compile(r"\baccessed\b|\bretrieved\b|available (at|from|online)|\blast visited\b", re.I)
_PATENT   = re.compile(r"\bpatent\b|\bUS\s?\d{1,2},?\d{3},?\d{3}\b", re.I)
_THESIS   = re.compile(r"\bph\.?\s?d\.?\s+(thesis|dissertation)\b|\bmaster'?s? thesis\b|"
                       r"\bdissertation\b|\bdiss\.\s", re.I)
_REPORT   = re.compile(r"\btech(nical)?\.?\s*rep(ort|\.)|white paper|working paper|"
                       r"\bpreprint server\b|\bpolicy brief\b", re.I)
_STANDARD = re.compile(r"\bRFC\s?\d+\b|\bISO[/\s-]?\d+|\bIEEE\s+Std\b|\bITU-[RT]\b|"
                       r"\bANSI\b|\brecommendation\s+[A-Z]\.\d+", re.I)
_SOFTWARE = re.compile(r"github\.com|gitlab\.com|\bpypi\b|\bCRAN\b|\bzenodo\.org\b", re.I)
_BOOKPUB  = re.compile(r"\b(springer|wiley|elsevier|pearson|mcgraw[- ]hill|academic press|"
                       r"cambridge univ|oxford univ|princeton univ|mit press|crc press|"
                       r"nova science|world scientific)\b", re.I)
_BOOKISH  = re.compile(r"\b\d+(st|nd|rd|th)\s+edition\b|\bISBN\b|\b\d{2,4}\s?pp?\.\b|"
                       r"\bchapter\b|\bhandbook\b|\bencyclopedia\b|\bmonograph\b|"
                       r"\bin:\s|\(ed(s|itors)?\.?\)|\bedited by\b|\beds?\.\s|"
                       r"\bsupplementary material\b|\bin\s+[A-Z][a-z]+\s+[IVXL]+\b", re.I)
_CONFY    = re.compile(r"\bproceedings\b|\bconf(erence|\.)|\bsymposium\b|\bworkshop\b|"
                       r"\bin proc\b|\bLNCS\b|\bNeurIPS\b|\bICML\b|\bICLR\b|\bCVPR\b|"
                       r"\bECCV\b|\bICCV\b|\bAAAI\b|\bIJCAI\b|\bBMVC\b|\bSIGKDD\b|"
                       r"\binternational conference\b|\bneural information processing\b|"
                       r"\badvances in neural\b|\bcurran associates\b|\bpmlr\b|"
                       r"\bannual meeting\b|\bin\s+proc(\.|eedings)", re.I)
# NOTE: a bare page range is deliberately NOT here. Book chapters have "pp. 81-94"
# too, and including it blocked the book rule on three chapters in the labelled set.
_AFFIL    = re.compile(r"\b(institute|university|universit[ée]|academy of sciences|"
                       r"department of|faculty of|laborator(y|ies)|school of)\b", re.I)
_ADDRESS  = re.compile(r"\b\d{1,4}\s+[A-Z][a-z]+\s+(St|Street|Ave|Avenue|Rd|Road|Blvd)\b|"
                       r"\b\d{5,6},?\s+[A-Z][a-z]+|\bP\.?O\.?\s?Box\b", re.I)
_CITEISH  = re.compile(r"\bvol\.?\s?\d+|\bpp?\.\s?\d+|\(\d{4}\)|\b(19|20)\d{2}[a-z]?[.,;]|"
                       r"\bdoi\s*:|\barxiv\s*:|https?://", re.I)
_JOURNALY = re.compile(r"\bvol\.?\s?\d+|\bno\.?\s?\d+|\bissue\s?\d+|"
                       r"\bdoi\s*:|\barxiv\s*:", re.I)


def _which_rule(ref):
    """Same logic, but returns the rule name -- so a misfire can be attributed."""
    raw = (getattr(ref, "raw", None) or "")
    if getattr(ref, "doi", None):
        return None
    if _URL.search(raw) and not _ARXIVURL.search(raw):
        if _ACCESS.search(raw) or not _JOURNALY.search(raw):
            return "url"
    if _SOFTWARE.search(raw):
        return "software"
    if _ACCESS.search(raw) and not _JOURNALY.search(raw):
        return "access-phrase"
    if _PATENT.search(raw):   return "patent"
    if _THESIS.search(raw):   return "thesis"
    if _REPORT.search(raw):   return "report"
    if _STANDARD.search(raw): return "standard"
    if ((_BOOKPUB.search(raw) or _BOOKISH.search(raw))
            and not _JOURNALY.search(raw) and not _CONFY.search(raw)):
        return "book(pub=%s,ish=%s)" % (bool(_BOOKPUB.search(raw)), bool(_BOOKISH.search(raw)))
    if _AFFIL.search(raw) and _ADDRESS.search(raw) and not _CITEISH.search(raw):
        return "affiliation"
    if len(raw.strip()) < 15 and not getattr(ref, "title", None):
        return "debris"
    if (len(raw.strip()) < 60 and not _CITEISH.search(raw)
            and not getattr(ref, "journal", None) and not getattr(ref, "volume", None)):
        return "debris-nocite"
    return None


def is_likely_nonacademic_v2(ref) -> bool:
    """True when the reference is clearly NOT a journal/conference/preprint article."""
    raw = (getattr(ref, "raw", None) or "")
    if getattr(ref, "doi", None):
        return False                      # a DOI means a registered scholarly object

    # 1. web resource: a URL that is not an arXiv/DOI link, plus either an access
    #    phrase or no journal-ish structure at all
    if _URL.search(raw) and not _ARXIVURL.search(raw):
        if _ACCESS.search(raw) or not _JOURNALY.search(raw):
            return True
    if _SOFTWARE.search(raw):
        return True
    if _ACCESS.search(raw) and not _JOURNALY.search(raw):
        return True

    # 2. explicitly-typed non-articles
    if _PATENT.search(raw) or _THESIS.search(raw) or _REPORT.search(raw) or _STANDARD.search(raw):
        return True

    # 3. books / chapters: a publisher or book marker AND no journal structure.
    #    The second half matters -- Springer and Elsevier publish journals too.
    if ((_BOOKPUB.search(raw) or _BOOKISH.search(raw))
            and not _JOURNALY.search(raw) and not _CONFY.search(raw)):
        return True

    # 4. affiliation / address fragment: GROBID sometimes emits the author block of a
    #    paper as if it were a reference. Requires an institution AND a postal
    #    address AND no citation structure, so a real paper naming a university is safe.
    if _AFFIL.search(raw) and _ADDRESS.search(raw) and not _CITEISH.search(raw):
        return True

    # 5. parse débris: too short, or nothing that resembles a citation at all
    if len(raw.strip()) < 15 and not getattr(ref, "title", None):
        return True
    if (len(raw.strip()) < 60 and not _CITEISH.search(raw)
            and not getattr(ref, "journal", None) and not getattr(ref, "volume", None)):
        return True
    return False


if __name__ == "__main__":
    import json, sys, types, os
    sys.path.insert(0, "/space/rwang/fake-citation-detector/scripts")
    os.chdir("/space/rwang/fake-citation-detector/scripts")
    import batch_verify_years as bvy

    rows = [json.loads(l) for l in open(
        "/space/rwang/fake-citation-detector/eval/notfound_labeled.jsonl")]

    def mk(r):
        return types.SimpleNamespace(raw=r.get("raw") or "", title=r.get("title"),
                                     doi=r.get("doi"), year=r.get("year"),
                                     journal=r.get("journal"))

    def score(fn, name):
        tp = fn_ = fp = tn = 0
        wrong = []
        for r in rows:
            art = bool(r.get("article_like"))
            flagged = bool(fn(mk(r)))
            if not art and flagged:   tp += 1
            elif not art:             fn_ += 1
            elif art and flagged:
                fp += 1
                wrong.append((r.get("label"), _which_rule(mk(r)), (r.get("raw") or "")[:62]))
            else:                     tn += 1
        rec = 100.0 * tp / (tp + fn_) if tp + fn_ else 0
        prec = 100.0 * tp / (tp + fp) if tp + fp else 0
        fpr = 100.0 * fp / (fp + tn) if fp + tn else 0
        print("%-14s recall %5.1f%%  precision %5.1f%%  "
              "flagged-a-real-article %d (%.1f%%)" % (name, rec, prec, fp, fpr))
        return wrong

    print("evaluated on %d hand-labelled references "
          "(%d article-like, %d not)\n"
          % (len(rows), sum(1 for r in rows if r.get("article_like")),
             sum(1 for r in rows if not r.get("article_like"))))
    score(bvy.is_likely_nonacademic, "SHIPPED")
    wrong = score(is_likely_nonacademic_v2, "v2")
    if wrong:
        print("\nv2 wrongly flagged these REAL articles (each one would hide a")
        print("potential fabrication -- this is the number that must stay near zero):")
        for lab, rule, t in wrong:
            print("   [%s via %s] %s" % (lab, rule, t))
