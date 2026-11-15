"""Turn a saved course page (``.mhtml`` / ``.html``) into raw HTML.

Kept separate from the crawler so the MIME handling can be tested on its own.
Only the standard library is used: ``email`` already knows how to split a
MHTML multipart container, so there is no reason to hand-roll it.
"""

from __future__ import annotations

import email
import email.policy
import re
from typing import Optional


def is_mhtml(raw: bytes) -> bool:
    head = raw[:2048].lstrip()
    return head.startswith(b"From:") or b"Snapshot-Content-Location:" in head


def decode_snapshot(raw: bytes) -> str:
    """Return the HTML document contained in a snapshot file.

    Handles three shapes seen in practice:
    * plain ``.html``                -> decoded as-is
    * ``.mhtml`` saved by Blink      -> first ``text/html`` MIME part
    * ``.mhtml`` with only one part  -> that part
    """
    if not is_mhtml(raw):
        return _decode_text(raw)

    message = email.message_from_bytes(raw, policy=email.policy.default)
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return _decode_text(payload, part.get_content_charset())
    payload = message.get_payload(decode=True)
    if payload:
        return _decode_text(payload, message.get_content_charset())
    return _decode_text(raw)


def snapshot_source_url(raw: bytes) -> Optional[str]:
    """The URL a snapshot was saved from, when the file records one."""
    head = raw[:4096].decode("utf-8", "replace")
    match = re.search(r"^Snapshot-Content-Location:\s*(\S+)", head, re.M)
    return match.group(1) if match else None


def _decode_text(raw: bytes, charset: Optional[str] = None) -> str:
    if charset:
        try:
            return raw.decode(charset, "replace")
        except LookupError:
            pass
    for candidate in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw.decode(candidate)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")
