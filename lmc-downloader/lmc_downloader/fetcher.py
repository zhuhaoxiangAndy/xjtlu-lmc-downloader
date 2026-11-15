"""Download resolved files into the course tree, with resume, dedupe and a manifest.

Layout
------
Files land at ``<root>/<section>/<activity>/<file>``, mirroring the course, so
the result matches the directories ``build-dir-tree`` creates. A ``manifest.json``
at the root records every item's section, activity, original URL, byte size,
SHA-256 and outcome.

Incremental archiving
---------------------
Re-running the same course is cheap and safe:

* an item whose recorded size and SHA-256 still match the file on disk is
  skipped without a network request beyond the name probe;
* an item whose remote size changed is downloaded again;
* an item present on disk but absent from the manifest is adopted, not
  re-downloaded and not overwritten.

Deduplication
-------------
Two activities frequently point at the same upload (the same workbook appears
under ``Week 5`` and ``Week 5 Tutorial``). With ``dedupe=True`` the second copy
is recorded in the manifest as a duplicate of the first and not written again,
which is what makes "one file per distinct upload" possible without losing the
per-section record.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .crawler import FileRef
from .http import HttpClient, HttpError, SessionExpired
from .names import choose_filename, human_bytes, sanitize_name, unique_path

LOGGER = logging.getLogger("lmc_downloader.fetcher")

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1
CHUNK = 64 * 1024


@dataclass
class FetchResult:
    ref: FileRef
    path: str = ""
    sha256: str = ""
    bytes_written: int = 0
    status: str = "pending"      # downloaded | skipped | adopted | duplicate | failed | link
    message: str = ""
    duplicate_of: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("downloaded", "skipped", "adopted", "link")

    def to_dict(self) -> Dict[str, object]:
        data = {
            "name": self.ref.name,
            "section": self.ref.section,
            "activity": self.ref.activity,
            "modtype": self.ref.modtype,
            "kind": self.ref.kind,
            "status": self.status,
            "path": self.path,
            "sha256": self.sha256,
            "bytes": self.bytes_written,
            "view_url": self.ref.view_url,
            "real_url": self.ref.real_url,
        }
        if self.message:
            data["message"] = self.message
        if self.duplicate_of:
            data["duplicate_of"] = self.duplicate_of
        return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class Manifest:
    root: Path
    course_id: str = ""
    course_title: str = ""
    entries: Dict[str, Dict[str, object]] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return self.root / MANIFEST_NAME

    @classmethod
    def load(cls, root: Path, course_id: str = "") -> "Manifest":
        root = Path(root)
        manifest = cls(root=root, course_id=course_id)
        if not manifest.path.exists():
            return manifest
        try:
            data = json.loads(manifest.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            LOGGER.warning("ignoring unreadable manifest %s: %s", manifest.path, exc)
            return manifest
        if data.get("version") != MANIFEST_VERSION:
            LOGGER.warning("manifest version %s differs from %s; starting fresh",
                           data.get("version"), MANIFEST_VERSION)
            return manifest
        manifest.course_id = data.get("course_id") or course_id
        manifest.course_title = data.get("course_title") or ""
        manifest.entries = {
            item["key"]: item
            for item in data.get("items", [])
            if isinstance(item, dict) and item.get("key")
        }
        return manifest

    def key(self, result: FetchResult) -> str:
        return "/".join(
            sanitize_name(part) or "_"
            for part in (result.ref.section, result.ref.activity, result.ref.name)
        )

    def record(self, result: FetchResult) -> str:
        key = self.key(result)
        entry = result.to_dict()
        entry["key"] = key
        self.entries[key] = entry
        return key

    def save(self) -> Path:
        payload = {
            "version": MANIFEST_VERSION,
            "course_id": self.course_id,
            "course_title": self.course_title,
            "generated_by": "lmc-downloader",
            "items": sorted(self.entries.values(), key=lambda e: e.get("key", "")),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.part")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)
        return self.path

    def find_unchanged(self, result: FetchResult) -> Optional[Dict[str, object]]:
        """Return the previous entry when both the disk copy and the remote agree.

        Two independent checks, because either alone gives wrong answers:

        * the file on disk must still match the recorded size and SHA-256, so an
          edit or truncation is noticed;
        * when the crawler learnt the remote size, it must match the recorded
          size too — otherwise an updated upload would be skipped forever, which
          is the whole point of incremental archiving.
        """
        entry = self.entries.get(self.key(result))
        if not entry:
            return None
        path = Path(entry.get("path") or "")
        if not path.is_file():
            return None
        recorded_hash = entry.get("sha256")
        recorded_size = entry.get("bytes")
        if not recorded_hash or recorded_size is None:
            return None
        if path.stat().st_size != recorded_size:
            return None
        remote_size = result.ref.size
        if remote_size is not None and remote_size != recorded_size:
            LOGGER.info("remote size changed for %s (%s -> %s); re-downloading",
                        result.ref.name, recorded_size, remote_size)
            return None
        if sha256_file(path) != recorded_hash:
            return None
        return entry


class Fetcher:
    """Turn :class:`FileRef` objects into files on disk."""

    def __init__(
        self,
        client: HttpClient,
        root: Path,
        dedupe: bool = True,
        overwrite: bool = False,
        dry_run: bool = False,
    ) -> None:
        self.client = client
        self.root = Path(root)
        self.dedupe = dedupe
        self.overwrite = overwrite
        self.dry_run = dry_run
        # Load whatever a previous run left behind, otherwise "incremental"
        # is a lie: a fresh Fetcher would re-download everything.
        self.manifest = Manifest.load(self.root)
        self._hashes: Dict[str, str] = {
            entry["sha256"]: entry["path"]
            for entry in self.manifest.entries.values()
            if entry.get("sha256") and entry.get("path")
        }

    # -- helpers ------------------------------------------------------------
    def target_dir(self, ref: FileRef) -> Path:
        parts = [sanitize_name(p) for p in (ref.section, ref.activity) if p]
        parts = [p for p in parts if p]
        return self.root.joinpath(*parts) if parts else self.root

    def _existing_by_hash(self, digest: str) -> Optional[str]:
        return self._hashes.get(digest)

    # -- main ---------------------------------------------------------------
    def fetch(self, ref: FileRef) -> FetchResult:
        result = FetchResult(ref=ref)
        previous = self.manifest.find_unchanged(result)
        if previous:
            result.path = previous.get("path", "")
            result.sha256 = previous.get("sha256", "")
            result.bytes_written = int(previous.get("bytes") or 0)
            result.status = "skipped"
            result.message = "unchanged since last run"
            return result

        if ref.kind == "link" or not ref.real_url:
            result.status = "link" if ref.real_url else "failed"
            result.message = ref.error or "recorded without downloading"
            if ref.kind == "folder" and ref.error:
                result.status = "failed"
            return result

        name = choose_filename(
            None, ref.real_url, ref.name, ref.content_type
        )
        directory = self.target_dir(ref)
        destination = directory / name

        if self.dry_run:
            result.path = str(destination)
            result.status = "skipped"
            result.message = "dry run"
            return result

        try:
            response = self.client.download(ref.real_url, destination)
        except SessionExpired:
            raise
        except HttpError as exc:
            result.status = "failed"
            result.message = str(exc)
            return result

        # Moodle names the file in Content-Disposition on the final response.
        real_name = choose_filename(
            response.content_disposition, response.url, ref.name, response.content_type
        )
        if real_name and real_name != destination.name:
            renamed = unique_path(directory, real_name)
            destination.replace(renamed)
            destination = renamed

        result.path = str(destination)
        result.bytes_written = destination.stat().st_size
        result.sha256 = sha256_file(destination)

        if self.dedupe:
            original = self._existing_by_hash(result.sha256)
            if original and Path(original) != destination:
                destination.unlink(missing_ok=True)
                result.status = "duplicate"
                result.duplicate_of = original
                result.path = original
                self.manifest.record(result)
                return result

        self._hashes[result.sha256] = str(destination)
        result.status = "downloaded"
        self.manifest.record(result)
        return result

    def adopt_existing(self, ref: FileRef) -> Optional[FetchResult]:
        """Record a file that already exists on disk but is missing from the manifest."""
        name = choose_filename(None, ref.real_url, ref.name, ref.content_type)
        candidate = self.target_dir(ref) / name
        if not candidate.is_file():
            return None
        result = FetchResult(
            ref=ref,
            path=str(candidate),
            sha256=sha256_file(candidate),
            bytes_written=candidate.stat().st_size,
            status="adopted",
            message="already present on disk, not re-downloaded",
        )
        self._hashes[result.sha256] = str(candidate)
        self.manifest.record(result)
        return result

    def summary(self, results: Iterable[FetchResult]) -> str:
        counts: Dict[str, int] = {}
        total = 0
        for result in results:
            counts[result.status] = counts.get(result.status, 0) + 1
            total += result.bytes_written
        parts = ["%s=%d" % (k, v) for k, v in sorted(counts.items())]
        return "%s; %s on disk" % (", ".join(parts), human_bytes(total))
