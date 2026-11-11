"""Native file/folder pickers, run in a subprocess.

Tkinter owns its event loop and is not safe to drive from the HTTP server
threads, so each dialog runs in its own short-lived process and reports the
chosen path on stdout as JSON.

DPI: Windows scales a DPI-unaware process, so the dialog renders blurry and
its layout shifts when the shell redraws (for example after "New folder").
Declaring per-monitor DPI awareness *before* Tk starts makes the dialog crisp
and keeps its geometry stable.

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


def _enable_dpi_awareness() -> None:
    """Opt this process into per-monitor DPI awareness before Tk initialises.

    Must run first: once Tk has created a window, Windows has already decided
    the scaling mode and the setting no longer takes effect. Every failure is
    ignored -- a blurry dialog is better than no dialog.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        # PROCESS_PER_MONITOR_DPI_AWARE = 2 (Windows 8.1+)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _pick(kind: str, initial: str) -> Dict[str, Any]:
    _enable_dpi_awareness()

    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    root.update_idletasks()

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
                    ("HTML 文件", "*.html"),
                    ("HTML 文件", "*.htm"),
                    ("全部文件", "*.*"),
                ],
            )
    finally:
        try:
            root.destroy()
        except Exception:
            pass

    return {"ok": bool(picked), "path": picked or ""}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="build-dir-tree-dialog")
    parser.add_argument("kind", choices=("file", "dir"))
    parser.add_argument("--initial", default="")
    args = parser.parse_args(argv)

    try:
        result = _pick(args.kind, args.initial)
    except Exception as exc:
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
