"""Export the live LMC cookies from the Playwright profile for CLI use.

Why this exists: the browser used for manual login keeps its cookies inside a
SQLite file that Chromium encrypts. Rather than decrypt that (which needs OS
key material), the cookies are read through the browser itself over CDP and
written as a Netscape ``cookies.txt``. The repository never stores the file;
it is written outside the checkout on purpose.

Usage:
    python scripts/export_cookies.py --port 9333 --out D:\\Downloads\\.lmc-cookies.txt
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--out", default=r"D:\Downloads\.lmc-cookies.txt")
    ap.add_argument("--domain", default="xjtlu.edu.cn")
    args = ap.parse_args()

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
        cookies = []
        for ctx in browser.contexts:
            cookies.extend(ctx.cookies())
        keep = [c for c in cookies if args.domain in (c.get("domain") or "")]

    if not keep:
        print("no cookies for %s — is the login still valid?" % args.domain,
              file=sys.stderr)
        return 1

    lines = [
        "# Netscape HTTP Cookie File",
        "# exported by lmc-downloader scripts/export_cookies.py",
        "",
    ]
    for cookie in keep:
        domain = cookie.get("domain") or ""
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        path = cookie.get("path") or "/"
        secure = "TRUE" if cookie.get("secure") else "FALSE"
        expires = int(cookie.get("expires") or 0)
        if expires <= 0:
            expires = 0
        lines.append("\t".join([
            domain, include_sub, path, secure, str(expires),
            cookie.get("name") or "", cookie.get("value") or "",
        ]))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    names = sorted({c.get("name") or "" for c in keep})
    print("wrote %s (%d cookies)" % (out, len(keep)))
    print("names: %s" % ", ".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
