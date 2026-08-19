#!/usr/bin/env python3
r"""
apply_fixes_345.py — deterministic fixes for items 3-5, each verified to apply exactly
once or the script aborts without writing. All use \uXXXX escapes (no literal mojibake).

FIX 3  (text_repair.py): two encoding repairs.
  (a) digit-U+00B1-digit -> "-"  : a font-encoded en-dash in a page range or year span
      ("163\u00b1169", "1909\u00b11962"). NOT a real plus/minus -- those don't sit
      between two bare digits. Added inside repair_pdf_ligatures (targeted, safe).
  (b) fix_utf8_mojibake()          : UTF-8 bytes decoded as Latin-1 ("\u00c3\u00a9" for
      e-acute, "\u00e2\u0080\u0099" for a curly apostrophe). Table-free canonical repair:
      re-encode Latin-1, decode UTF-8, guarded by a signature + clean round-trip so it
      never touches well-formed text. Added as a separate retry variant so it never
      conflicts with the existing glyph-specific ligature fixes.

FIX 4  (batch_verify_years._demote_journal_in_title_slot): recover the journal when the
  mis-parsed "title" carries a trailing volume ("Proc. IEEE 91" -> "Proc. IEEE") or a
  trailing ". Journal" the parser glued on ("... claims data. Epidemiology"). Only a
  split the journal authority CONFIRMS is accepted, so a real title is never truncated.

FIX 5  (truncation): genuine line-break truncation cannot be repaired deterministically
  (the missing characters are gone) -- that is the corroborated-similarity / LLM-cleaning
  target, deferred by design. The word-DEMERGE half of item 5 already ships
  (text_repair.demerge_words); this script does not touch it.

HONESTY NOTE: the fake-injection gate builds refs from clean JSON and bypasses
parse_tei_refs, so FIX 4 is never exercised there and FIX 3's page-range repair helps
metadata (not title) matching -- the gate FPR is expected to hold at ~6.8%, unchanged.
These pay off on the real sweep. The gate run is a REGRESSION check (recall 114/114, FPR
not worse), not a win check.
"""
import re, shutil, sys, py_compile

TR = "/space/rwang/fake-citation-detector/scripts/text_repair.py"
BV = "/space/rwang/fake-citation-detector/scripts/batch_verify_years.py"

# ---------- FIX 3a: digit-plusminus-digit inside repair_pdf_ligatures ----------
tr = open(TR, encoding="utf-8").read()
a3a = ("    for bad, good in _MOJIBAKE_DASH.items():\n"
       "        if bad in s:\n"
       "            s = s.replace(bad, good)\n"
       "    return s\n")
r3a = ("    for bad, good in _MOJIBAKE_DASH.items():\n"
       "        if bad in s:\n"
       "            s = s.replace(bad, good)\n"
       "    # digit-U+00B1-digit: a font-encoded en-dash in a page range / year span\n"
       "    # (\"163\\u00b1169\", \"1909\\u00b11962\"), not a real plus/minus. Targeted & safe.\n"
       "    if \"\\u00b1\" in s:\n"
       "        s = re.sub(r\"(?<=\\d)\\u00b1(?=\\d)\", \"-\", s)\n"
       "    return s\n")
if tr.count(a3a) != 1:
    sys.exit("FIX3a ABORT: repair_pdf_ligatures tail anchor count = %d" % tr.count(a3a))

# ---------- FIX 3b: fix_utf8_mojibake() + wire into title_repair_variants ----------
a3b = ('def title_repair_variants(title):\n'
       '    """Yield distinct cleaned title variants')
r3b = (
    '_MOJI_UTF8_SIG = re.compile(\n'
    '    "\\u00c3[\\u0080-\\u00bf]|\\u00e2\\u0080[\\u0080-\\u00bf]|\\u00c2[\\u00a0-\\u00bf]")\n'
    '\n\n'
    'def fix_utf8_mojibake(s):\n'
    '    """Repair UTF-8 that was decoded as Latin-1 (\\u00c3\\u00a9 -> e-acute). Re-encode\n'
    '    Latin-1 then decode UTF-8; guarded by a mojibake signature and a clean round-trip\n'
    '    so well-formed text is never altered. Returns s unchanged when not applicable."""\n'
    '    if not s or not _MOJI_UTF8_SIG.search(s):\n'
    '        return s\n'
    '    try:\n'
    '        fixed = s.encode("latin-1").decode("utf-8")\n'
    '    except (UnicodeEncodeError, UnicodeDecodeError):\n'
    '        return s\n'
    '    return fixed if fixed != s else s\n'
    '\n\n'
    'def title_repair_variants(title):\n'
    '    """Yield distinct cleaned title variants')
