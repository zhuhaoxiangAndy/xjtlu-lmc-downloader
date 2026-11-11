"""Launcher for the local web UI: python -m build_dir_tree.ui"""

import sys

from .web.server import main

if __name__ == "__main__":
    sys.exit(main())
