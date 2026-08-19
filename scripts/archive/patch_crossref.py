p = "/space/rwang/fake-citation-detector/scripts/crossref_lookup.py"
s = open(p).read()
sig = "    def _get(self, url: str, params: Optional[dict] = None) -> Optional[dict]:\n"
if "SKIP_CROSSREF_API" in s:
    print("already patched")
elif sig not in s:
    print("ERROR: signature not found")
else:
    guard = sig + ('        import os as _os\n'
                   '        if _os.environ.get("SKIP_CROSSREF_API") == "1":\n'
                   '            return None\n')
    open(p + ".bak_skipxrefapi", "w").write(s)          # backup
    open(p, "w").write(s.replace(sig, guard, 1))
    print("patched OK (backup .bak_skipxrefapi)")
