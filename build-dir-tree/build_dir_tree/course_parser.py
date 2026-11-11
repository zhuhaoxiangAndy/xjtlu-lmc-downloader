#!/usr/bin/env python3
"""build-dir-tree: parse a Moodle (LMC) course page snapshot into a directory spec.

The parser is deliberately dependency-free: it decodes a saved MHTML file,
extracts the course index / course content markup, and rebuilds the
section -> subsection -> activity hierarchy that Moodle renders.

Public entry points:
    parse_mhtml(bytes)      -> Course
    parse_html(str)         -> Course
    Course.to_spec(...)     -> dict (JSON-serialisable directory spec)
"""

from __future__ import annotations

import quopri
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

__all__ = ["Activity", "Section", "Course", "parse_html", "parse_mhtml", "ParseError"]

VOID_TAGS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)

# Moodle module type -> short token used in leaf directory names.


class ParseError(RuntimeError):
    """Raised when the snapshot does not look like a Moodle course page."""


@dataclass
class Activity:
    id: int
    name: str
    modtype: str
    section_id: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "modtype": self.modtype,
            "section_id": self.section_id,
        }


@dataclass
class Section:
    num: int
    name: str
    section_id: int = 0
    parent: Optional["Section"] = None
    children: List["Section"] = field(default_factory=list)
    activities: List[Activity] = field(default_factory=list)
    delegated: bool = False

    @property
    def is_top(self) -> bool:
        return self.parent is None

    def real_activities(self) -> List[Activity]:
        """Activities excluding the 'subsection' placeholder modules."""
        return [a for a in self.activities if a.modtype != "subsection"]

    def walk(self) -> Iterable["Section"]:
        yield self
        for child in self.children:
            yield from child.walk()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "num": self.num,
            "section_id": self.section_id,
            "name": self.name,
            "delegated": self.delegated,
            "parent_num": self.parent.num if self.parent else None,
            "activities": [a.as_dict() for a in self.activities],
            "children": [c.as_dict() for c in self.children],
        }


