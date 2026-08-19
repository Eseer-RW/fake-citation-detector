p = "/space/rwang/fake-citation-detector/scripts/batch_verify_years.py"
s = open(p).read()

old = '''    ct = getattr(ref, "title", None)
    rt = _first(record.get("title"))
    if ct and rt:
        try:
            from title_normalize import normalize_title_key
            cw = set(normalize_title_key(ct).split()); rw = set(normalize_title_key(rt).split())
            if cw and rw and len(cw & rw) / len(cw | rw) < 0.3:
                issues.append(f"title: cited '{ct[:60]}' differs from actual '{rt[:60]}'")
        except Exception:
            pass'''

new = '''    ct = getattr(ref, "title", None)
    rt = _first(record.get("title"))
    if ct and rt:
        try:
            from title_normalize import normalize_title_key
            from text_repair import repair_pdf_ligatures, demerge_words
            ct_rep = demerge_words(repair_pdf_ligatures(ct))
            cw = set(normalize_title_key(ct_rep).split()); rw = set(normalize_title_key(rt).split())
            # Guard: skip when the cited "title" is not really a title -- a journal
            # abbreviation or a title-less citation (common in physics). Either would
            # falsely disagree with the matched record's real title. Flag only on a
            # substantive (>=4-token), non-journal cited title, repaired for
            # ligature/merge mojibake first (same repair the matcher uses).
            _journalish = False
            try:
                from journal_authority import resolve as _jr
                _journalish = bool(_jr(ct))
            except Exception:
                pass
            if len(cw) >= 4 and not _journalish and rw and len(cw & rw) / len(cw | rw) < 0.3:
                issues.append(f"title: cited '{ct[:60]}' differs from actual '{rt[:60]}'")
        except Exception:
            pass'''

if "ct_rep = demerge_words" in s:
    print("already patched")
elif old not in s:
    print("ERROR: target block not found (indentation drift?)")
else:
    open(p + ".bak_titlemiss", "w").write(s)
    open(p, "w").write(s.replace(old, new, 1))
    print("patched OK (backup .bak_titlemiss)")
