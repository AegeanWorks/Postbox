"""Append-only JSONL persistence with atomic writes and a format version.

Writes are serialized with a lock because the HTTP server is threaded, and
each append is flushed and fsynced so a kill does not lose a captured request.

The file begins with a versioned header line. A loader refuses a version it
does not support and never modifies the file; older versions migrate in memory
only when a migration is registered (project guideline G-001).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Callable

from postbox.models import CapturedRequest, CorruptInboxError

FORMAT = "postbox-inbox"
VERSION = 1


def _fsync_dir(path: Path) -> None:
    """Durably commit a directory entry where the platform allows it.

    Windows refuses ``os.open`` on a directory, so a failure here is not an
    error: the file itself is already fsynced and ``os.replace`` is atomic.
    """
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    # hygiene: allow directory fsync is unavailable on Windows; the file is already fsynced
    except OSError:
        pass
    finally:
        os.close(descriptor)

# Migrations take the record lines of an older version and return record lines
# understood by VERSION. They never rewrite the file on disk.
MIGRATIONS: dict[int, Callable[[list[str]], list[str]]] = {
    0: lambda records: records,  # headerless files from before versioning
}


class UnsupportedFormatError(ValueError):
    """The inbox uses a format or version this loader cannot read."""


class AmbiguousIdError(LookupError):
    """A short id prefix matches more than one captured request."""

    def __init__(self, prefix: str, matches: list[str]) -> None:
        self.prefix = prefix
        self.matches = matches
        super().__init__(
            f"prefix '{prefix}' matches {len(matches)} ids: {', '.join(matches)}"
        )


class InboxStore:
    """A thread-safe, append-only, versioned store of captured requests."""

    def __init__(self, path: str | Path, max_requests: int | None = None) -> None:
        self.path = Path(path)
        if max_requests is not None and max_requests < 1:
            raise ValueError("max_requests must be at least 1 or None")
        self.max_requests = max_requests
        self._lock = threading.Lock()

    @staticmethod
    def _header_line() -> str:
        return json.dumps({"format": FORMAT, "version": VERSION}) + "\n"

    def _ensure_file(self) -> None:
        """Create the file with its header when it is absent or empty."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() or self.path.stat().st_size == 0:
            with self.path.open("w", encoding="utf-8") as handle:
                handle.write(self._header_line())
                handle.flush()
                os.fsync(handle.fileno())
            _fsync_dir(self.path.parent)

    def _read_lines(self) -> list[str]:
        """Read and validate the file, returning its record lines."""
        if not self.path.exists():
            return []
        lines = [line for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            return []

        try:
            first = json.loads(lines[0])
        except json.JSONDecodeError as exc:
            raise CorruptInboxError("inbox header is not valid JSON") from exc
        if not isinstance(first, dict):
            raise CorruptInboxError("inbox header is not a JSON object")
        if "format" not in first:
            migrate = MIGRATIONS.get(0)
            return migrate(lines) if migrate is not None else []

        if first["format"] != FORMAT:
            raise UnsupportedFormatError(
                f"inbox format '{first['format']}' is not '{FORMAT}'"
            )
        version = first.get("version")
        if version == VERSION:
            return lines[1:]
        migrate = MIGRATIONS.get(version)
        if migrate is None:
            raise UnsupportedFormatError(
                f"inbox version {version!r} is unsupported (this loader reads {VERSION}); "
                "the file was left untouched"
            )
        return migrate(lines[1:])

    @staticmethod
    def _decode(lines: list[str]) -> list[CapturedRequest]:
        """Decode record lines, converting any parse failure to one error type."""
        requests: list[CapturedRequest] = []
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CorruptInboxError("inbox record is not valid JSON") from exc
            requests.append(CapturedRequest.from_dict(record))
        return requests

    def _atomic_write(self, requests: list[CapturedRequest]) -> None:
        """Replace the file with a fresh header and exactly ``requests``."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(self.path.parent),
            prefix=".postbox-",
            delete=False,
        )
        temp_path: Path | None = Path(handle.name)
        try:
            with handle:
                handle.write(self._header_line())
                for request in requests:
                    handle.write(json.dumps(request.to_dict(), ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
            temp_path = None
            _fsync_dir(self.path.parent)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def append(self, request: CapturedRequest) -> None:
        """Append one request, fsync it, and enforce retention if bounded."""
        line = json.dumps(request.to_dict(), ensure_ascii=False) + "\n"
        with self._lock:
            self._ensure_file()
            # Parse before writing: a trim that cannot read the old file must
            # not leave an orphan line behind.
            records = self._decode(self._read_lines()) if self.max_requests else []
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            if self.max_requests is not None:
                kept = records + [request]
                if len(kept) > self.max_requests:
                    self._atomic_write(kept[-self.max_requests :])

    def extend(self, requests: list[CapturedRequest]) -> None:
        """Append a batch in one atomic write, honouring retention."""
        with self._lock:
            self._ensure_file()
            existing = self._decode(self._read_lines()) if self.max_requests else []
            combined = existing + list(requests)
            if self.max_requests is not None and len(combined) > self.max_requests:
                combined = combined[-self.max_requests :]
            self._atomic_write(combined)

    def all(self) -> list[CapturedRequest]:
        """Return every captured request in arrival order."""
        with self._lock:
            lines = self._read_lines()
            return self._decode(lines)

    def find(self, prefix: str) -> CapturedRequest | None:
        """Find a request by id prefix, refusing an ambiguous match."""
        matches = [request for request in self.all() if request.id.startswith(prefix)]
        if len(matches) > 1:
            raise AmbiguousIdError(prefix, [request.id for request in matches])
        return matches[0] if matches else None

    def clear(self) -> None:
        """Empty the inbox with a temp-file + os.replace atomic write."""
        with self._lock:
            self._atomic_write([])

    def count(self) -> int:
        """Return the number of captured requests."""
        return len(self.all())
