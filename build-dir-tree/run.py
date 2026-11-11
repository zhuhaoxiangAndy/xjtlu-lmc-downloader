#!/usr/bin/env python3
"""Run build-dir-tree without installing it.

Usage:
    python run.py course.mhtml --summary
    python run.py --ui

This adds the script directory to sys.path and dispatches, so `python -m
build_dir_tree` style arguments work verbatim.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0] == "--ui":
        from build_dir_tree.web.server import main as ui_main

        return ui_main(argv[1:])
    from build_dir_tree.cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
