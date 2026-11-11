"""Where downloaded trees should live by default.

Rules, in order:
  1. If the current working directory is somewhere the user actually works
     (not the home directory itself, not a system directory), use it.
  2. Otherwise fall back to <Documents>/LMC/<course name>.

"Documents" must be looked up through the Windows Known Folder API, not
assumed to be ~/Documents: this machine, for example, keeps it on D:.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

__all__ = ["documents_dir", "suggest_output_dir"]


def _system_dirs():
    return [
        os.environ.get("SystemRoot", "C:\\Windows"),
        os.environ.get("ProgramFiles", "C:\\Program Files"),
        os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),
        os.environ.get("ProgramData", "C:\\ProgramData"),
    ]


def _known_folder_documents() -> Optional[str]:
    """Ask Windows where Documents really is (handles moved/redirected folders)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        folder_id = uuid.UUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}")
        guid = GUID()
        guid.Data1 = folder_id.time_low
        guid.Data2 = folder_id.time_mid
        guid.Data3 = folder_id.time_hi_version
        for index, byte in enumerate(folder_id.bytes[8:]):
            guid.Data4[index] = byte

        path_ptr = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(path_ptr)
        )
        if result != 0 or not path_ptr.value:
            return None
        path = path_ptr.value
        try:
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
        except Exception:
            pass
        return path if os.path.isdir(path) else None
    except Exception:
        return None


def documents_dir() -> str:
    """Best available Documents directory, verified to exist.

    Order: Windows Known Folder -> ~/Documents -> ~/文档 -> home.
    """
    known = _known_folder_documents()
    if known:
        return known

    home = os.path.expanduser("~")
    for candidate in (
        os.path.join(home, "Documents"),
        os.path.join(home, "文档"),
        home,
    ):
        if os.path.isdir(candidate):
            return candidate
    return home


def _is_unhelpful_cwd(cwd: str) -> bool:
    """True when the working directory is not a sensible place to write into."""
    if not cwd:
        return True
    normalised = os.path.normcase(os.path.abspath(cwd))
    home = os.path.normcase(os.path.abspath(os.path.expanduser("~")))
    if normalised == home:
        return True
    drive, tail = os.path.splitdrive(normalised)
    if drive and tail.strip("/\\") == "":
        return True
    for system_dir in _system_dirs():
        if not system_dir:
            continue
        root = os.path.normcase(os.path.abspath(system_dir))
        if normalised == root or normalised.startswith(root + os.sep):
            return True
    return False


def suggest_output_dir(course_name: str = "") -> str:
    """Suggested output base directory (the folder the tree is created in).

    Uses the current working directory when it looks like a real project
    folder; otherwise <Documents>/LMC[/<course name>].
    """
    cwd = os.getcwd()
    if not _is_unhelpful_cwd(cwd):
        return cwd

    base = os.path.join(documents_dir(), "LMC")
    name = (course_name or "").strip()
    return os.path.join(base, name) if name else base
