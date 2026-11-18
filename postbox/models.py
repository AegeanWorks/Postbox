"""The captured-request value object."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone


def utcnow() -> str:
    """Return a stable, timezone-aware ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class CapturedRequest:
    """An immutable record of one HTTP request received by the inbox."""

    id: str
    method: str
    path: str
    headers: tuple[tuple[str, str], ...]
    body: bytes
    received_at: str
    query: str = ""

    def to_dict(self) -> dict:
        """Return a JSON-safe representation (body is base64 encoded)."""
        return {
            "id": self.id,
            "method": self.method,
            "path": self.path,
            "query": self.query,
            "headers": [[key, value] for key, value in self.headers],
            "body_b64": base64.b64encode(self.body).decode("ascii"),
            "received_at": self.received_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CapturedRequest":
        """Rebuild a request from its JSON-safe representation."""
        return cls(
            id=data["id"],
            method=data["method"],
            path=data["path"],
            query=data.get("query", ""),
            headers=tuple((key, value) for key, value in data["headers"]),
            body=base64.b64decode(data["body_b64"]),
            received_at=data["received_at"],
        )

    def text(self) -> str:
        """Return the body decoded as UTF-8, replacing invalid bytes."""
        return self.body.decode("utf-8", errors="replace")

    def header(self, name: str) -> str | None:
        """Return the first header matching ``name`` case-insensitively."""
        lowered = name.lower()
        for key, value in self.headers:
            if key.lower() == lowered:
                return value
        return None
