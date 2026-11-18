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

from postbox.models import CapturedRequest

FORMAT = "postbox-inbox"
VERSION = 1

# Migrations take the record lines of an older version and return record lines
# understood by VERSION. They never rewrite the file on disk.
MIGRATIONS: dict[int, Callable[[list[str]], list[str]]] = {
    0: lambda records: records,  # headerless files from before versioning
}


class UnsupportedFormatError(ValueError):
    """The inbox uses a format or version this loader cannot read."""


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

    def _read_lines(self) -> list[str]:
        """Read and validate the file, returning its record lines."""
        if not self.path.exists():
            return []
        lines = [line for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            return []

        first = json.loads(lines[0])
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
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def append(self, request: CapturedRequest) -> None:
        """Append one request, fsync it, and enforce retention if bounded."""
        line = json.dumps(request.to_dict(), ensure_ascii=False) + "\n"
        with self._lock:
            self._ensure_file()
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            if self.max_requests is not None:
                records = self._read_lines()
                if len(records) > self.max_requests:
                    kept = [CapturedRequest.from_dict(json.loads(item)) for item in records]
                    self._atomic_write(kept[-self.max_requests :])

    def all(self) -> list[CapturedRequest]:
        """Return every captured request in arrival order."""
        with self._lock:
            lines = self._read_lines()
        return [CapturedRequest.from_dict(json.loads(line)) for line in lines]

    def find(self, prefix: str) -> CapturedRequest | None:
        """Find a request whose id starts with ``prefix``."""
        for request in self.all():
            if request.id.startswith(prefix):
                return request
        return None

    def clear(self) -> None:
        """Empty the inbox with a temp-file + os.replace atomic write."""
        with self._lock:
            self._atomic_write([])

    def count(self) -> int:
        """Return the number of captured requests."""
        return len(self.all())
