"""Command line interface for lmc-downloader.

Two subcommands, because the two halves are useful independently:

``outline``  print or save the course structure (no files downloaded)
``download`` resolve every activity and download the ones that carry files

Examples
--------
List what a course contains, without downloading anything::

    python -m lmc_downloader outline 1213 --cookies cookies.txt

Download everything into a course folder, skipping unchanged files::

    python -m lmc_downloader download 1213 --cookies cookies.txt ^
        --root "D:\\LMC\\ISA104"

Only the Office documents::

    python -m lmc_downloader download 1213 --cookies cookies.txt ^
        --root "D:\\LMC\\ISA104" --ext pdf,docx,pptx,xlsx

Exit codes: 0 all good, 1 nothing could be done / bad usage,
2 finished but some items failed or the session expired.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from urllib.parse import urlparse
from typing import List, Optional

from . import __version__
from .crawler import Crawler, CourseOutline, course_url, parse_outline
from .fetcher import Fetcher, FetchResult
from .http import HttpClient, HttpError, SessionExpired
from .names import human_bytes
from .session import CookieError, CookieStore, has_session_cookie, load_cookies

DEFAULT_HOST = "core.xjtlu.edu.cn"


#: 常见 MIME -> 扩展名，用于在 URL 与标题都没有后缀时判断类型。
EXT_FOR_MIME = {
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/quicktime": "mov",
    "audio/mpeg": "mp3",
    "application/pdf": "pdf",
    "application/zip": "zip",
    "text/html": "html",
}

#: --ext office 的预设集合。含 mhtml：Moodle 的 page 模块与未解析出真实
#: 文件的 resource 都以网页形式保存，用户明确要求网页一律存 .mhtml。
OFFICE_EXTENSIONS = ("pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "mhtml")

#: 视频类扩展名。默认不下载，由 --include-video 显式开启。
VIDEO_EXTENSIONS = ("mp4", "mkv", "webm", "mov", "avi", "m4v", "flv", "wmv")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lmc-downloader",
        description="Archive an XJTLU Learning Mall Core course with its structure.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="log every request and decision")

    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("course_id", help="numeric course id from course/view.php?id=N")
        sub.add_argument("--cookies", default=None,
                         help="Netscape cookies.txt path or a raw Cookie header "
                              "(default: the LMC_COOKIES environment variable)")
        sub.add_argument("--snapshot", default=None,
                         help="read the course structure from a saved .mhtml/.html "
                              "instead of the live page")
        sub.add_argument("--root", default=None,
                         help="destination course folder (download only)")
        sub.add_argument("--host", default=DEFAULT_HOST,
                         help="course host (default: %(default)s)")

    outline = subparsers.add_parser("outline", help="show the course structure")
    add_common(outline)
    outline.add_argument("--json", dest="json_out", default=None,
                         help="write the outline to this JSON file")

    download = subparsers.add_parser("download", help="download course files")
    add_common(download)
    download.add_argument("--ext", default="",
                          help="comma separated allow-list, e.g. pdf,docx "
                               "('office' selects pdf/doc/docx/ppt/pptx/xls/xlsx)")
    download.add_argument("--types", default="resource,folder,url,assign",
                          help="activity types to resolve (default: %(default)s)")
    download.add_argument("--dry-run", action="store_true",
                          help="resolve and report, write nothing")
    download.add_argument(
        "--layout",
        choices=("files-flat", "link-name", "mirror"),
        default="files-flat",
        help="how a single-file activity is stored: files-flat = file directly "
             "in the chapter folder; link-name = file named after the activity, "
             "no extra folder; mirror = one folder per activity",
    )
    download.add_argument("--no-dedupe", action="store_true",
                          help="keep a separate copy per activity even when the "
                               "underlying upload is identical")
    download.add_argument(
        "--include-video",
        action="store_true",
        help="also download video files (mp4/mkv/webm/mov/...). "
             "Off by default so a course archive stays text-and-documents only.",
    )
    download.add_argument("--limit", type=int, default=0,
                          help="stop after N downloadable items")
    download.add_argument("--manifest", default=None,
                          help="manifest path (default: <root>/manifest.json)")
    return parser


def _make_client(args) -> HttpClient:
    cookies = None
    if not args.snapshot:
        raw = load_cookies(args.cookies)
        cookies = CookieStore(raw)
        if not has_session_cookie(raw):
            print("warning: no MoodleSession cookie found; the site may treat "
                  "this as a guest and hide course content", file=sys.stderr)
    return HttpClient(cookies=cookies, allowed_hosts={args.host})


def _load_outline(client: HttpClient, args) -> CourseOutline:
    if args.snapshot:
        data = Path(args.snapshot).read_bytes()
        outline = parse_outline(data, course_id=args.course_id)
        if not outline.course_id:
            outline.course_id = args.course_id
        return outline

    response = client.get(course_url(args.course_id))
    if response.is_html and "login" in response.url:
        raise SessionExpired("session expired: course page redirected to login")
    return parse_outline(response.content, course_id=args.course_id)


def command_outline(args) -> int:
    client = _make_client(args)
    with client:
        outline = _load_outline(client, args)

    print("course  : %s" % (outline.title or "(untitled)"))
    print("id      : %s" % outline.course_id)
    print("sections: %d" % len(outline.sections))
    print("activity: %d" % len(outline.activities))
    counts = {}
    for activity in outline.activities:
        counts[activity.modtype] = counts.get(activity.modtype, 0) + 1
    if counts:
        print("types   : %s" % ", ".join(
            "%s=%d" % (k, v) for k, v in sorted(counts.items())))
    print()
    current = None
    for activity in outline.activities:
        if activity.section != current:
            current = activity.section
            print("[%s]" % current)
        print("   %-14s %s" % (activity.modtype, activity.name))

    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(outline.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("\nwrote %s" % path)
    return 0


def _wanted_extensions(spec: str, include_video: bool = False) -> set:
    """解析 --ext；视频只在 --include-video 时进入 allow-list。

    spec 为空表示「不限类型」，但视频仍然默认排除——用户要求视频必须显式选择。
    """
    if not spec:
        base = set()
    elif spec.strip().lower() == "office":
        base = set(OFFICE_EXTENSIONS)
    else:
        base = {item.strip().lower().lstrip(".") for item in spec.split(",") if item.strip()}

    if include_video:
        base |= set(VIDEO_EXTENSIONS)
    return base


def command_download(args) -> int:
    client = _make_client(args)
    wanted_types = {t.strip() for t in args.types.split(",") if t.strip()}
    allowed_ext = _wanted_extensions(args.ext, include_video=args.include_video)

    root = Path(args.root) if args.root else Path.cwd() / "LMC" / str(args.course_id)

    with client:
        outline = _load_outline(client, args)
        crawler = Crawler(client)

        targets = [a for a in outline.activities if a.modtype in wanted_types]
        print("course  : %s" % (outline.title or "(untitled)"))
        print("root    : %s" % root)
        print("targets : %d activities" % len(targets))
        if args.dry_run:
            print("mode    : dry run, nothing will be written")
        print()

        fetcher = Fetcher(
            client,
            root=root,
            dedupe=not args.no_dedupe,
        layout=args.layout,
            dry_run=args.dry_run,
        )
        fetcher.manifest.course_id = outline.course_id
        fetcher.manifest.course_title = outline.title

        results: List[FetchResult] = []
        stopped = 0
        try:
            for index, activity in enumerate(targets, 1):
                if args.limit and stopped >= args.limit:
                    break
                try:
                    refs = crawler.resolve(activity)
                except SessionExpired:
                    raise
                except HttpError as exc:
                    print("[%d/%d] FAIL  %s: %s"
                          % (index, len(targets), activity.name, exc))
                    continue

                for ref in refs:
                    if ref.kind == "link":
                        print("[%d/%d] link  %-10s %s"
                              % (index, len(targets), activity.modtype, activity.name))
                        if ref.real_url:
                            print("            -> %s" % ref.real_url)
                        results.append(FetchResult(
                            ref=ref, status="link", message=ref.error or ""))
                        continue

                    if ref.kind != "folder":
                        # 扩展名要以真实下载地址与 MIME 为准：活动标题通常没有后缀
                        # （例如「CW1 Submission Guide」指向 .mp4），只看 ref.name
                        # 会漏掉视频，把它们一并下载下来。
                        candidates = [
                            Path(urlparse(ref.real_url or "").path).suffix.lower().lstrip("."),
                            Path(ref.name).suffix.lower().lstrip("."),
                            (EXT_FOR_MIME.get((ref.content_type or "").split(";")[0].strip().lower(), "")),
                        ]
                        found = [c for c in candidates if c]
                        reason = ""
                        # 视频必须显式 --include-video，即便 --ext 为空也一样。
                        if not args.include_video and any(c in VIDEO_EXTENSIONS for c in found):
                            reason = "video (use --include-video to include)"
                        elif allowed_ext and found and not (set(found) & allowed_ext):
                            reason = ".%s not in --ext" % found[0]
                        if reason:
                            print("[%d/%d] skip  %-6s %s  [%s]"
                                  % (index, len(targets), (found[0] if found else "?"),
                                     activity.name, reason))
                            continue

                    result = fetcher.fetch(ref)
                    results.append(result)
                    stopped += 1
                    flag = {"downloaded": "OK  ", "skipped": "same",
                            "duplicate": "dup ", "adopted": "keep",
                            "failed": "FAIL", "link": "link"}.get(result.status, "?   ")
                    detail = result.path or result.message
                    print("[%d/%d] %s  %-9s %s"
                          % (index, len(targets), flag, activity.modtype, activity.name))
                    if result.status == "downloaded":
                        print("            %s  %s"
                              % (human_bytes(result.bytes_written), detail))
                    elif result.status in ("failed", "duplicate"):
                        print("            %s" % detail)
        except SessionExpired as exc:
            print("\nSESSION EXPIRED: %s" % exc, file=sys.stderr)
            print("Refresh the cookie file and run the same command again; "
                  "finished files are skipped.", file=sys.stderr)
            if not args.dry_run:
                if args.manifest:
                    fetcher.manifest.path = Path(args.manifest)
                fetcher.manifest.save()
            return 2
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
            if not args.dry_run:
                fetcher.manifest.save()
            return 2

    if not args.dry_run:
        if args.manifest:
            fetcher.manifest.path = Path(args.manifest)
        saved = fetcher.manifest.save()
        print("\nmanifest: %s" % saved)

    print("summary : %s" % fetcher.summary(results))
    failed = [r for r in results if r.status == "failed"]
    if failed:
        print("\n%d item(s) could not be downloaded:" % len(failed))
        for result in failed:
            print("  - %s / %s: %s"
                  % (result.ref.section, result.ref.activity, result.message))
        return 2
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        if args.command == "outline":
            return command_outline(args)
        return command_download(args)
    except CookieError as exc:
        print("cookie error: %s" % exc, file=sys.stderr)
        return 1
    except SessionExpired as exc:
        print("session expired: %s" % exc, file=sys.stderr)
        return 2
    except (HttpError, RuntimeError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
