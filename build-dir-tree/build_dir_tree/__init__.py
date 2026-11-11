"""build-dir-tree: rebuild a course directory tree from an LMC/Moodle snapshot."""

from .course_parser import (
    DEFAULT_POLICY,
    Activity,
    Course,
    NamePolicy,
    ParseError,
    Section,
    apply_name_policy,
    join_name,
    parse_bytes,
    parse_html,
    parse_mhtml,
    sanitize_name,
    slugify,
)

__version__ = "1.4.0"

__all__ = [
    "DEFAULT_POLICY",
    "Activity",
    "Course",
    "NamePolicy",
    "ParseError",
    "Section",
    "apply_name_policy",
    "join_name",
    "parse_bytes",
    "parse_html",
    "parse_mhtml",
    "sanitize_name",
    "slugify",
    "__version__",
]
