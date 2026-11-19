"""A small threaded HTTP server that records every incoming request."""

from __future__ import annotations

import json
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
_INBOX_ERRORS = (CorruptInboxError, UnsupportedFormatError, OSError)


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

    def _send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _inbox_error(self, exc: Exception) -> None:
        """Report a store failure as JSON instead of dropping the connection."""
        self._send_json({"error": f"inbox error: {exc}"}, 500)

    def _bad_request(self, reason: str) -> None:
        self._send_json({"error": reason}, 400)

    def _read_chunked_body(self) -> bytes:
        """Decode a chunked body with bounded reads and a cumulative size cap."""
        chunks: list[bytes] = []
        total = 0
        while True:
            line = self.rfile.readline(128).strip()
            if not line:
                raise _BadChunkedBody("empty chunk size")
            try:
                size = int(line.split(b";", 1)[0], 16)
            except ValueError as exc:
                raise _BadChunkedBody("invalid chunk size") from exc
            if size < 0:
                raise _BadChunkedBody("negative chunk size")
            if size == 0:
                while True:
                    trailer = self.rfile.readline(65536)
                    if trailer in (b"\r\n", b"\n", b""):
                        break
                break
            total += size
            if total > self.max_body:
                raise _OversizeChunkedBody("chunked body exceeds max_body")
            data = self.rfile.read(size)
            if len(data) != size:
                raise _BadChunkedBody("truncated chunk")
            chunks.append(data)
            if self.rfile.read(2) != b"\r\n":
                raise _BadChunkedBody("missing chunk delimiter")
        return b"".join(chunks)

    def _capture(self) -> None:
        raw_length = self.headers.get("Content-Length")
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
                body = self._read_chunked_body()
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

    def _clear(self) -> None:
        try:
            self.store.clear()
        except _INBOX_ERRORS as exc:
            self._inbox_error(exc)
            return
        self._send_json({"ok": True, "cleared": True})

    def _paged_inbox(self, query: str) -> None:
        params = urllib.parse.parse_qs(query)
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
        if path == "/__clear":
            if self.command == "GET":
                self._send_json({"error": "use POST or DELETE to clear"}, 405)
            else:
                self._clear()
        elif path == "/__inbox":
            try:
                if "limit" in query or "offset" in query:
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
        super().__init__(address, InboxHandler)


def serve(
    port: int,
    store: InboxStore,
    host: str = "127.0.0.1",
    max_body: int = DEFAULT_MAX_BODY,
) -> None:
    """Run the inbox until interrupted."""
    server = InboxServer((host, port), store, max_body)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPostbox stopped.")
    finally:
        server.server_close()
