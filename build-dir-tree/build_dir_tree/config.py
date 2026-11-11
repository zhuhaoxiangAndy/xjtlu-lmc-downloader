"""User settings: persisted defaults that survive between runs.

Stored as JSON next to the user profile, so the CLI and the graphical
interface share one set of defaults. Anything unset falls back to the
built-in value, which keeps old config files forward compatible.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

__all__ = ["config_path", "load_settings", "save_settings", "DEFAULTS"]

DEFAULTS: Dict[str, Any] = {
    # Where the tree is written. Empty means "decide at run time".
    "outputDir": "",
    # Wrap the tree in a folder named after the course.
    "createRoot": False,
    # Name for that wrapper folder. Empty means "use the course title".
    "rootName": "",
    # Which directories to tick initially: sections, all, none.
    "defaultSelection": "sections",
    # Naming options.
    "number": False,
    "collapseSpaces": False,
    "dropTrailing": True,
    "spaceReplace": "",
    "illegalReplace": "",
    # Folder scanned for snapshots on start. Empty means "current folder".
    "scanDir": "",
}


def config_path() -> str:
    """Location of the settings file.

    Honours BUILD_DIR_TREE_CONFIG so tests and portable setups can point
    somewhere else. Default is %APPDATA%/build-dir-tree (or ~/.config on
    systems without APPDATA).
    """
    override = os.environ.get("BUILD_DIR_TREE_CONFIG")
    if override:
        return override

    appdata = os.environ.get("APPDATA")
    if appdata:
        base = os.path.join(appdata, "build-dir-tree")
    else:
        base = os.path.join(os.path.expanduser("~"), ".config", "build-dir-tree")
    return os.path.join(base, "settings.json")


def load_settings() -> Dict[str, Any]:
    """Read settings, merged over the defaults. Never raises."""
    settings = dict(DEFAULTS)
    path = config_path()
    try:
        with open(path, encoding="utf-8") as handle:
            stored = json.load(handle)
    except (OSError, ValueError):
        return settings

    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in DEFAULTS:
                settings[key] = value
    return settings


def save_settings(changes: Dict[str, Any]) -> Dict[str, Any]:
    """Merge `changes` into the stored settings and write them back."""
    settings = load_settings()
    for key, value in (changes or {}).items():
        if key in DEFAULTS:
            settings[key] = value

    path = config_path()
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, ensure_ascii=False, indent=2)
    return settings
