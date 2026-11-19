"""Enumerate a course's sections, activities and real file URLs.

Two inputs are supported, because the two existing tools produce different
things:

* A saved snapshot (``.mhtml`` / ``.html``) — parsed by ``build_dir_tree``, so
  the structure is identical to the directory tree the user already built.
  Snapshots contain no ``pluginfile.php`` URLs, only activity view URLs, so
  files still need resolving over HTTP.
* A live course page — fetched with the user's cookies and parsed by the same
  ``build_dir_tree`` parser. This is the normal path: it resolves real URLs.

Resolution rules learned from the live site
-------------------------------------------
* ``mod/resource/view.php?id=N``        302 -> ``pluginfile.php/...`` (a file),
  or an HTML viewer page when the resource is itself an HTML file.
* ``mod/folder/view.php?id=N``          may expose ``download_folder.php?id=N``
  (a ZIP), and may answer "Cannot download this folder" when the folder holds
  only sub-folders. Both outcomes are recorded, not treated as errors.
* ``mod/url/view.php?id=N``             an external link; recorded, not fetched.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .http import HttpClient, HttpError, SessionExpired
from .names import sanitize_name
from .snapshot import decode_snapshot, snapshot_source_url

LOGGER = logging.getLogger("lmc_downloader.crawler")

COURSE_URL = "https://core.xjtlu.edu.cn/course/view.php?id=%s"
VIEW_URL = "https://core.xjtlu.edu.cn/mod/%s/view.php?id=%s"
FOLDER_ZIP_URL = "https://core.xjtlu.edu.cn/mod/folder/download_folder.php?id=%s"

#: Activity types that can yield downloadable files.
FILE_TYPES = frozenset(["resource", "folder", "url", "assign"])

#: Activity types that never carry files but are still worth recording.
NON_FILE_TYPES = frozenset(
    ["forum", "moodleoverflow", "bigbluebuttonbn", "page", "label", "quiz",
     "h5pactivity", "subsection", "book", "choice", "feedback", "workshop",
     "attendance", "wiki", "lesson", "glossary", "chat", "data"]
)


@dataclass
class FileRef:
    """One downloadable item, with whatever is known before fetching it."""

    name: str
    view_url: str = ""
    real_url: str = ""
    section: str = ""
    activity: str = ""
    modtype: str = ""
    size: Optional[int] = None
    content_type: str = ""
    kind: str = "file"          # file | folder | link | export
    error: str = ""
    children: List["FileRef"] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error

    def to_dict(self) -> Dict[str, object]:
        data: Dict[str, object] = {
            "name": self.name,
            "section": self.section,
            "activity": self.activity,
            "modtype": self.modtype,
            "kind": self.kind,
            "view_url": self.view_url,
            "real_url": self.real_url,
            "content_type": self.content_type,
        }
        if self.size is not None:
            data["size"] = self.size
        if self.error:
            data["error"] = self.error
        if self.children:
            data["children"] = [c.to_dict() for c in self.children]
        return data


@dataclass
class Activity:
    name: str
    modtype: str
    section: str
    view_url: str = ""
    instance: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "name": self.name,
            "modtype": self.modtype,
            "section": self.section,
            "view_url": self.view_url,
        }


@dataclass
class CourseOutline:
    course_id: str
    title: str = ""
    activities: List[Activity] = field(default_factory=list)

    @property
    def sections(self) -> List[str]:
        seen, ordered = set(), []
        for activity in self.activities:
            if activity.section not in seen:
                seen.add(activity.section)
                ordered.append(activity.section)
        return ordered

    def by_type(self, *modtypes: str) -> List[Activity]:
        wanted = set(modtypes)
        return [a for a in self.activities if a.modtype in wanted]

    def to_dict(self) -> Dict[str, object]:
        return {
            "course_id": self.course_id,
            "title": self.title,
            "sections": self.sections,
            "activity_count": len(self.activities),
            "activities": [a.to_dict() for a in self.activities],
        }


def course_url(course_id: str) -> str:
    return COURSE_URL % course_id


def view_url(modtype: str, instance: str) -> str:
    if not modtype or not instance:
        return ""
    return VIEW_URL % (modtype, instance)


def _activity_ids(html: str) -> Dict[str, str]:
    """Map ``activity-<modtype>-<instance>`` class names to modtypes.

    Moodle renders every activity as ``<li class="activity modtype_resource"
    id="module-123">``; the instance id lives on the inner link. Parsing the
    snapshot HTML again is avoided by letting ``build_dir_tree`` own structure
    and only extracting URLs here.
    """
    pairs = {}
    for match in re.finditer(
        r'<li[^>]*class="[^"]*modtype_(\w+)[^"]*"[^>]*id="module-(\d+)"', html
    ):
        pairs[match.group(2)] = match.group(1)
    for match in re.finditer(
        r'<li[^>]*id="module-(\d+)"[^>]*class="[^"]*modtype_(\w+)[^"]*"', html
    ):
        pairs.setdefault(match.group(1), match.group(2))
    return pairs


def parse_outline(snapshot_bytes: bytes, course_id: str = "") -> CourseOutline:
    """Build a :class:`CourseOutline` from a saved course page.

    ``build-dir-tree`` is used when it is installed, because it resolves nested
    sub-sections exactly the way the directory tree does. It stays optional: the
    built-in parser below handles the same page shape, so a plain
    ``pip install lmc-downloader`` still works. The live course page and the
    offline snapshot therefore share one code path with no hard dependency.
    """
    html = decode_snapshot(snapshot_bytes)
    detected = snapshot_source_url(snapshot_bytes)
    if not course_id and detected:
        match = re.search(r"[?&]id=(\d+)", detected)
        if match:
            course_id = match.group(1)

    outline = _parse_with_build_dir_tree(snapshot_bytes, course_id)
    if outline is not None:
        return outline
    return parse_course_html(html, course_id=course_id)


SECTION_SEP = "\x1f"


def section_label(section) -> str:
    """章节的完整路径名：父周/子章节，与网页层级一致。"""
    names = []
    node = section
    while node is not None:
        name = (getattr(node, "name", "") or "").strip()
        if name:
            names.append(name)
        node = getattr(node, "parent", None)
    names.reverse()
    # 用不可见的单元分隔符连接父子章节：标题本身可能含 "/"
    # （例如 "Week 10: File I/O, List, Set and Map"），用 "/" 会把它拆错。
    return SECTION_SEP.join(names) if names else getattr(section, "name", "") or "Course"

def _parse_with_build_dir_tree(snapshot_bytes: bytes,
                               course_id: str) -> Optional[CourseOutline]:
    """Use build-dir-tree when present; return ``None`` when unavailable."""
    try:
        from build_dir_tree import parse_bytes  # type: ignore
    except ImportError:
        return None
    try:
        course = parse_bytes(snapshot_bytes)
    except Exception as exc:  # noqa: BLE001 - any parser failure falls back
        LOGGER.debug("build-dir-tree could not parse this page: %s", exc)
        return None

    outline = CourseOutline(
        course_id=course_id or str(course.course_id or ""),
        title=course.title or "",
    )
    for section in course.all_sections():
        # 子章节（Lecture/Tutorial/Lab）必须带上父周名，否则 11 个 Week 的
        # Lecture 会塌成同一个 section，活动全部落到错误的周里。
        label = section_label(section)
        for activity in section.real_activities():
            modtype = activity.modtype or ""
            instance = str(activity.id or "")
            outline.activities.append(
                Activity(
                    name=activity.name,
                    modtype=modtype,
                    section=label,
                    view_url=view_url(modtype, instance),
                    instance=instance,
                )
            )
    return outline if outline.activities else None


_SECTION_RE = re.compile(r'<li[^>]*\bid="section-(\d+)"[^>]*>', re.I)
#: Moodle stores the authoritative section title on the <li> itself.
_SECTION_NAME_ATTR = re.compile(r'data-sectionname="([^"]*)"', re.I)
_MODULE_RE = re.compile(r'<li[^>]*\bid="module-(\d+)"[^>]*>', re.I)
_CLASS_RE = re.compile(r'\bclass="([^"]*)"', re.I)
_SECTION_NAME_RE = re.compile(
    r'<h3[^>]*class="[^"]*sectionname[^"]*"[^>]*>(.*?)</h3>', re.S | re.I
)
_INSTANCE_RE = re.compile(
    r'<span[^>]*class="[^"]*instancename[^"]*"[^>]*>(.*?)</span>', re.S | re.I
)
_HREF_RE = re.compile(r'href="([^"]*/mod/(\w+)/view\.php\?id=(\d+))"', re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_ACCESSHIDE_RE = re.compile(
    r'<span[^>]*class="[^"]*(?:accesshide|sr-only|visually-hidden)[^"]*"[^>]*>.*?</span>',
    re.S | re.I,
)
_ENTITY_MAP = (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
               ("&#39;", "'"), ("&apos;", "'"), ("&quot;", '"'))


_ACTIVITY_SUFFIX = re.compile(
    r"\s+(File|Folder|Forum|Page|Assignment|Moodleoverflow|URL|Quiz|Book|Folder)$"
)


def _decode_entities(text: str) -> str:
    """Decode the handful of entities Moodle puts in attribute values."""
    for entity, char in _ENTITY_MAP:
        text = text.replace(entity, char)
    return re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)


def _strip_tags(text: str) -> str:
    text = _ACCESSHIDE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    for entity, char in _ENTITY_MAP:
        text = text.replace(entity, char)
    text = re.sub(r"\s+", " ", text).strip()
    # Moodle appends the activity type for screen readers, e.g.
    # "Week 3 Workbook File". Drop it so the name matches what users see.
    stripped = _ACTIVITY_SUFFIX.sub("", text)
    return stripped or text


def _next_section_before(position: int, marks, current_offset: int) -> bool:
    """True when another section marker sits between two offsets."""
    for offset, kind, _match in marks:
        if offset <= current_offset:
            continue
        if offset < position and kind == "section":
            return True
    return False


def parse_course_html(html: str, course_id: str = "") -> CourseOutline:
    """Parse a Moodle course page without any third-party or sibling package.

    Sections are located by ``id="section-N"``, activities by
    ``id="module-N"`` carrying a ``modtype_*`` class.

    Sub-sections (Lecture / Tutorial / Lab / References) are nested inside their
    parent week's ``<li>``, so the parent-child relation has to come from tag
    depth. Treating every section as a sibling — the previous behaviour — made
    `Lecture` swallow the whole week: CSI101 collapsed from 11 weeks into 6
    flat chapters and every activity landed under the wrong heading.
    """
    title = ""
    title_match = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    if title_match:
        title = _strip_tags(title_match.group(1))

    outline = CourseOutline(course_id=course_id, title=title)

    marks = []
    for match in _SECTION_RE.finditer(html):
        marks.append((match.start(), "section", match))
    for match in _MODULE_RE.finditer(html):
        marks.append((match.start(), "module", match))
    marks.sort(key=lambda item: item[0])

    # 用标签配对算出每个 section 的 DOM 深度，深度更大者为子章节
    depths = _section_depths(html)

    # 维护一个按深度排列的祖先栈：stack[d] 是深度为 d 的当前章节名
    stack = []

    current_section = ""
    for offset, kind, match in marks:
        if kind == "section":
            num = match.group(1)
            # 1) data-sectionname（Moodle 新主题）
            attr = _SECTION_NAME_ATTR.search(match.group(0))
            label = _decode_entities(attr.group(1)) if attr else ""
            # 2) 该 li 内紧跟的 <h3 class="sectionname">（旧主题与快照）
            if not label:
                h3 = re.search(
                    r'<h3[^>]*class="[^"]*sectionname[^"]*"[^>]*>(.*?)</h3>',
                    html[offset:offset + 4000], re.S | re.I,
                )
                if h3:
                    label = _strip_tags(h3.group(1))
            if not label:
                label = "Section %s" % num

            depth = depths.get(offset, 0)
            if depth <= 0:
                # 顶层：清空栈后入栈
                stack = [(depth, label)]
            else:
                # 弹出所有深度 >= 当前深度的祖先，剩下的最后一个就是父章节
                while stack and stack[-1][0] >= depth:
                    stack.pop()
                stack.append((depth, label))

            # 活动归属：父章节名 + 本子章节名，用 / 连接，与网页层级一致
            if len(stack) > 1:
                current_section = "/".join(name for _, name in stack)
            else:
                current_section = label
            continue

        instance = match.group(1)
        class_match = _CLASS_RE.search(match.group(0))
        classes = class_match.group(1) if class_match else ""
        modtype_match = re.search(r"modtype_(\w+)", classes)
        modtype = modtype_match.group(1) if modtype_match else ""

        window = html[offset:offset + 8000]
        name = ""
        instance_match = _INSTANCE_RE.search(window)
        if instance_match:
            name = _strip_tags(instance_match.group(1))
        href_match = _HREF_RE.search(window)
        href = href_match.group(1) if href_match else ""
        if not modtype and href_match:
            modtype = href_match.group(2)
        if not modtype or modtype == "subsection":
            continue
        if not href:
            href = view_url(modtype, instance)

        outline.activities.append(
            Activity(
                name=name or ("%s %s" % (modtype, instance)),
                modtype=modtype,
                section=current_section or "Course",
                view_url=href,
                instance=instance,
            )
        )

    return outline




_VOID_TAGS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)


def _section_depths(html: str) -> dict:
    """每个 ``id="section-N"`` 出现位置的 DOM 嵌套深度。

    Walks the tag stream once, keeping an element stack. The depth is the stack
    size at the moment the section <li> opens, so a section nested inside
    another section's <li> gets a strictly larger number.
    """
    depths = {}
    stack = []
    tag_re = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9:-]*)((?:\"[^\"]*\"|'[^']*'|[^>\"'])*?)(/?)>")
    section_re = re.compile(r'<li[^>]*\bid="section-(\d+)"', re.I)

    pos = 0
    while True:
        m = tag_re.search(html, pos)
        if not m:
            break
        pos = m.end()
        closing, tag, attrs, self_close = m.group(1), m.group(2).lower(), m.group(3), m.group(4)
        if tag in _VOID_TAGS or self_close:
            continue
        if closing:
            for i in range(len(stack) - 1, -1, -1):
                if stack[i] == tag:
                    del stack[i:]
                    break
            continue
        # 记录这个 <li> 之前刚被匹配到的 section 偏移
        if tag == "li" and 'id="section-' in m.group(0):
            depths[m.start()] = len(stack)
        stack.append(tag)
    return depths

class Crawler:
    """Resolve activities into concrete :class:`FileRef` objects."""

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def resolve(self, activity: Activity) -> List[FileRef]:
        """Resolve one activity into zero or more file references."""
        modtype = activity.modtype
        if modtype == "resource":
            return [self._resolve_resource(activity)]
        if modtype == "folder":
            return self._resolve_folder(activity)
        if modtype == "url":
            return [self._resolve_link(activity)]
        if modtype == "assign":
            return [self._resolve_link(activity, kind="link")]
        return []

    # -- per type -----------------------------------------------------------
    def _resolve_resource(self, activity: Activity) -> FileRef:
        ref = FileRef(
            name=sanitize_name(activity.name) or "resource",
            view_url=activity.view_url,
            section=activity.section,
            activity=activity.name,
            modtype=activity.modtype,
            kind="file",
        )
        if not activity.view_url:
            ref.error = "no view url for this activity"
            return ref
        try:
            probe = self.client.probe(activity.view_url)
        except SessionExpired:
            raise
        except HttpError as exc:
            ref.error = str(exc)
            return ref

        ref.real_url = probe.url
        ref.content_type = probe.content_type
        declaration = probe.headers.get("content-length")
        if declaration and declaration.isdigit():
            ref.size = int(declaration)

        if probe.is_html:
            inner = self._viewer_target(probe)
            if inner:
                ref.real_url = inner
                try:
                    again = self.client.probe(inner)
                    ref.content_type = again.content_type
                    if again.content_type:
                        ref.real_url = again.url
                except HttpError as exc:
                    LOGGER.debug("viewer re-probe failed for %s: %s", inner, exc)
                ref.kind = "viewer"
        return ref

    def _viewer_target(self, response) -> str:
        """Find the embedded file URL inside a Moodle resource viewer page."""
        text = response.text("utf-8")
        found = re.findall(
            r'(?:src|data|href)="([^"]*pluginfile\.php[^"]*)"', text, re.I
        )
        for url in found:
            if "core_admin" in url:
                continue
            if url.startswith("/"):
                from urllib.parse import urljoin
                url = urljoin(response.url, url)
            return url
        return ""

    def _resolve_folder(self, activity: Activity) -> List[FileRef]:
        refs: List[FileRef] = []
        instance = activity.instance
        if not instance:
            match = re.search(r"id=(\d+)", activity.view_url or "")
            instance = match.group(1) if match else ""
        if not instance:
            return [FileRef(name=sanitize_name(activity.name), section=activity.section,
                            activity=activity.name, modtype=activity.modtype,
                            view_url=activity.view_url, kind="folder",
                            error="cannot determine folder id")]

        zip_url = FOLDER_ZIP_URL % instance
        ref = FileRef(
            name=sanitize_name(activity.name) or "folder",
            view_url=activity.view_url,
            real_url=zip_url,
            section=activity.section,
            activity=activity.name,
            modtype=activity.modtype,
            kind="folder",
        )
        try:
            probe = self.client.probe(zip_url)
        except SessionExpired:
            raise
        except HttpError as exc:
            ref.error = str(exc)
            return [ref]

        if probe.content_type in ("application/zip", "application/x-zip-compressed",
                                  "application/octet-stream"):
            declaration = probe.headers.get("content-length")
            if declaration and declaration.isdigit():
                ref.size = int(declaration)
            return [ref]

        # Moodle answers 404 "Cannot download this folder" for folders that hold
        # only sub-folders. That is a property of the course, not a failure.
        ref.error = "folder is not downloadable as a ZIP (holds no direct files)"
        return [ref]

    def _resolve_link(self, activity: Activity, kind: str = "link") -> FileRef:
        ref = FileRef(
            name=sanitize_name(activity.name) or "link",
            view_url=activity.view_url,
            section=activity.section,
            activity=activity.name,
            modtype=activity.modtype,
            kind=kind,
        )
        if not activity.view_url:
            ref.error = "no view url"
            return ref
        try:
            response = self.client.get(activity.view_url)
        except SessionExpired:
            raise
        except HttpError as exc:
            ref.error = str(exc)
            return ref
        match = re.search(
            r'<div[^>]*class="[^"]*urlworkaround[^"]*"[^>]*>.*?href="([^"]+)"',
            response.text(), re.S | re.I,
        )
        if not match:
            match = re.search(r'<a[^>]+class="[^"]*url[^"]*"[^>]+href="([^"]+)"',
                              response.text(), re.I)
        if match:
            ref.real_url = match.group(1)
        else:
            ref.error = "no external url found on the page"
        return ref
