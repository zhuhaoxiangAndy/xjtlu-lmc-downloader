#!/usr/bin/env python3
"""build-dir-tree CLI: turn an LMC/Moodle course snapshot into a directory tree.

Examples
--------
    # Inspect a snapshot without writing anything
    python -m build_dir_tree course.mhtml --summary

    # Print the directory list (no filesystem changes)
    python -m build_dir_tree course.mhtml --layout sections

    # Create the directories
    python -m build_dir_tree course.mhtml --layout sections --mkdir -o out/

    # Generate a standalone PowerShell script instead
    python -m build_dir_tree course.mhtml --emit-ps1 build-tree.ps1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

from .course_parser import Course, NamePolicy, ParseError, apply_name_policy, parse_bytes

__all__ = ["main", "build_parser"]


def _fidelity_warnings(spec: Dict[str, Any], root: str) -> List[str]:
    """Report titles that could not be reproduced exactly on this filesystem.

    Trailing spaces and dots are dropped by Windows unconditionally (the
    directory name would otherwise not match what is on disk). Report them so
    the user can see the deviation instead of discovering it later.
    """
    warnings: List[str] = []
    changed: List[str] = []
    for record in spec["records"]:
        for key in ("section_name", "activity_name"):
            original = record.get(key)
            if not original:
                continue
            if original != original.rstrip(" ."):
                changed.append(original)
                break
    if changed:
        warnings.append(
            "note: %d title(s) end with a space or dot; Windows drops those, "
            "so the folder name differs slightly:" % len(changed)
        )
        for text in changed[:5]:
            warnings.append("  " + repr(text))
    return warnings

def _path_length_warnings(spec: Dict[str, Any], root: str) -> List[str]:
    """Warn about paths that may exceed the Windows MAX_PATH limit (260).

    Directory names keep the original course titles verbatim, so deep trees can
    get long. On Windows the limit applies to the full absolute path; we only
    warn, never truncate, because truncating would break the structure fidelity
    that this tool exists for.
    """
    warnings: List[str] = []
    limit = 260
    root_len = len(os.path.abspath(root)) + 1

    over = 0
    longest = ""
    longest_len = 0
    for name in spec["dirs"]:
        candidate = name.replace("/", os.sep)
        total = root_len + len(candidate)
        if total > limit:
            over += 1
            if total > longest_len:
                longest, longest_len = candidate, total

    if over:
        warnings.append(
            "warning: %d of %d path(s) may exceed the Windows %d-character limit "
            "(longest: %d chars)" % (over, len(spec["dirs"]), limit, longest_len)
        )
        warnings.append("  " + longest)
        warnings.append(
            "  hint: use a shorter -o/--output, or enable long paths in Windows "
            "(see docs: LongPathsEnabled)"
        )
    return warnings

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="build-dir-tree",
        description="Rebuild a course directory tree from a saved LMC/Moodle page snapshot.",
    )
    p.add_argument("snapshot", nargs="?", help="Path to the .mhtml/.html course snapshot")
    p.add_argument("-o", "--output", help="Root directory for created folders (required with --mkdir)")
    p.add_argument(
        "--layout",
        choices=("sections", "full"),
        default="sections",
        help="sections = chapter skeleton (default); full = also one leaf per activity",
    )
    p.add_argument(
        "--root-name",
        default=None,
        help="Name of the top-level folder to create inside --output (default: derived from the course title)",
    )
    p.add_argument("--mkdir", action="store_true", help="Actually create the directories")
    p.add_argument("--dry-run", action="store_true", help="Print what would be created (default when --mkdir is absent)")
    p.add_argument("--summary", action="store_true", help="Print only course statistics and exit")
    p.add_argument("--json", dest="json_out", metavar="FILE", help="Write the full spec as JSON to FILE")
    p.add_argument("--emit-ps1", metavar="FILE", help="Write a standalone PowerShell script that creates the tree")
    p.add_argument("--separator", default="/", help="Path separator used in printed paths (default: /)")
    p.add_argument("--skip-empty-subsections", action="store_true",
                   help="Do not create folders for subsections that contain no activities")
    grp = p.add_argument_group("naming policy (defaults are faithful)")
    grp.add_argument(
        "--number",
        action="store_true",
        help="Prefix directories with their Moodle number (04-, 04.16-, 01 )",
    )
    grp.add_argument(
        "--collapse-spaces",
        action="store_true",
        help="Collapse runs of spaces into one (default: keep them verbatim)",
    )
    grp.add_argument(
        "--space-replace",
        metavar="TEXT",
        default=None,
        help="Replace every space with TEXT (default: keep spaces verbatim)",
    )
    grp.add_argument(
        "--illegal-replace",
        metavar="TEXT",
        default="",
        help="Replace each illegal character (< > : \" / \\ | ? *) with TEXT. "
             "Default: delete them.",
    )
    grp.add_argument(
        "--keep-trailing",
        action="store_true",
        help="Do not strip trailing dots/spaces (Windows will drop them anyway)",
    )
    p.add_argument("-q", "--quiet", action="store_true", help="Only emit errors")
    return p


def _load(path: str) -> Course:
    if not os.path.exists(path):
        raise SystemExit("error: snapshot not found: %s" % path)
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        return parse_bytes(raw)
    except ParseError as exc:
        raise SystemExit("error: could not parse snapshot: %s" % exc)


def _default_root_name(course: Course) -> str:
    title = course.title
    for suffix in (" | LM Core", " | Learning Mall Core"):
        if title.endswith(suffix):
            title = title[: -len(suffix)]
    if "Course:" in title:
        title = title.split("Course:", 1)[1]
    title = title.strip()
    return apply_name_policy(title) or "course-tree"


def _ps1_script(spec: Dict[str, Any], root: str, root_name: str) -> str:
    """Render a standalone PowerShell script that materialises the spec."""

    def q(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    course = spec["course"]
    names = spec["dirs"]

    out: List[str] = [
        "<#",
        "    Generated by build-dir-tree.",
        "    Course : %s" % course.get("title", ""),
        "    Source : %s" % course.get("source_url", ""),
        "    Layout : %s (%d directories)" % (spec["layout"], spec["count"]),
        "#>",
        "[CmdletBinding()]",
        "param(",
        "    [string]$Base = %s," % q(root),
        "    [switch]$DryRun",
        ")",
        "",
        "$ErrorActionPreference = 'Stop'",
        "$Root = Join-Path $Base %s" % q(root_name),
        "$Dirs = @(",
    ]
    for idx, name in enumerate(names):
        suffix = "," if idx < len(names) - 1 else ""
        out.append("    " + q(name.replace("/", "\\")) + suffix)
    out += [
        ")",
        "",
        "Write-Host ('Course : ' + %s)" % q(course.get("title", "")),
        "Write-Host ('Layout : %s  Dirs: ' -f 0 2>$null)" % spec["layout"],
    ]
    # replace the awkward placeholder line with a clean one
    out[-1] = "Write-Host ('Layout : {0}  Dirs: {1}' -f %s, $Dirs.Count)" % q(spec["layout"])
    out += [
        "Write-Host ('Root   : ' + $Root)",
        "",
        "if ($DryRun) {",
        "    $Dirs | ForEach-Object { Write-Output ('  + ' + $_) }",
        "    Write-Output '(dry-run, nothing created)'",
        "    return",
        "}",
        "",
        "$created = 0",
        "foreach ($rel in $Dirs) {",
        "    $path = Join-Path $Root $rel",
        "    if (-not (Test-Path -LiteralPath $path)) {",
        "        New-Item -ItemType Directory -Path $path -Force | Out-Null",
        "        $created++",
        "    }",
        "}",
        "Write-Host ('Done: {0} directories, {1} newly created' -f $Dirs.Count, $created)",
        "",
    ]
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.snapshot:
        build_parser().print_help()
        return 2

    course = _load(args.snapshot)

    if args.summary:
        info = course.summary()
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(course.as_dict(), fh, ensure_ascii=False, indent=2)
        if not args.quiet:
            print("Title      : %s" % info["title"])
            print("Source     : %s" % info["source_url"])
            print("Course id  : %s" % info["course_id"])
            print("Top-level  : %d sections" % info["top_sections"])
            print("Subsections: %d" % info["subsections"])
            print("Activities : %d" % info["activities"])
        return 0

    policy = NamePolicy(
        illegal_replace=args.illegal_replace,
        space_replace=args.space_replace,
        collapse_spaces=args.collapse_spaces,
        drop_trailing=not args.keep_trailing,
        number=args.number,
    )
    spec = course.to_spec(
        layout=args.layout,
        separator=args.separator,
        include_empty_subsections=not args.skip_empty_subsections,
        policy=policy,
    )

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(spec, fh, ensure_ascii=False, indent=2)

    root_name = args.root_name or _default_root_name(course)

    if args.emit_ps1:
        with open(args.emit_ps1, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write(_ps1_script(spec, args.output or ".", root_name))
        if not args.quiet:
            print("wrote PowerShell script: %s (%d directories)" % (args.emit_ps1, spec["count"]))
        return 0

    if not args.mkdir:
        if not args.quiet:
            print("Layout=%s  Dirs=%d  Root=%s" % (args.layout, spec["count"], root_name))
            for name in spec["dirs"]:
                print("  + " + name)
            print("(dry-run, nothing created; pass --mkdir -o <dir> to create)")
        for line in _fidelity_warnings(spec, root_name) + _path_length_warnings(
            spec, os.path.join(args.output or ".", root_name)
        ):
            print(line, file=sys.stderr)
        return 0

    if not args.output:
        raise SystemExit("error: --mkdir requires -o/--output")

    root = os.path.join(args.output, root_name)
    for line in _fidelity_warnings(spec, root) + _path_length_warnings(spec, root):
        if not args.quiet:
            print(line, file=sys.stderr)
    created = 0
    for name in spec["dirs"]:
        path = os.path.join(root, *name.split("/"))
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
            created += 1
    if not args.quiet:
        print("Created/verified %d directories under %s (new: %d)" % (spec["count"], root, created))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
