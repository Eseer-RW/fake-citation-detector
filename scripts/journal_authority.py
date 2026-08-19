"""
journal_authority.py — resolve journal names (canonical / ISO-4 abbreviation /
alternate title / acronym) to a single journal identity, for EXACT journal
matching across shortened and alternate names.

Backed by journal_authority.sqlite (built by build_journal_authority.py from the
OpenAlex sources snapshot). Read-only, thread-safe, lazily loaded.

Public API:
    resolve(name) -> identity string (ISSN-L or venue_id) or None
    same_journal(a, b) -> bool
"""
import os, re, sqlite3, threading

_DB_PATH = os.environ.get(
    "JOURNAL_AUTHORITY_DB",
    str(__import__("pathlib").Path.home() / "journal_authority" / "journal_authority.sqlite"),
)

_STOP = {"of","the","and","for","in","on","at","to","a","an","&","de","la","le",
         "der","die","das","und","el","los","las"}
_PUNCT = re.compile(r"[^\w\s]", re.U)
_WS    = re.compile(r"\s+")

_local = threading.local()


def _con():
    c = getattr(_local, "con", None)
    if c is None:
        if not os.path.exists(_DB_PATH):
            _local.con = False
            return None
        c = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True,
                            check_same_thread=False)
        _local.con = c
    return c or None


def _norm(name: str) -> str:
    if not name:
        return ""
    s = name.lower().replace("&", " and ")
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    if s.startswith("the "):
        s = s[4:]
    return s


def _acronym(name: str) -> str:
    if not name:
        return ""
    words = [w for w in _PUNCT.sub(" ", name.lower()).split()
             if w and w not in _STOP]
    if len(words) < 2:
        return ""
    return "".join(w[0] for w in words)


def resolve(name: str):
    """Return the journal identity for a name, or None if unknown/ambiguous.

    Resolution order: exact alias (canonical/abbrev/alternate) → unique acronym.
    Ambiguous matches (a name mapping to >1 journal) return None so callers do
    not treat them as a confident match.
    """
    con = _con()
    if con is None:
        return None
    k = _norm(name)
    if not k:
        return None
    rows = con.execute(
        "SELECT DISTINCT identity FROM alias WHERE name_norm=?", (k,)
    ).fetchall()
    if len(rows) == 1:
        return rows[0][0]
    if len(rows) > 1:
        return None  # ambiguous canonical/alternate name
    # Fallback: acronym (only if it maps to exactly one journal)
    acr = k.replace(" ", "")
    if 2 <= len(acr) <= 10:
        rows = con.execute(
            "SELECT DISTINCT identity FROM acronym WHERE acr=?", (acr,)
        ).fetchall()
        if len(rows) == 1:
            return rows[0][0]
    return None


def canonical_name(name: str):
    """Return the canonical display_name for a journal name (resolved via the
    authority), or None. Used to normalize abbreviations to the full name that the
    Crossref bibliographic index is keyed on."""
    ident = resolve(name)
    if not ident:
        return None
    con = _con()
    if con is None:
        return None
    row = con.execute("SELECT display_name FROM journal WHERE identity=?", (ident,)).fetchone()
    return row[0] if row else None


# Abbreviations OpenAlex's snapshot simply does not carry, though it holds the
# spelled-out name. Measured: 7 of 17 common physics journals resolved to NOTHING,
# e.g. "Comm. Math. Phys" is absent while "Communications in Mathematical Physics"
# resolves fine, and "Eur. Phys. J. A" is absent while "epj a" is present. Mapping the
# abbreviation onto a form the authority DOES know is a data patch, not a matching
# change -- it is consulted only after the normal lookup has already failed.
_ABBREV_FALLBACK = {
    "comm math phys":            "Communications in Mathematical Physics",
    "commun math phys":          "Communications in Mathematical Physics",
    "eur phys j a":              "European Physical Journal A",
    "eur phys j c":              "European Physical Journal C",
    "eur phys j b":              "European Physical Journal B",
    "nucl phys":                 "Nuclear Physics",
    "nucl phys a":               "Nuclear Physics A",
    "nucl phys b":               "Nuclear Physics B",
    "nuovo cim":                 "Il Nuovo Cimento",
    "prog theor phys":           "Progress of Theoretical Physics",
    "int j theor phys":          "International Journal of Theoretical Physics",
    "annu rev nucl part sci":    "Annual Review of Nuclear and Particle Science",
    # the authority stores the full subtitle form, not the short "Royal Society A"
    "proc roy soc lond a":       "Proceedings of the Royal Society A: Mathematical, Physical and Engineering Sciences",
    "proc r soc lond a":         "Proceedings of the Royal Society A: Mathematical, Physical and Engineering Sciences",
    "proc roy soc a":            "Proceedings of the Royal Society A: Mathematical, Physical and Engineering Sciences",
    "j mod phys":                "Journal of Modern Physics",
    "rev mod phys":              "Reviews of Modern Physics",
    "astrophys j":               "The Astrophysical Journal",
    "mon not r astron soc":      "Monthly Notices of the Royal Astronomical Society",
    "j high energy phys":        "Journal of High Energy Physics",
    "class quantum grav":        "Classical and Quantum Gravity",
    "phys lett b":               "Physics Letters B",
}