@dataclass
class Course:
    title: str = ""
    source_url: str = ""
    course_id: Optional[int] = None
    sections: List[Section] = field(default_factory=list)

    # -- queries ---------------------------------------------------------
    def top_sections(self) -> List[Section]:
        return sorted(self.sections, key=lambda s: s.num)

    def all_sections(self) -> List[Section]:
        out: List[Section] = []
        for top in self.top_sections():
            out.extend(top.walk())
        return out

    def count_activities(self, include_subsection_placeholder: bool = False) -> int:
        total = 0
        for sec in self.all_sections():
            if include_subsection_placeholder:
                total += len(sec.activities)
            else:
                total += len(sec.real_activities())
        return total

    def summary(self) -> Dict[str, Any]:
        tops = self.top_sections()
        return {
            "title": self.title,
            "source_url": self.source_url,
            "course_id": self.course_id,
            "top_sections": len(tops),
            "subsections": sum(len(t.children) for t in tops),
            "activities": self.count_activities(),
        }

    def as_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "source_url": self.source_url,
            "course_id": self.course_id,
            "sections": [s.as_dict() for s in self.top_sections()],
        }

    def to_tree(self, policy: Optional["NamePolicy"] = None) -> List[Dict[str, Any]]:
        """Nest the course into a tree of nodes for graphical display.

        Each node carries a stable ``id`` so a client can select or deselect
        individual sections/activities, plus the resolved directory name and
        the path it maps to.

        Returns a list of top-level nodes; children are in ``children``.
        """
        policy = policy or DEFAULT_POLICY

        def activity_node(sec: Section, act: Activity, parent_path: str) -> Dict[str, Any]:
            name = apply_name_policy(act.name, policy)
            path = join_name(parent_path, name, separator="/")
            return {
                "id": "cm-%d" % act.id,
                "kind": "activity",
                "label": act.name,
                "dirName": name,
                "path": path,
                "modtype": act.modtype,
                "children": [],
            }

        def section_node(sec: Section, parent_path: str, top: Optional[Section] = None) -> Dict[str, Any]:
            name = apply_name_policy(sec.name, policy)
            if top is not None and policy.number:
                name = "%02d.%02d-%s" % (top.num, sec.num, name)
            elif top is None and policy.number:
                name = "%02d-%s" % (sec.num, name)
            path = join_name(parent_path, name, separator="/")
            kids: List[Dict[str, Any]] = []
            if top is None:
                for child in sorted(sec.children, key=lambda s: s.num):
                    kids.append(section_node(child, path, top=sec))
            for idx, act in enumerate(sec.real_activities(), start=1):
                node = activity_node(sec, act, path)
                if policy.number:
                    node["dirName"] = "%02d %s" % (idx, node["dirName"])
                    node["path"] = join_name(path, node["dirName"], separator="/")
                kids.append(node)
            return {
                "id": "section-%d" % sec.num,
                "kind": "section",
                "label": sec.name,
                "dirName": name,
                "path": path,
                "sectionNum": sec.num,
                "delegated": sec.delegated,
                "children": kids,
            }

        return [section_node(top, "") for top in self.top_sections()]


    def to_spec(
        self,
        layout: str = "sections",
        root: Optional[str] = None,
        separator: str = "/",
        include_empty_subsections: bool = True,
        policy: Optional["NamePolicy"] = None,
    ) -> Dict[str, Any]:
        """Build a serialisable directory spec.

        layout:
            sections -> one directory per section/subsection
            full     -> sections plus one leaf directory per activity

        policy controls naming. By default names are byte-for-byte the course
        page titles (see :class:`NamePolicy`); numbering and space handling are
        opt-in.
        """
        if layout not in ("sections", "full"):
            raise ParseError("layout must be 'sections' or 'full'")

        policy = policy or DEFAULT_POLICY
        dirs: List[str] = []
        records: List[Dict[str, Any]] = []

        def name_of(section: Section) -> str:
            base = apply_name_policy(section.name, policy)
            return ("%02d-" % section.num) + base if policy.number else base

        def set_num(parent_num: int, child: Section) -> str:
            if policy.number:
                return "%s%02d.%02d-" % ("", parent_num, child.num)
            return ""

        def add(path: str, section: Section, activity: Optional[Activity] = None) -> None:
            if path in dirs:
                return
            dirs.append(path)
            record: Dict[str, Any] = {
                "path": path,
                "section_num": section.num,
                "section_name": section.name,
            }
            if activity is not None:
                record.update(
                    {
                        "activity_id": activity.id,
                        "activity_name": activity.name,
                        "modtype": activity.modtype,
                    }
                )
            records.append(record)

        for top in self.top_sections():
            top_name = name_of(top)
            add(top_name, top)
            if layout == "full":
                for idx, act in enumerate(top.real_activities(), start=1):
                    leaf = apply_name_policy(act.name, policy)
                    if policy.number:
                        leaf = "%02d %s" % (idx, leaf)
                    add(join_name(top_name, leaf, separator=separator), top, act)
            for child in sorted(top.children, key=lambda s: s.num):
                if not include_empty_subsections and not child.real_activities():
                    continue
                prefix = "%02d.%02d-" % (top.num, child.num) if policy.number else ""
                child_name = join_name(
                    top_name, prefix + apply_name_policy(child.name, policy), separator=separator
                )
                add(child_name, child)
                if layout == "full":
                    for idx, act in enumerate(child.real_activities(), start=1):
                        leaf = apply_name_policy(act.name, policy)
                        if policy.number:
                            leaf = "%02d %s" % (idx, leaf)
                        add(
                            join_name(child_name, leaf, separator=separator),
                            child,
                            act,
                        )

        return {
            "course": self.summary(),
            "layout": layout,
            "root": root,
            "separator": separator,
            "policy": {
                "illegal_replace": policy.illegal_replace,
                "space_replace": policy.space_replace,
                "collapse_spaces": policy.collapse_spaces,
                "drop_trailing": policy.drop_trailing,
                "number": policy.number,
            },
            "count": len(dirs),
            "dirs": dirs,
            "records": records,
        }

