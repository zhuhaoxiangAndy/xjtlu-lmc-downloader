"""Reuse a browser login without ever storing credentials in the repository.

Three ways to supply cookies, in order of convenience:

1. ``--cookies cookies.txt``  Netscape cookie file exported from a browser
   extension ("Get cookies.txt") or written by ``curl --cookie-jar``.
2. ``--cookies "MoodleSession=abc; MOODLEID1_=xyz"``  a raw ``Cookie:`` header.
3. ``LMC_COOKIES`` environment variable holding either of the above (a path or
   a raw header), so the value never appears in shell history or process args.

The store keeps only ``name=value`` pairs for one origin. Moodle rotates
``MoodleSession`` on requests, so ``Set-Cookie`` responses are folded back into
the store; without that, a long crawl would silently fall back to guest access
halfway through.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional

ENV_VAR = "LMC_COOKIES"


class CookieError(RuntimeError):
    """Raised when cookies cannot be read or contain nothing usable."""


def parse_cookie_header(text: str) -> Dict[str, str]:
    """Parse ``a=b; c=d`` into a mapping. Ignores attributes like ``Path``."""
    cookies = {}
    if not text:
        return cookies
    for chunk in text.replace("\n", ";").split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        name, _, value = chunk.partition("=")
        name = name.strip()
        if not name or name.lower() in ("path", "domain", "expires", "secure",
                                        "httponly", "samesite", "max-age"):
            continue
        cookies[name] = value.strip()
    return cookies


def parse_netscape(text: str) -> Dict[str, str]:
    """Parse a Netscape ``cookies.txt`` file body."""
    cookies = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            # "#HttpOnly_" prefixed lines are real cookies, not comments.
            if stripped.startswith("#HttpOnly_"):
                stripped = stripped[len("#HttpOnly_"):]
            else:
                continue
        fields = stripped.split("\t")
        if len(fields) < 7:
            continue
        name, value = fields[5], fields[6]
        if name:
            cookies[name] = value
    return cookies


def load_cookies(spec: Optional[str] = None) -> Dict[str, str]:
    """Resolve a cookie spec into a mapping, or raise :class:`CookieError`."""
    raw = spec if spec is not None else os.environ.get(ENV_VAR, "")
    raw = (raw or "").strip()
    if not raw:
        raise CookieError(
            "no cookies supplied; pass --cookies FILE|HEADER or set %s" % ENV_VAR
        )

    path = Path(raw)
    if "\n" not in raw and len(raw) < 4096:
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
                cookies = (parse_netscape(text) if "\t" in text
                           else parse_cookie_header(text))
                if cookies:
                    return cookies
                raise CookieError("cookie file contained no usable entries: %s" % path)
        except OSError as exc:
            raise CookieError("cannot read cookie file %s: %s" % (path, exc))

    cookies = parse_cookie_header(raw)
    if not cookies:
        raise CookieError("cookie value contained no name=value pairs")
    return cookies


def looks_like_netscape(text: str) -> bool:
    for line in text.splitlines():
        if line.strip().startswith("#") or not line.strip():
            continue
        return len(line.split("\t")) >= 7
    return False


class CookieStore:
    """A tiny single-origin cookie jar that absorbs ``Set-Cookie`` updates."""

    def __init__(self, cookies: Optional[Mapping[str, str]] = None) -> None:
        self._cookies: Dict[str, str] = dict(cookies or {})

    def __bool__(self) -> bool:
        return bool(self._cookies)

    def __len__(self) -> int:
        return len(self._cookies)

    def as_dict(self) -> Dict[str, str]:
        return dict(self._cookies)

    def header(self) -> str:
        return "; ".join("%s=%s" % (k, v) for k, v in self._cookies.items())

    def names(self) -> Iterable[str]:
        return tuple(self._cookies)

    def set(self, name: str, value: str) -> None:
        if name:
            self._cookies[name] = value

    def absorb_set_cookie(self, headers: Iterable[str]) -> None:
        """Fold ``Set-Cookie`` header values back into the store."""
        for header in headers:
            if not header:
                continue
            first = header.split(";", 1)[0].strip()
            if "=" not in first:
                continue
            name, _, value = first.partition("=")
            name = name.strip()
            if not name:
                continue
            lowered = value.strip().lower()
            if lowered in ("deleted", "expired") or not value.strip():
                # Moodle clears MoodleSession on logout; drop it rather than
                # sending a stale value on the next request.
                self._cookies.pop(name, None)
                continue
            self._cookies[name] = value.strip()

    def redacted(self) -> str:
        """A safe-to-log summary: names only, never values."""
        return ", ".join(sorted(self._cookies)) or "(none)"


_SESSION_COOKIE = re.compile(r"MoodleSession", re.I)


def has_session_cookie(cookies: Mapping[str, str]) -> bool:
    """Best-effort check that a Moodle session cookie is present."""
    return any(_SESSION_COOKIE.search(name) for name in cookies)
