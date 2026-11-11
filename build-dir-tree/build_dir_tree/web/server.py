"""Local web UI for build-dir-tree (Python standard library only).

Why a server at all: the page has to read a local .mhtml file and write to a
local directory, which a file:// page cannot do. Everything stays on
127.0.0.1 and nothing is uploaded anywhere.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Set, Tuple

from ..course_parser import NamePolicy, ParseError, apply_name_policy, parse_bytes
from ..config import load_settings, save_settings, config_path
from ..locations import documents_dir, suggest_output_dir

__all__ = ["serve"]

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _policy_from(payload: Dict[str, Any]) -> NamePolicy:
    """Build a NamePolicy from the UI payload, clamping anything unexpected."""
    space_replace = payload.get("spaceReplace")
    if space_replace in (None, ""):
        space_replace = None
    return NamePolicy(
        illegal_replace=str(payload.get("illegalReplace", "")),
        space_replace=space_replace,
        collapse_spaces=bool(payload.get("collapseSpaces")),
        drop_trailing=bool(payload.get("dropTrailing", True)),
        number=bool(payload.get("number")),
    )


def _policy_dict(policy: NamePolicy) -> Dict[str, Any]:
    return {
        "illegal_replace": policy.illegal_replace,
        "space_replace": policy.space_replace,
        "collapse_spaces": policy.collapse_spaces,
        "drop_trailing": policy.drop_trailing,
        "number": policy.number,
    }


def _iter_nodes(nodes: List[Dict[str, Any]]):
    """Yield every node in the tree, parents before children."""
    for node in nodes:
        yield node
        yield from _iter_nodes(node.get("children") or [])


def _flatten(nodes: List[Dict[str, Any]]) -> List[str]:
    """All directory paths in a tree, parents before children."""
    out: List[str] = []
    for node in nodes:
        out.append(node["path"])
        out.extend(_flatten(node.get("children") or []))
    return out


def _selected_paths(
    nodes: List[Dict[str, Any]],
    excluded: Set[str],
    selected: Optional[Set[str]],
) -> List[str]:
    """Resolve which directory paths to create from the tree and the selection.

    Rules:
      * A directory is created when the user selected its node.
      * A directory is *also* created when a selected descendant needs it, so
        picking a leaf never leaves an orphan path.
      * Selecting a section does NOT implicitly select its activities; the
        client expands the tick itself when the user clicks a section.
      * ``excluded`` prunes a node and everything beneath it.
      * ``selected is None`` means "everything is selected".

    Paths are returned in tree order (parents before children).
    """
    keep: Dict[str, bool] = {}

    def visit(node: Dict[str, Any], parent_excluded: bool) -> None:
        nid = node["id"]
        excluded_here = parent_excluded or nid in excluded
        chosen = False if excluded_here else (
            True if selected is None else nid in selected
        )
        keep[nid] = chosen
        for child in node.get("children") or []:
            visit(child, excluded_here)
        if not chosen and not excluded_here:
            if any(keep[c["id"]] for c in (node.get("children") or [])):
                keep[nid] = True

    for node in nodes:
        visit(node, False)

    out: List[str] = []

    def emit(items: List[Dict[str, Any]]) -> None:
        for node in items:
            if keep.get(node["id"]):
                out.append(node["path"])
            emit(node.get("children") or [])

    emit(nodes)
    return out


def _course_dir_name(course, policy: NamePolicy) -> str:
    title = course.title
    for suffix in (" | LM Core", " | Learning Mall Core"):
        if title.endswith(suffix):
            title = title[: -len(suffix)]
    if "Course:" in title:
        title = title.split("Course:", 1)[1]
    return apply_name_policy(title.strip(), policy) or "course-tree"


def _build(payload: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = str(payload.get("snapshot", "")).strip()
    if not snapshot:
        return {"ok": False, "error": "没有填写快照路径"}
    if not os.path.isfile(snapshot):
        return {"ok": False, "error": "找不到文件: %s" % snapshot}

    with open(snapshot, "rb") as fh:
        raw = fh.read()
    try:
        course = parse_bytes(raw)
    except ParseError as exc:
        return {"ok": False, "error": str(exc)}

    saved = load_settings()
    merged = dict(saved)
    merged.update({k: v for k, v in payload.items() if v is not None})

    policy = _policy_from(merged)
    tree = course.to_tree(policy=policy)
    all_paths = _flatten(tree)

    raw_selected = payload.get("selected")
    excluded = set(payload.get("excluded") or [])
    if raw_selected is None:
        # No explicit choice yet: honour the saved default selection.
        mode = str(merged.get("defaultSelection") or "sections")
        if mode == "none":
            selected = set()
        elif mode == "sections":
            selected = {
                node["id"] for node in _iter_nodes(tree) if node["kind"] == "section"
            }
        else:
            selected = None
    else:
        selected = set(raw_selected)
    dirs = _selected_paths(tree, excluded, selected)

    course_dir = _course_dir_name(course, policy)
    create_root = bool(merged.get("createRoot", False))
    given_root = str(merged.get("rootName") or "").strip()
    root_name = (given_root or course_dir) if create_root else ""

    created = 0
    create_error: Optional[str] = None
    output = str(payload.get("output") or merged.get("outputDir") or "").strip()
    if not output:
        output = suggest_output_dir(course_dir)
    if payload.get("create") and output:
        root = os.path.join(output, root_name) if root_name else output
        try:
            for name in dirs:
                path = os.path.join(root, *name.split("/"))
                if not os.path.isdir(path):
                    os.makedirs(path, exist_ok=True)
                    created += 1
        except OSError as exc:
            create_error = str(exc)

    return {
        "ok": True,
        "course": course.summary(),
        "rootName": root_name,
        "courseDir": course_dir,
        "createRoot": create_root,
        "outputBase": output,
        "count": len(dirs),
        "totalCount": len(all_paths),
        "dirs": dirs,
        "tree": tree,
        "created": created,
        "createError": create_error,
        "policy": _policy_dict(policy),
        "output": output,
        "settings": saved,
        "configPath": config_path(),
    }


def _default_snapshot_dir() -> str:
    """Best guess at the folder the user keeps course snapshots in.

    Prefers the current working directory, then the user profile downloads
    folder, then the home directory.
    """
    for candidate in (
        os.getcwd(),
        os.path.join(os.path.expanduser("~"), "Downloads"),
        os.path.expanduser("~"),
    ):
        if candidate and os.path.isdir(candidate):
            return candidate
    return ""


def _scan_snapshots(directory: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Find .mhtml/.html snapshots in a folder (non-recursive), newest first."""
    if not directory or not os.path.isdir(directory):
        return []
    found: List[Dict[str, Any]] = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                if not entry.name.lower().endswith((".mhtml", ".mht", ".html", ".htm")):
                    continue
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                found.append(
                    {
                        "path": entry.path,
                        "name": entry.name,
                        "size": stat.st_size,
                        "modified": int(stat.st_mtime),
                    }
                )
    except OSError:
        return []
    found.sort(key=lambda item: item["modified"], reverse=True)
    return found[:limit]