# -- naming policy -------------------------------------------------------

_ENTITIES = (
    ("&amp;", "&"),
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("&quot;", chr(34)),
    ("&#39;", "'"),
    ("&#x27;", "'"),
    ("&#034;", chr(34)),
    ("&nbsp;", " "),
)

# Characters Windows cannot store in a file name:
# < > : " / \ | ? * plus control characters.
_ILLEGAL = chr(60) + chr(62) + chr(58) + chr(34) + chr(47) + chr(92) + chr(124) + chr(63) + chr(42)
_ILLEGAL_SET = frozenset(_ILLEGAL) | frozenset(chr(i) for i in range(32))


def decode_entities(text: str, max_passes: int = 3) -> str:
    """Decode the HTML entities Moodle emits; never touch whitespace.

    Moodle double-escapes section names, so a saved page can contain
    "Speech &amp;amp; Creativity" for what the browser displays as
    "Speech & Creativity". Decode repeatedly until stable (bounded, so a
    pathological input cannot loop forever).
    """
    for _ in range(max_passes):
        before = text
        for src, dst in _ENTITIES:
            text = text.replace(src, dst)
        if text == before:
            break
    return text


@dataclass(frozen=True)
class NamePolicy:
    """How a course title becomes a directory name.

    Defaults are faithful: the title is used exactly as it appears on the course
    page, except that characters Windows cannot store are *deleted*.

    Attributes:
        illegal_replace:
            String substituted for each illegal character. Empty (the default)
            means delete it. There is no built-in choice of replacement -- the
            caller decides, including deciding to use nothing at all.
        space_replace:
            String substituted for each space. None (the default) means keep
            spaces exactly as they are.
        collapse_spaces:
            Collapse runs of spaces into one. Off by default, so "a  b" stays
            "a  b".
        drop_trailing:
            Strip trailing dots and spaces. On by default because Windows
            discards them anyway; turning it off lets the caller see the
            mismatch.
        number:
            Prefix directories with their Moodle number ("04-", "04.16-",
            "01 "). Off by default.
    """

    illegal_replace: str = ""
    space_replace: Optional[str] = None
    collapse_spaces: bool = False
    drop_trailing: bool = True
    number: bool = False


DEFAULT_POLICY = NamePolicy()


def apply_name_policy(text: str, policy: Optional[NamePolicy] = None) -> str:
    """Turn a raw Moodle title into one path component under `policy`."""
    policy = policy or DEFAULT_POLICY
    name = decode_entities(text)

    # Windows silently drops trailing dots and spaces, so a name keeping them
    # would never match what lands on disk. Do this before substitution so a
    # trailing space does not turn into a trailing replacement character.
    if policy.drop_trailing:
        name = name.rstrip(" .")

    if _ILLEGAL_SET.intersection(name):
        name = "".join(
            policy.illegal_replace if ch in _ILLEGAL_SET else ch for ch in name
        )

    if policy.collapse_spaces:
        while "  " in name:
            name = name.replace("  ", " ")

    if policy.space_replace is not None:
        name = name.replace(" ", policy.space_replace)

    return name or "untitled"


def sanitize_name(text: str, policy: Optional[NamePolicy] = None) -> str:
    """Backwards-compatible alias for :func:`apply_name_policy`."""
    return apply_name_policy(text, policy)


def slugify(text: str, fallback: str = "untitled") -> str:
    """Deprecated alias kept for compatibility; use :func:`apply_name_policy`."""
    value = apply_name_policy(text)
    return value if value != "untitled" else fallback


def join_name(*parts: str, separator: str = "/") -> str:
    """Join non-empty path segments with the given separator."""
    return separator.join(str(p) for p in parts if p)

# -- MHTML / HTML decoding -----------------------------------------------

