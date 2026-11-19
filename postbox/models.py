"""The captured-request value object."""

from __future__ import annotations

import base64
import binascii
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone

REDACTED = "[REDACTED]"

SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
    }
)

SENSITIVE_QUERY_KEYS = frozenset(
    {"token", "api_key", "apikey", "secret", "password", "key"}
)


def redact_headers(
    headers: tuple[tuple[str, str], ...] | list[tuple[str, str]]
) -> tuple[tuple[str, str], ...]:
    """Replace credential-bearing header values with a fixed marker."""
    return tuple(
        (name, REDACTED if name.lower() in SENSITIVE_HEADERS else value)
        for name, value in headers
    )


def redact_query(query: str) -> str:
    """Replace credential-bearing query values while preserving other keys."""
    if not query:
        return ""
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
    return urllib.parse.urlencode(
        [(name, REDACTED if name.lower() in SENSITIVE_QUERY_KEYS else value)
         for name, value in pairs]
    )


class CorruptInboxError(ValueError):
    """A record in the inbox cannot be decoded safely."""


_REQUIRED_FIELDS = frozenset(
    {"id", "method", "path", "headers", "body_b64", "received_at"}
)


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

    def __post_init__(self) -> None:
        try:
            datetime.fromisoformat(self.received_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError) as exc:
            raise CorruptInboxError(
                "received_at must be an ISO-8601 timestamp"
            ) from exc

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
        """Rebuild a request from its JSON-safe representation.

        A malformed record raises :class:`CorruptInboxError` rather than
        leaking `KeyError`, `TypeError` or a silently mangled binary body.
        """
        if not isinstance(data, dict):
            raise CorruptInboxError("record is not a JSON object")
        missing = _REQUIRED_FIELDS - data.keys()
        if missing:
            raise CorruptInboxError(f"record missing field(s): {', '.join(sorted(missing))}")

        for field in ("id", "method", "path", "received_at"):
            if not isinstance(data[field], str):
                raise CorruptInboxError(f"field '{field}' must be a string")
        query = data.get("query", "")
        if not isinstance(query, str):
            raise CorruptInboxError("field 'query' must be a string")

        raw_headers = data["headers"]
        if not isinstance(raw_headers, (list, tuple)):
            raise CorruptInboxError("field 'headers' must be a list of pairs")
        headers: list[tuple[str, str]] = []
        for pair in raw_headers:
            if (
                not isinstance(pair, (list, tuple))
                or len(pair) != 2
                or not all(isinstance(value, str) for value in pair)
            ):
                raise CorruptInboxError("field 'headers' must be a list of string pairs")
            headers.append((pair[0], pair[1]))

        try:
            body = base64.b64decode(data["body_b64"], validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise CorruptInboxError("field 'body_b64' is not valid base64") from exc

        return cls(
            id=data["id"],
            method=data["method"],
            path=data["path"],
            query=query,
            headers=tuple(headers),
            body=body,
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
