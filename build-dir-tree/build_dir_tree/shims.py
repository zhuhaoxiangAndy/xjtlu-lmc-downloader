"""Replace the pip-generated .exe launchers with script shims.

Why: on machines running Windows Defender Application Control (WDAC), the
console-script .exe stubs that pip writes into the Scripts folder are not on the allowed
list and get blocked outright. A .cmd file is not affected, because the policy
governs PE executables, not batch files.

Running this module after installation installs two shims next to the exe:

    Scripts/build-dir-tree.cmd       - CLI
    Scripts/build-dir-tree-ui.cmd    - graphical interface

They simply forward to "python -m build_dir_tree[.ui]", so they always use the
same interpreter that has this package installed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional

__all__ = ["install_shims", "shim_dir", "main"]

SHIMS = {
    "build-dir-tree.cmd": "build_dir_tree",
    "build-dir-tree-ui.cmd": "build_dir_tree.ui",
}


def shim_dir() -> str:
    """The Scripts directory that belongs to the running interpreter.

    On Windows python.exe sits at the environment root and the launchers live in
    its Scripts subfolder, so prefer that when it exists. On POSIX the two are
    the same directory.
    """
    base = os.path.dirname(os.path.abspath(sys.executable))
    candidates = (
        os.path.join(base, "Scripts"),
        base,
    )
    for path in candidates:
        if os.path.isdir(path):
            return path
    return base


def _shim_text(module: str) -> str:
    return (
        "@echo off\r\n"
        "rem Launcher shim written by build-dir-tree.\r\n"
        "rem A .cmd file is used because WDAC policies block pip's .exe stubs.\r\n"
        "\"%s\" -m %s %%*\r\n" % (sys.executable, module)
    )


def install_shims(directory: Optional[str] = None, force: bool = False) -> List[str]:
    """Write the .cmd shims; returns the paths written."""
    target = directory or shim_dir()
    if not os.path.isdir(target):
        raise SystemExit("error: not a directory: %s" % target)

    written: List[str] = []
    for name, module in SHIMS.items():
        path = os.path.join(target, name)
        if os.path.exists(path) and not force:
            continue
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(_shim_text(module))
        written.append(path)
    return written


def _probe(path: str) -> bool:
    """True when the launcher starts without being blocked by policy.

    The UI launcher has no --version flag and would open a server, so it is only
    started with --help, which exits immediately.
    """
    if not os.path.isfile(path):
        return False
    argument = "--help"
    try:
        result = subprocess.run(
            [os.environ.get("COMSPEC", "cmd.exe"), "/c", path, argument],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="build-dir-tree-shims",
        description="Install .cmd launcher shims that WDAC will not block.",
    )
    parser.add_argument("--dir", default=None, help="Target directory (default: the Python Scripts dir)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing shims")
    args = parser.parse_args(argv)

    written = install_shims(args.dir, args.force)
    target = args.dir or shim_dir()
    if written:
        for path in written:
            print("wrote %s" % path)
    else:
        print("shims already present in %s" % target)

    for name in SHIMS:
        path = os.path.join(target, name)
        print("  %-24s %s" % (name, "ok" if _probe(path) else "not runnable"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
