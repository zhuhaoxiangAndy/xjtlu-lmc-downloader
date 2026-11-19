"""Minimal HTTP client on the standard library.

Why not ``requests``: this package stays installable on a locked-down machine
with no third-party wheels, matching ``build-dir-tree``. Everything needed here
— cookies, redirects, retries, streaming to a file — is available in
``urllib.request``.

Behaviour worth knowing
-----------------------
* Redirects are followed and the FINAL url is reported, because an LMC
  ``mod/resource/view.php?id=N`` answers with a 302 to ``pluginfile.php/...``.
  That final URL is what the manifest records as the file's true origin.
* Redirects to ``login/index.php`` mean the session expired. That is surfaced as
  :class:`SessionExpired` instead of being written to disk as if it were a file.
* ``Set-Cookie`` responses are written back into the :class:`CookieStore`, so a
  rotated ``MoodleSession`` keeps the crawl authenticated.
* Failures are retried on 429/5xx and on transient socket errors, with backoff.
"""

from __future__ import annotations

import http.client
import logging
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from .session import CookieStore

LOGGER = logging.getLogger("lmc_downloader.http")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/159.0.0.0 Safari/537.36"
)

RETRY_STATUS = frozenset([408, 425, 429, 500, 502, 503, 504])
LOGIN_PATTERN = re.compile(r"/login/(index\.php)?(\?|$)", re.I)


class HttpError(RuntimeError):
    """Base class for transport-level failures."""


class SessionExpired(HttpError):
    """The server redirected to the login page: cookies are no longer valid."""


@dataclass
class Response:
    url: str
    status: int
    headers: Dict[str, str] = field(default_factory=dict)
    content: bytes = b""

    @property
    def content_type(self) -> str:
        return (self.headers.get("content-type") or "").split(";")[0].strip().lower()

    @property
    def content_disposition(self) -> str:
        return self.headers.get("content-disposition") or ""

    @property
    def is_html(self) -> bool:
        return self.content_type in ("text/html", "application/xhtml+xml")

    def text(self, encoding: str = "utf-8") -> str:
        return self.content.decode(encoding, "replace")


LOGIN_BODY_MARKERS = (
    b'name="logintoken"',
    b'id="login"',
    b'action="/login/index.php"',
    b"action=\"https://core.xjtlu.edu.cn/login/index.php\"",
    b"Log in to the site",
)


def looks_like_login_page(response: "Response") -> bool:
    """True when an HTML body is actually the Moodle login form.

    A 302 to /login/ is caught by LOGIN_PATTERN, but Moodle sometimes returns the
    login form with HTTP 200 on the original URL (for example when the session
    expired between two requests).Without this check that page is saved as if it
    were the file the user asked for, which is how whole courses ended up as
    useless .html files.
    """
    if not response.is_html:
        return False
    body = response.content or b""
    return any(marker in body for marker in LOGIN_BODY_MARKERS)