if tr.count(a3b) != 1:
    sys.exit("FIX3b ABORT: title_repair_variants anchor count = %d" % tr.count(a3b))

# wire mojibake variant into the pipeline of title_repair_variants
a3c = ("    seen = {title}\n"
       "    lig = repair_pdf_ligatures(title)\n"
       "    au = strip_leading_author(lig)\n"
       "    gk = greek_words_to_symbols(au)\n"
       "    variants = [lig, au, gk]\n")
r3c = ("    seen = {title}\n"
       "    mj = fix_utf8_mojibake(title)\n"
       "    lig = repair_pdf_ligatures(mj)\n"
       "    au = strip_leading_author(lig)\n"
       "    gk = greek_words_to_symbols(au)\n"
       "    variants = [mj, lig, au, gk]\n")
if tr.count(a3c) != 1:
    sys.exit("FIX3c ABORT: title_repair_variants body anchor count = %d" % tr.count(a3c))

tr_new = tr.replace(a3a, r3a, 1).replace(a3b, r3b, 1).replace(a3c, r3c, 1)

# ---------- FIX 4: _demote_journal_in_title_slot case 2 ----------
bv = open(BV, encoding="utf-8").read()
a4 = ("    # case 2: no journal recorded, but the \"title\" resolves as a journal name\n"
      "    if not j:\n"
      "        try:\n"
      "            from journal_authority import resolve as _jr\n"
      "            if _jr(t):\n"
      "                obj.journal = t\n"
      "                obj.title = None\n"
      "        except Exception:\n"
      "            pass\n")
r4 = ("    # case 2: no journal recorded, but the \"title\" resolves as a journal name. Try\n"
      "    # the string as-is, then with a trailing volume number stripped (\"Proc. IEEE\n"
      "    # 91\" -> \"Proc. IEEE\"), then splitting a trailing \". Journal\" the parser glued\n"
      "    # on (\"...claims data. Epidemiology\"). Only a split the authority confirms is\n"
      "    # accepted, so a genuine article title is never truncated.\n"
      "    if not j:\n"
      "        try:\n"
      "            from journal_authority import resolve as _jr\n"
      "        except Exception:\n"
      "            _jr = None\n"
      "        if _jr:\n"
      "            probes = [t]\n"
      "            _novol = re.sub(r\"[\\s,]+\\d+\\s*$\", \"\", t).strip()\n"
      "            if _novol and _novol != t:\n"
      "                probes.append(_novol)\n"
      "            for _probe in probes:\n"
      "                try:\n"
      "                    if _jr(_probe):\n"
      "                        obj.journal = _probe\n"
      "                        obj.title = None\n"
      "                        return\n"
      "                except Exception:\n"
      "                    pass\n"
      "            m = re.match(r\"^(.*\\S)\\.\\s+([A-Z][A-Za-z.&'\\- ]{2,40})$\", t)\n"
      "            if m and len(m.group(1).split()) >= 3:\n"
      "                _head, _tail = m.group(1).strip(), m.group(2).strip()\n"
      "                try:\n"
      "                    if _jr(_tail):\n"
      "                        obj.journal = _tail\n"
      "                        obj.title = _head\n"
      "                        return\n"
      "                except Exception:\n"
      "                    pass\n")
if bv.count(a4) != 1:
    sys.exit("FIX4 ABORT: _demote case-2 anchor count = %d" % bv.count(a4))
bv_new = bv.replace(a4, r4, 1)

# ---------- write + compile ----------
shutil.copy(TR, TR + ".bak_moji345")
shutil.copy(BV, BV + ".bak_demote345")
open(TR, "w", encoding="utf-8").write(tr_new)
open(BV, "w", encoding="utf-8").write(bv_new)
py_compile.compile(TR, doraise=True)
py_compile.compile(BV, doraise=True)
print("FIX3 text_repair.py         : patched (backup .bak_moji345)")
print("FIX4 batch_verify_years.py  : patched (backup .bak_demote345)")
print("both compile OK")