def venue_ids_for(name: str):
    """Resolve a journal name to its Solr venue_id(s) (OpenAlex source ids).

    Falls back to the AMBIGUOUS candidates when resolve() declines. resolve() returns
    None for a name mapping to >1 identity, which is right for same_journal()/mismatch
    detection -- you must not assert two journals differ when unsure. But for LOOKUP it
    throws away usable information, because the ambiguity is usually one journal split
    across ISSN eras, not different journals. Measured: "Physical Review D" maps to 4
    identities (S133490392, 2470-0010, 1550-2368, 0556-2821) whose aliases are all
    Physical Review D under its 1970s, 1990s and current subtitles; "Physical Review E"
    maps to 3 the same way. Returning [] dropped the venue constraint entirely, so the
    caller's query degenerated to volume+year (56k-89k candidates) and its
    single-hit-only rule then correctly refused to guess.

    Widening to the union is safe HERE because the caller (oa_by_metadata) also
    constrains volume, year +-1, and page-or-author, and still asserts a match only on
    numFound == 1. Callers wanting the strict single identity should use resolve().
    """
    con = _con()
    if con is None:
        return []
    ident = resolve(name)
    if ident:
        idents = [ident]
    else:
        k = _norm(name)
        if not k:
            return []
        idents = [r[0] for r in con.execute(
            "SELECT DISTINCT identity FROM alias WHERE name_norm=?", (k,)).fetchall()]
        if not idents:
            alt = _ABBREV_FALLBACK.get(k)
            if alt:
                ak = _norm(alt)
                ident2 = resolve(alt)
                idents = [ident2] if ident2 else [
                    r[0] for r in con.execute(
                        "SELECT DISTINCT identity FROM alias WHERE name_norm=?",
                        (ak,)).fetchall()]
            if not idents:
                return []
    out, seen = [], set()
    for i in idents:
        for r in con.execute(
                "SELECT DISTINCT venue_id FROM journal WHERE identity=?", (i,)).fetchall():
            if r[0] and r[0] not in seen:
                seen.add(r[0])
                out.append(r[0])
    return out


def same_journal(a: str, b: str) -> bool:
    """True if a and b refer to the same journal.

    If both names resolve to an identity, compares exactly. If either fails to
    resolve, falls back to a conservative heuristic (all significant words of the
    shorter name are prefixes of words in the longer name, or matching acronym).
    """
    if not a or not b:
        return True  # missing data — don't reject
    ra, rb = resolve(a), resolve(b)
    if ra and rb:
        return ra == rb
    return _heuristic_match(a, b)


def _sig_words(s: str):
    # keep single-char series designators (A/B/C) — they distinguish journals
    return [w for w in _norm(s).split()
            if (len(w) >= 3 or (len(w) == 1 and w.isalpha())) and w not in _STOP]


def _series_letter(words):
    """A TRAILING single letter is a series designator ('J Phys A' -> 'a').
    Leading/medial single letters are ISO-4 word abbreviations (J=Journal,
    N=New) and are not series designators."""
    if words and len(words[-1]) == 1 and words[-1].isalpha():
        return words[-1]
    return None


def _heuristic_match(a: str, b: str) -> bool:
    """Conservative fallback when authority resolution fails.

    - Acronym match (jama <-> Journal of the American Medical Association), OR
    - every significant word of the shorter name prefix-matches a word in the
      longer name AND any single-letter series designators agree.
    A single-word short name must match the longer name's word set exactly
    (so 'Cell' does not match 'Cell Reports').
    """
    # Acronym check first (works even for very short forms)
    ca, cb = _norm(a).replace(" ", ""), _norm(b).replace(" ", "")
    if ca and (ca == _acronym(b)) or cb and (cb == _acronym(a)):
        return True

    aw, bw = _sig_words(a), _sig_words(b)
    if not aw or not bw:
        return True
    short, long = (aw, bw) if len(aw) <= len(bw) else (bw, aw)

    # Series designators (trailing A/B/C) must agree if BOTH sides carry one
    sa, sl = _series_letter(short), _series_letter(long)
    if sa and sl and sa != sl:
        return False

    multi = [w for w in short if len(w) >= 3]
    # A single multi-letter word alone is too generic — require exact word-set match
    if len(multi) <= 1:
        return set(w for w in short if len(w) >= 3) == set(w for w in long if len(w) >= 3)

    return all(any(_word_matches(sw, lw) for lw in long) for sw in multi)


def _is_subseq(s: str, t: str) -> bool:
    it = iter(t)
    return all(ch in it for ch in s)


def _word_matches(sw: str, lw: str) -> bool:
    """A citation word matches a full word if one is a prefix of the other, or —
    for abbreviations of 4+ chars — the shorter is a subsequence of the longer
    (Natl -> National), regardless of which argument is the abbreviation."""
    if lw.startswith(sw) or sw.startswith(lw):
        return True
    sh, lo = (sw, lw) if len(sw) <= len(lw) else (lw, sw)
    if len(sh) >= 4 and _is_subseq(sh, lo):
        return True
    return False


if __name__ == "__main__":
    tests = [
        ("Nat Med", "Nature Medicine"),
        ("JAMA", "Journal of the American Medical Association"),
        ("PNAS", "Proceedings of the National Academy of Sciences"),
        ("NEJM", "New England Journal of Medicine"),
        ("BMJ", "British Medical Journal"),
        ("J Clin Oncol", "Journal of Clinical Oncology"),
        ("Journal of Clinical Oncology", "Journal of Clinical Medicine"),
        ("Nature", "Nature Communications"),
        ("PLoS ONE", "PLOS ONE"),
    ]
    for a, b in tests:
        print(f"{same_journal(a,b)!s:>6}  resolve(a)={resolve(a)}  resolve(b)={resolve(b)}  | {a}  ~  {b}")