def _open_dialog(kind: str, initial: str) -> Dict[str, Any]:
    """Run the tkinter picker in a child process and read its choice back."""
    cmd = [sys.executable, "-m", "build_dir_tree.web.dialogs", kind]
    if initial:
        cmd += ["--initial", initial]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "path": "", "error": "对话框超时未返回"}
    except OSError as exc:
        return {"ok": False, "path": "", "error": "无法启动对话框: %s" % exc}

    out = (proc.stdout or "").strip()
    if not out:
        err = (proc.stderr or "").strip().splitlines()
        detail = err[-1] if err else "对话框没有返回结果"
        return {"ok": False, "path": "", "error": detail}
    try:
        return json.loads(out)
    except ValueError:
        return {"ok": False, "path": "", "error": "对话框返回了无法解析的内容"}

class Handler(BaseHTTPRequestHandler):
    server_version = "build-dir-tree"

    def log_message(self, fmt: str, *args: Any) -> None:
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
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

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
        route = self.path.split("?", 1)[0]
        if route not in ("/api/build", "/api/dialog", "/api/scan", "/api/settings"):
            self._json(404, {"ok": False, "error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2_000_000:
            self._json(413, {"ok": False, "error": "payload too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            self._json(400, {"ok": False, "error": "bad json: %s" % exc})
            return
        try:
            if route == "/api/settings":
                if payload.get("save"):
                    result = {"ok": True, "settings": save_settings(payload.get("settings") or {}),
                              "configPath": config_path()}
                else:
                    result = {"ok": True, "settings": load_settings(), "configPath": config_path(),
                              "outputDefault": suggest_output_dir()}
            elif route == "/api/dialog":
                kind = "dir" if payload.get("kind") == "dir" else "file"
                initial = str(payload.get("initial") or "") or _default_snapshot_dir()
                result = _open_dialog(kind, initial)
            elif route == "/api/scan":
                directory = (
                    str(payload.get("directory") or "")
                    or str(load_settings().get("scanDir") or "")
                    or _default_snapshot_dir()
                )
                result = {
                    "ok": True,
                    "directory": directory,
                    "files": _scan_snapshots(directory),
                    "documents": documents_dir(),
                    "suggestedOutput": suggest_output_dir(),
                    "settings": load_settings(),
                    "configPath": config_path(),
                }
            else:
                result = _build(payload)
        except Exception as exc:
            self._json(500, {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})
            return
        ok = bool(result.get("ok"))
        if route == "/api/scan":
            ok = True
        self._json(200 if ok else 400, result)


def serve(
    host: str = "127.0.0.1", port: int = 0, open_browser: bool = True
) -> Tuple[str, ThreadingHTTPServer]:
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
    print("按 Ctrl+C 停止。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