_BOUNDARY_RE = re.compile(rb"^--(-{2,}.*?)--?\s*$", re.MULTILINE)


def mhtml_headers(raw: bytes) -> Dict[str, str]:
    """Top-level MIME headers of the MHTML archive (Snapshot-Content-Location etc.)."""
    head = raw[:8192].decode("latin-1", "replace")
    cut = head.find("\r\n\r\n")
    if cut == -1:
        cut = head.find("\n\n")
    block = head[: cut if cut != -1 else len(head)]
    headers: Dict[str, str] = {}
    for line in block.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
    return headers


def mhtml_html_part(raw: bytes) -> bytes:
    """Return the decoded bytes of the text/html part of an MHTML file."""
    head = raw[:4096].decode("latin-1", "replace")
    m = re.search(r'boundary="([^"]+)"', head)
    if not m:
        raise ParseError("no MIME boundary found; is this a saved .mhtml page?")
    boundary = ("--" + m.group(1)).encode("latin-1")

    parts = raw.split(boundary)
    for part in parts[1:]:
        header_end = part.find(b"\r\n\r\n")
        alt_end = part.find(b"\n\n")
        if header_end == -1 or (alt_end != -1 and alt_end < header_end):
            if alt_end == -1:
                continue
            header_end = alt_end
            body = part[header_end + 2:]
        else:
            body = part[header_end + 4:]

        headers = part[:header_end].decode("latin-1", "replace").lower()
        if "text/html" not in headers:
            continue
        if "quoted-printable" in headers:
            return quopri.decodestring(body)
        if "base64" in headers:
            import base64

            return base64.b64decode(re.sub(rb"\s+", b"", body))
        return body
    raise ParseError("no text/html part found in the MHTML snapshot")


def _snapshot_meta(html: str) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    if m:
        meta["title"] = decode_entities(m.group(1))
    for key, pattern in (
        ("source_url", r"Snapshot-Content-Location:\s*(\S+)"),
        ("content_location", r'Content-Location:\s*(\S+)'),
    ):
        m = re.search(pattern, html, re.I)
        if m:
            meta[key] = m.group(1)
    return meta


_TAG_RE = re.compile(r"<(\/?)([a-zA-Z][a-zA-Z0-9:-]*)([^>]*)>", re.S)


def _scan_tags(html: str):
    """Yield (closing, tagname, attrs, offset)."""
    for m in _TAG_RE.finditer(html):
        closing = m.group(1) == "/"
        tag = m.group(2).lower()
        attrs = m.group(3) or ""
        if attrs.rstrip().endswith("/") or tag in VOID_TAGS:
            yield closing, tag, attrs, m.start(), True
            continue
        yield closing, tag, attrs, m.start(), False


def _attr(attrs: str, name: str) -> Optional[str]:
    m = re.search(name + r'\s*=\s*"([^"]*)"', attrs)
    if m:
        return m.group(1)
    m = re.search(name + r"\s*=\s*'([^']*)'", attrs)
    return m.group(1) if m else None


def _course_id_from(source_url: str, html: str) -> Optional[int]:
    m = re.search(r"course/view\.php\?id=(\d+)", source_url or "")
    if m:
        return int(m.group(1))
    m = re.search(r"course/view\.php\?id=(\d+)", html[:200000])
    return int(m.group(1)) if m else None


