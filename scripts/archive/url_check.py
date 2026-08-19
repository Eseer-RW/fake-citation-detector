"""
url_check.py — verify that a cited URL points to a live web resource.

A citation carrying a URL that resolves (any HTTP status < 500) is a real web
source (website, report, dataset, standard), not a fabrication. Used to validate
the non-academic / URL-bearing slice of the 'not found' bucket.

Status interpretation (per project decision):
  200            page exists and is accessible
  301 / 302      URL redirects (followed)
  401 / 403      site exists, access restricted
  404            server exists, that page does not
  500+           server error -> treated as "exists but broken" => NOT counted live
"""
import re
import requests

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
_HEADERS = {"User-Agent": _UA, "Accept": "*/*"}

# Extract an http(s) URL from a raw reference string; strip trailing punctuation.
_URL_RE = re.compile(r'https?://[^\s<>"\')]+', re.I)


def extract_url(text: str):
    if not text:
        return None
    m = _URL_RE.search(text)
    if not m:
        return None
    url = m.group(0).rstrip('.,;:)]}>"\'')
    return url


def website_exists(url: str, timeout: int = 5) -> bool:
    """Return True if the URL resolves to a live server (HTTP status < 500)."""
    try:
        resp = requests.head(url, allow_redirects=True, timeout=timeout,
                             headers=_HEADERS)
        # Some servers do not support HEAD
        if resp.status_code in {405, 501}:
            resp = requests.get(url, allow_redirects=True, timeout=timeout,
                                headers=_HEADERS, stream=True)
        return resp.status_code < 500
    except requests.RequestException:
        return False


if __name__ == "__main__":
    for u in ["https://example.com",
              "https://not-a-real-domain-12345.com",
              "https://www.who.int/",
              "https://doi.org/10.1038/s41586-020-2649-2"]:
        print(f"{website_exists(u)!s:>6}  {u}")
