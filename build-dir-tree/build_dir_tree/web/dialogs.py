"""Native file/folder pickers, run in a subprocess.

Tkinter owns its event loop and is not safe to drive from the HTTP server
threads, so each dialog runs in its own short-lived process and reports the
chosen path on stdout as JSON.

Usage (internal):
    python -m build_dir_tree.web.dialogs file  [--initial DIR]
    python -m build_dir_tree.web.dialogs dir   [--initial DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from typing import Any, Dict, List, Optional


def _pick(kind: str, initial: str) -> Dict[str, Any]:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update()

    try:
        if kind == "dir":
            picked = filedialog.askdirectory(
                parent=root,
                title="选择输出根目录",
                initialdir=initial or None,
                mustexist=True,
            )
        else:
            picked = filedialog.askopenfilename(
                parent=root,
                title="选择课程页快照（.mhtml / .html）",
                initialdir=initial or None,
                filetypes=[
                    ("Moodle 页面快照", "*.mhtml"),
                    ("HTML 文件", "*.html *.htm"),
                    ("全部文件", "*.*"),
                ],
            )
    finally:
        root.destroy()

    return {"ok": bool(picked), "path": picked or ""}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="build-dir-tree-dialog")
    parser.add_argument("kind", choices=("file", "dir"))
    parser.add_argument("--initial", default="")
    args = parser.parse_args(argv)

    try:
        result = _pick(args.kind, args.initial)
    except Exception as exc:  # no display, missing tkinter, etc.
        result = {
            "ok": False,
            "path": "",
            "error": "%s: %s" % (type(exc).__name__, exc),
            "detail": traceback.format_exc(limit=3),
        }

    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