def parse_html(html: str) -> Course:
    """Parse the rendered Moodle course page markup into a Course."""
    if 'data-for="section"' not in html and 'class="course-section' not in html:
        raise ParseError("no Moodle course sections found in this HTML")

    meta = _snapshot_meta(html)
    course = Course(
        title=meta.get("title", ""),
        source_url=meta.get("source_url") or meta.get("content_location", ""),
    )
    course.course_id = _course_id_from(meta.get("source_url", ""), html)

    stack: List[Dict[str, Any]] = []  # open element frames
    by_num: Dict[int, Section] = {}

    for closing, tag, attrs, offset, self_closed in _scan_tags(html):
        if closing:
            for i in range(len(stack) - 1, -1, -1):
                if stack[i]["tag"] == tag:
                    del stack[i:]
                    break
            continue
        if self_closed:
            continue

        frame: Dict[str, Any] = {"tag": tag, "section": None}
        stack.append(frame)

        if tag == "li":
            m = re.search(r'id="section-(\d+)"', attrs)
            if m:
                num = int(m.group(1))
                parent = None
                for open_frame in reversed(stack[:-1]):
                    if open_frame["section"] is not None:
                        parent = open_frame["section"]
                        break
                section = Section(
                    num=num,
                    name=decode_entities(_attr(attrs, "data-sectionname") or ""),
                    section_id=int(_attr(attrs, "data-id") or 0),
                    parent=parent,
                    delegated=bool(re.search(r"delegated-section", attrs))
                    or bool(re.search(r"delegated-section", _frame_class(html, offset))),
                )
                frame["section"] = section
                by_num[num] = section
            else:
                m = re.search(r'id="module-(\d+)"', attrs)
                if m:
                    owner = None
                    for open_frame in reversed(stack[:-1]):
                        if open_frame["section"] is not None:
                            owner = open_frame["section"]
                            break
                    if owner is not None:
                        modtype = "unknown"
                        mt = re.search(r"modtype_([a-z0-9]+)", attrs)
                        if mt:
                            modtype = mt.group(1)
                        else:
                            mt = re.search(r'class="[^"]*activity\s+([a-z0-9]+)', attrs)
                            if mt:
                                modtype = mt.group(1)
                        name = _activity_name(html, offset)
                        owner.activities.append(
                            Activity(id=int(m.group(1)), name=name,
                                     modtype=modtype, section_id=owner.section_id)
                        )

    # Wire up the tree: sections with a parent go under it, rest are top-level.
    course.sections = []
    for section in sorted(by_num.values(), key=lambda s: s.num):
        if section.parent is not None and section.parent.num in by_num:
            section.parent.children.append(section)
        else:
            section.parent = None
            course.sections.append(section)

    if not course.sections:
        raise ParseError("parsed no course sections")
    return course


def _frame_class(html: str, offset: int) -> str:
    """Class attribute of the tag starting at offset (best-effort, cheap)."""
    end = html.find(">", offset)
    return html[offset:end + 1] if end != -1 else ""


def _activity_name(html: str, offset: int) -> str:
    window = html[offset:offset + 4000]
    m = re.search(r'data-activityname="([^"]*)"', window)
    if m:
        return decode_entities(m.group(1))
    m = re.search(r'<div class="activitytitle[^"]*">(.*?)</div>', window, re.S)
    if m:
        inner = re.sub(r"<[^>]+>", " ", m.group(1))
        return decode_entities(inner)
    m = re.search(r"<span[^>]*class=\"[^\"]*instancename[^\"]*\"[^>]*>(.*?)</span>", window, re.S)
    if m:
        return decode_entities(re.sub(r"<[^>]+>", " ", m.group(1)))
    return ""


def parse_mhtml(raw: bytes) -> Course:
    """Parse a saved .mhtml snapshot."""
    headers = mhtml_headers(raw)
    part = mhtml_html_part(raw)
    html = part.decode("utf-8", "replace")
    course = parse_html(html)
    if not course.source_url:
        course.source_url = headers.get("snapshot-content-location", "")
    if not course.course_id:
        course.course_id = _course_id_from(course.source_url, "")
    subject = headers.get("subject", "")
    if not course.title and subject:
        course.title = decode_entities(subject)
    return course


def parse_bytes(raw: bytes) -> Course:
    """Dispatch on content: MHTML snapshot or a plain HTML export."""
    if raw[:2048].lstrip().startswith(b"<") and b"MIME-Version" not in raw[:2048]:
        return parse_html(raw.decode("utf-8", "replace"))
    if b"Content-Type: multipart" in raw[:4096]:
        return parse_mhtml(raw)
    return parse_html(raw.decode("utf-8", "replace"))