def _origin(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return "%s://%s" % (parsed.scheme, parsed.netloc)


class HttpClient:
    """Cookie-carrying GET client with retries and streaming support."""

    def __init__(
        self,
        cookies: Optional[CookieStore] = None,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 60.0,
        retries: int = 3,
        backoff: float = 1.5,
        allowed_hosts: Optional[set] = None,
    ) -> None:
        self.cookies = cookies or CookieStore()
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = max(1, retries)
        self.backoff = backoff
        self.allowed_hosts = set(allowed_hosts) if allowed_hosts else set()
        self.request_count = 0
        # Redirect chain is checked manually so an off-site hop can be refused.
        self._opener = urllib.request.build_opener()

    # -- internal -----------------------------------------------------------
    def _build_request(self, url: str) -> urllib.request.Request:
        request = urllib.request.Request(url, method="GET")
        request.add_header("User-Agent", self.user_agent)
        request.add_header("Accept", "*/*")
        request.add_header("Accept-Language", "en-US,en;q=0.9")
        reference = next(iter(self.allowed_hosts), None)
        if reference:
            request.add_header("Referer", "https://%s/" % reference)
        if self.cookies:
            request.add_header("Cookie", self.cookies.header())
        return request

    def _check_host(self, url: str) -> None:
        host = urllib.parse.urlparse(url).netloc.lower()
        if not self.allowed_hosts:
            return
        if host not in self.allowed_hosts:
            raise HttpError(
                "refusing to leave the course host: %s (allowed: %s)"
                % (host, ", ".join(sorted(self.allowed_hosts)))
            )

    def _absorb(self, headers) -> None:
        values = headers.get_all("Set-Cookie") or []
        if values:
            self.cookies.absorb_set_cookie(values)

    # -- public -------------------------------------------------------------
    def get(self, url: str, allow_login_redirect: bool = False) -> Response:
        """GET a URL and return the final response body in memory."""
        last_error: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                self._check_host(url)
                request = self._build_request(url)
                self.request_count += 1
                with self._opener.open(request, timeout=self.timeout) as response:
                    final_url = response.geturl()
                    self._check_host(final_url)
                    self._absorb(response.headers)
                    if not allow_login_redirect and LOGIN_PATTERN.search(
                        urllib.parse.urlparse(final_url).path + "?"
                    ):
                        raise SessionExpired(
                            "session expired: redirected to %s" % final_url
                        )
                    headers = {k.lower(): v for k, v in response.headers.items()}
                    body = response.read()
                    return Response(final_url, response.status, headers, body)
            except SessionExpired:
                raise
            except urllib.error.HTTPError as exc:
                self._absorb(exc.headers) if exc.headers else None
                if exc.code in RETRY_STATUS and attempt < self.retries:
                    last_error = exc
                    time.sleep(self.backoff ** attempt)
                    continue
                raise HttpError("HTTP %d for %s" % (exc.code, url)) from exc
            except (urllib.error.URLError, socket.timeout, http.client.HTTPException,
                    ConnectionError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.backoff ** attempt)
                    continue
                raise HttpError("network failure for %s: %s" % (url, exc)) from exc
        raise HttpError("exhausted retries for %s: %s" % (url, last_error))

    def download(self, url: str, destination: Path) -> Response:
        """Stream a URL straight to ``destination`` and return the metadata.

        Streaming matters for lecture videos: buffering a 500 MB file in memory
        would be pointless. The file is written to a ``.part`` sibling first and
        renamed on success, so an interrupted run never leaves a half file that
        looks complete.
        """
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".part")
        last_error: Optional[Exception] = None

        for attempt in range(1, self.retries + 1):
            try:
                self._check_host(url)
                request = self._build_request(url)
                self.request_count += 1
                with self._opener.open(request, timeout=self.timeout) as response:
                    final_url = response.geturl()
                    self._check_host(final_url)
                    self._absorb(response.headers)
                    if LOGIN_PATTERN.search(urllib.parse.urlparse(final_url).path + "?"):
                        raise SessionExpired(
                            "session expired: redirected to %s" % final_url
                        )
                    headers = {k.lower(): v for k, v in response.headers.items()}
                    written = 0
                    with open(partial, "wb") as handle:
                        while True:
                            chunk = response.read(64 * 1024)
                            if not chunk:
                                break
                            handle.write(chunk)
                            written += len(chunk)
                    partial.replace(destination)
                    return Response(final_url, response.status, headers,
                                    b"" if written else b"")
            except SessionExpired:
                partial.unlink(missing_ok=True)
                raise
            except urllib.error.HTTPError as exc:
                partial.unlink(missing_ok=True)
                if exc.code in RETRY_STATUS and attempt < self.retries:
                    last_error = exc
                    time.sleep(self.backoff ** attempt)
                    continue
                raise HttpError("HTTP %d for %s" % (exc.code, url)) from exc
            except (urllib.error.URLError, socket.timeout, http.client.HTTPException,
                    ConnectionError) as exc:
                partial.unlink(missing_ok=True)
                last_error = exc
                if attempt < self.retries:
                    time.sleep(self.backoff ** attempt)
                    continue
                raise HttpError("network failure for %s: %s" % (url, exc)) from exc
        raise HttpError("exhausted retries for %s: %s" % (url, last_error))

    def probe(self, url: str) -> Response:
        """Fetch only what is needed to learn a file's real name and type.

        Moodle sends ``Content-Disposition`` on the final ``pluginfile.php``
        response, so the headers alone identify the file. Reading a small prefix
        then closing the connection avoids pulling a whole lecture video just to
        discover its name.
        """
        self._check_host(url)
        request = self._build_request(url)
        self.request_count += 1
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                final_url = response.geturl()
                self._check_host(final_url)
                self._absorb(response.headers)
                if LOGIN_PATTERN.search(urllib.parse.urlparse(final_url).path + "?"):
                    raise SessionExpired(
                        "session expired: redirected to %s" % final_url)
                headers = {k.lower(): v for k, v in response.headers.items()}
                # A whole page is needed when the target is an HTML viewer: the
                # real file link sits tens of kilobytes in (a Moodle resource
                # viewer puts it well past the 512-byte header prefix), so a
                # short sample made every such resource look like a web page.
                is_html = "text/html" in (headers.get("content-type") or "").lower()
                sample = response.read() if is_html else response.read(512)
                result = Response(final_url, response.status, headers, sample)
            if looks_like_login_page(result):
                raise SessionExpired(
                    "session expired: %s returned the login form" % url
                )
            return result
        except SessionExpired:
            raise
        except urllib.error.HTTPError as exc:
            # 404 is a normal answer here: a folder with no direct files, or a
            # resource the account cannot see. Report it, never crash the run.
            if exc.headers:
                self._absorb(exc.headers)
            raise HttpError("HTTP %d for %s" % (exc.code, url)) from exc
        except (urllib.error.URLError, socket.timeout, http.client.HTTPException,
                ConnectionError) as exc:
            raise HttpError("network failure for %s: %s" % (url, exc)) from exc

    def close(self) -> None:
        try:
            self._opener.close()
        except Exception:  # pragma: no cover - best effort
            pass

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
