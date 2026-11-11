"""Minimal local web UI for build-dir-tree (stdlib only).

Why a server at all: the page has to read a local .mhtml file and write to a
local directory, which a file:// page cannot do. Everything stays on
127.0.0.1 and nothing is uploaded anywhere.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

from ..course_parser import NamePolicy, ParseError, apply_name_policy, parse_bytes

__all__ = ["serve"]

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _policy_from(payload: Dict[str, Any]) -> NamePolicy:
    """Build a NamePolicy from the UI payload, clamping anything unexpected."""
    space_replace = payload.get("spaceReplace")
    if space_replace in (None, ""):
        space_replace = None
    illegal = payload.get("illegalReplace", "")
    return NamePolicy(
        illegal_replace=str(illegal),
        space_replace=space_replace if space_replace is None else str(space_replace),
        collapse_spaces=bool(payload.get("collapseSpaces")),
        drop_trailing=bool(payload.get("dropTrailing", True)),
        number=bool(payload.get("number")),
    )


def _build(payload: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = str(payload.get("snapshot", "")).strip()
    if not snapshot:
        return {"ok": False, "error": "no snapshot path given"}
    if not os.path.isfile(snapshot):
        return {"ok": False, "error": "file not found: %s" % snapshot}

    layout = payload.get("layout", "sections")
    if layout not in ("sections", "full"):
        layout = "sections"

    with open(snapshot, "rb") as fh:
        raw = fh.read()
    try:
        course = parse_bytes(raw)
    except ParseError as exc:
        return {"ok": False, "error": str(exc)}

    policy = _policy_from(payload)
    spec = course.to_spec(
        layout=layout,
        include_empty_subsections=not bool(payload.get("skipEmptySubsections")),
        policy=policy,
    )

    root_name = str(payload.get("rootName") or "").strip()
    if not root_name:
        title = course.title
        for suffix in (" | LM Core", " | Learning Mall Core"):
            if title.endswith(suffix):
                title = title[: -len(suffix)]
        if "Course:" in title:
            title = title.split("Course:", 1)[1]
        root_name = apply_name_policy(title.strip(), policy) or "course-tree"

    created = 0
    created_error: Optional[str] = None
    output = str(payload.get("output") or "").strip()
    if payload.get("create") and output:
        root = os.path.join(output, root_name)
        try:
            for name in spec["dirs"]:
                path = os.path.join(root, *name.split("/"))
                if not os.path.isdir(path):
                    os.makedirs(path, exist_ok=True)
                    created += 1
        except OSError as exc:
            created_error = str(exc)

    return {
        "ok": True,
        "course": course.summary(),
        "layout": layout,
        "rootName": root_name,
        "count": spec["count"],
        "dirs": spec["dirs"],
        "created": created,
        "createError": created_error,
        "policy": spec["policy"],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "build-dir-tree"

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter console
        if os.environ.get("BUILD_DIR_TREE_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Dict[str, Any]) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            target = os.path.join(STATIC, "index.html")
        else:
            target = os.path.join(STATIC, path.lstrip("/"))
            if not os.path.abspath(target).startswith(os.path.abspath(STATIC)):
                self._json(403, {"ok": False, "error": "forbidden"})
                return
        if not os.path.isfile(target):
            self._json(404, {"ok": False, "error": "not found"})
            return
        ctype = "text/html; charset=utf-8"
        if target.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif target.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        with open(target, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/build":
            self._json(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            self._json(413, {"ok": False, "error": "payload too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            self._json(400, {"ok": False, "error": "bad json: %s" % exc})
            return
        try:
            result = _build(payload)
        except Exception as exc:  # keep the UI alive on unexpected failures
            self._json(500, {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})
            return
        self._json(200 if result.get("ok") else 400, result)


def serve(host: str = "127.0.0.1", port: int = 0, open_browser: bool = True) -> Tuple[str, ThreadingHTTPServer]:
    """Start the UI; returns (url, server). Pass port=0 for an ephemeral port."""
    httpd = ThreadingHTTPServer((host, port), Handler)
    actual = httpd.server_address[1]
    url = "http://%s:%d/" % (host, actual)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    return url, httpd


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="build-dir-tree-ui", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    url, httpd = serve(args.host, args.port, not args.no_browser)
    print("build-dir-tree UI: %s" % url)
    print("Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
