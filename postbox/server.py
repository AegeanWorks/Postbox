"""A small threaded HTTP server that records every incoming request."""

from __future__ import annotations

import json
import socket
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from postbox.models import (
    CapturedRequest,
    CorruptInboxError,
    redact_headers,
    redact_query,
    utcnow,
)
from postbox.store import InboxStore, UnsupportedFormatError

DEFAULT_MAX_BODY = 1_048_576  # 1 MiB
_MAX_CHUNK_BYTES = 64 * 1024 * 1024  # hard cap even when no body limit is set
_INBOX_ERRORS = (
    CorruptInboxError,
    UnsupportedFormatError,
    OSError,
    UnicodeDecodeError,
)


class _BadChunkedBody(ValueError):
    """The chunked stream could not be decoded safely."""


class _OversizeChunkedBody(ValueError):
    """The decoded chunked body exceeds the configured limit."""


class InboxHandler(BaseHTTPRequestHandler):
    """Handler that captures requests and exposes a small inspection API."""

    server_version = "Postbox/0.1"

    @property
    def store(self) -> InboxStore:
        return self.server.store  # type: ignore[attr-defined]

    @property
    def max_body(self) -> int:
        return self.server.max_body  # type: ignore[attr-defined]

    def _send_json(
        self,
        payload: object,
        status: int = 200,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            # hygiene: allow a client may disconnect before reading; that is not a server error
            except OSError:
                pass

    def _inbox_error(self, exc: Exception) -> None:
        """Report a store failure as JSON instead of dropping the connection."""
        self._send_json({"error": f"inbox error: {exc}"}, 500)

    def _bad_request(self, reason: str) -> None:
        self._send_json({"error": reason}, 400)

    def _read_full_line(self, limit: int = 65536) -> bytes:
        """Read a whole CRLF line, consuming long chunk extensions fully."""
        parts: list[bytes] = []
        total = 0
        while True:
            part = self.rfile.readline(1024)
            if not part:
                break
            parts.append(part)
            total += len(part)
            if part.endswith(b"\n"):
                break
            if total > limit:
                raise _BadChunkedBody("line too long")
        return b"".join(parts).strip()

    def _read_chunked_body(self, limit: int | None = None) -> bytes:
        """Decode a chunked body with bounded reads and an optional size cap."""
        chunks: list[bytes] = []
        total = 0
        while True:
            line = self._read_full_line()
            if not line:
                raise _BadChunkedBody("empty chunk size")
            try:
                size = int(line.split(b";", 1)[0], 16)
            except ValueError as exc:
                raise _BadChunkedBody("invalid chunk size") from exc
            if size < 0:
                raise _BadChunkedBody("negative chunk size")
            if size > _MAX_CHUNK_BYTES:
                raise _BadChunkedBody("chunk size too large")
            if size == 0:
                while True:
                    trailer = self._read_full_line()
                    if not trailer:
                        break
                break
            total += size
            if limit is not None and total > limit:
                raise _OversizeChunkedBody("chunked body exceeds limit")
            data = self.rfile.read(size)
            if len(data) != size:
                raise _BadChunkedBody("truncated chunk")
            chunks.append(data)
            if self.rfile.read(2) != b"\r\n":
                raise _BadChunkedBody("missing chunk delimiter")
        return b"".join(chunks)

    def _bad_framing(self) -> bool:
        """Reject conflicting framing headers before reading any body (RFC 7230 §3.3.3)."""
        if self.headers.get("Transfer-Encoding") and self.headers.get("Content-Length"):
            self._bad_request("both Transfer-Encoding and Content-Length present")
            return True
        declared = self.headers.get_all("Content-Length")
        if declared is not None and len(declared) > 1:
            self._bad_request("conflicting Content-Length headers")
            return True
        return False

    def _capture(self) -> None:
        if self._bad_framing():
            return
        declared = self.headers.get_all("Content-Length")
        if declared is not None and len(declared) > 1:
            self._bad_request("conflicting Content-Length headers")
            return
        raw_length = declared[0] if declared else None
        try:
            length = int(raw_length) if raw_length is not None else 0
        except (TypeError, ValueError):
            self._bad_request("invalid Content-Length")
            return
        if length < 0:
            self._bad_request("negative Content-Length")
            return
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            try:
                body = self._read_chunked_body(self.max_body)
            except _BadChunkedBody as exc:
                self._bad_request(str(exc))
                return
            except _OversizeChunkedBody:
                self._send_json(
                    {"error": "payload too large", "max_bytes": self.max_body}, 413
                )
                return
        elif length > self.max_body:
            # Drain a bounded amount so the client finishes its write before
            # we answer; closing early aborts the client's send (WinError 10053).
            self.rfile.read(min(length, 16 * 1024 * 1024))
            self._send_json(
                {"error": "payload too large", "max_bytes": self.max_body}, 413
            )
            return
        else:
            body = self.rfile.read(length) if length > 0 else b""
            if len(body) != length:
                self._bad_request("incomplete request body")
                return
        path, _, query = self.path.partition("?")
        request = CapturedRequest(
            id=uuid.uuid4().hex[:12],
            method=self.command,
            path=path,
            query=redact_query(query),
            headers=redact_headers(tuple(self.headers.items())),
            body=body,
            received_at=utcnow(),
        )
        try:
            self.store.append(request)
        except _INBOX_ERRORS as exc:
            self._inbox_error(exc)
            return
        self._send_json({"ok": True, "id": request.id})

    def _drain_body(self) -> bool:
        """Consume and validate a request body; return False if a 400 was sent."""
        if self._bad_framing():
            return False
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            try:
                self._read_chunked_body()  # no storage cap: clear only drains
            except _BadChunkedBody as exc:
                self._bad_request(str(exc))
                return False
            return True

        declared = self.headers.get_all("Content-Length")
        if declared is not None and len(declared) > 1:
            self._bad_request("conflicting Content-Length headers")
            return False
        raw_length = declared[0] if declared else None
        try:
            length = int(raw_length) if raw_length is not None else 0
        except (TypeError, ValueError):
            self._bad_request("invalid Content-Length")
            return False
        if length < 0:
            self._bad_request("negative Content-Length")
            return False
        # Drain in bounded pieces to avoid an early close aborting the client.
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 64 * 1024))
            if not chunk:
                break
            remaining -= len(chunk)
        if remaining:
            self._bad_request("incomplete request body")
            return False
        return True

    def _clear(self) -> None:
        # A destructive endpoint must apply the same framing checks as capture.
        if not self._drain_body():
            return
        try:
            self.store.clear()
        except _INBOX_ERRORS as exc:
            self._inbox_error(exc)
            return
        self._send_json({"ok": True, "cleared": True})

    def _paged_inbox(self, query: str) -> None:
        params = urllib.parse.parse_qs(query, keep_blank_values=True)
        try:
            limit = int(params.get("limit", ["100"])[0])
            offset = int(params.get("offset", ["0"])[0])
            if limit < 1 or offset < 0:
                raise ValueError("out of range")
        except ValueError:
            self._bad_request("limit must be positive and offset non-negative")
            return
        requests = self.store.all()
        page = requests[offset : offset + limit]
        following = offset + len(page)
        self._send_json(
            {
                "total": len(requests),
                "offset": offset,
                "limit": limit,
                "next_offset": following if following < len(requests) else None,
                "requests": [request.to_dict() for request in page],
            }
        )

    def _route(self) -> None:
        path, _, query = self.path.partition("?")
        if path in {"/__inbox", "/__health"} and self.command not in {"GET", "HEAD"}:
            self._capture()
            return
        if path == "/__clear":
            if self.command not in {"POST", "DELETE"}:
                self._send_json(
                    {"error": "use POST or DELETE to clear"},
                    405,
                    {"Allow": "POST, DELETE"},
                )
            else:
                self._clear()
        elif path == "/__inbox":
            try:
                params = urllib.parse.parse_qs(query, keep_blank_values=True)
                if "limit" in params or "offset" in params:
                    self._paged_inbox(query)
                else:
                    self._send_json([item.to_dict() for item in self.store.all()])
            except _INBOX_ERRORS as exc:
                self._inbox_error(exc)
        elif path == "/__health":
            try:
                self._send_json({"ok": True, "count": self.store.count()})
            except _INBOX_ERRORS as exc:
                self._inbox_error(exc)
        else:
            self._capture()

    def do_GET(self) -> None:
        self._route()

    def do_POST(self) -> None:
        self._route()

    def do_PUT(self) -> None:
        self._route()

    def do_PATCH(self) -> None:
        self._route()

    def do_DELETE(self) -> None:
        self._route()

    def do_HEAD(self) -> None:
        self._route()

    def do_OPTIONS(self) -> None:
        self._route()

    def do_CONNECT(self) -> None:
        self._route()

    def do_TRACE(self) -> None:
        self._route()

    def log_message(self, format: str, *args: object) -> None:
        """Silence default stderr logging; the inbox is the record."""
        return


class InboxServer(ThreadingHTTPServer):
    """Threaded HTTP server bound to an :class:`InboxStore`."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        store: InboxStore,
        max_body: int = DEFAULT_MAX_BODY,
    ) -> None:
        self.store = store
        self.max_body = max_body
        self.address_family = (
            socket.AF_INET6 if ":" in address[0] else socket.AF_INET
        )
        super().__init__(address, InboxHandler)

    def url(self) -> str:
        """Return the bound URL, including an OS-assigned port when requested."""
        host, port = self.server_address[:2]
        shown = f"[{host}]" if ":" in host else host
        return f"http://{shown}:{port}"


def serve(
    port: int,
    store: InboxStore,
    host: str = "127.0.0.1",
    max_body: int = DEFAULT_MAX_BODY,
    quiet: bool = False,
) -> None:
    """Run the inbox until interrupted; ``quiet`` keeps stdout free for JSON."""
    server = InboxServer((host, port), store, max_body)
    if not quiet:
        print(f"Postbox listening on {server.url()} (inbox: {store.path})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if not quiet:
            print("\nPostbox stopped.")
    finally:
        server.server_close()
