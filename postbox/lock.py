"""Cross-platform single-writer lock for a Postbox inbox file.

The OS releases the lock when the process exits, so a killed server cannot
leave a stale lock behind. The lock guards the ``serve`` command only; read
commands remain available while a server is running.
"""

from __future__ import annotations

import os
from pathlib import Path

if os.name == "nt":  # pragma: no cover - platform specific
    import msvcrt
else:  # pragma: no cover - platform specific
    import fcntl


class SingleWriterLock:
    """An advisory exclusive lock on ``<inbox>.lock`` for a process lifetime."""

    def __init__(self, inbox_path: str | Path) -> None:
        self.path = Path(str(inbox_path) + ".lock")
        self._descriptor: int | None = None

    def acquire(self) -> bool:
        """Take the lock, returning False when another writer already holds it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            str(self.path), os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o644
        )
        try:
            if os.name == "nt":  # pragma: no cover - platform specific
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - platform specific
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._descriptor = descriptor
            return True
        except OSError:
            os.close(descriptor)
            return False

    def release(self) -> None:
        """Release the lock and close its descriptor; safe to call twice."""
        if self._descriptor is None:
            return
        try:
            if os.name == "nt":  # pragma: no cover - platform specific
                os.lseek(self._descriptor, 0, os.SEEK_SET)
                msvcrt.locking(self._descriptor, msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - platform specific
                fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        # hygiene: allow unlocking may fail if the OS already released it on exit
        except OSError:
            pass
        finally:
            os.close(self._descriptor)
            self._descriptor = None
