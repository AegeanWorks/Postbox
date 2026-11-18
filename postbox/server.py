"""A small threaded HTTP server that records every incoming request."""

from __future__ import annotations

import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from postbox.models import CapturedRequest, CorruptInboxError, utcnow
from postbox.store import InboxStore, UnsupportedFormatError

DEFAULT_MAX_BODY = 1_048_576  # 1 MiB
_INBOX_ERRORS = (CorruptInboxError, UnsupportedFormatError, OSError)


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
        self.wfile.write(body)

    def _inbox_error(self, exc: Exception) -> None:
        """Report a store failure as JSON instead of dropping the connection."""
        self.close_connection = True
        self._send_json({"error": f"inbox error: {exc}"}, 500)

    def _bad_request(self, reason: str) -> None:
        self.close_connection = True
        self._send_json({"error": reason}, 400)

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
            self._bad_request("chunked bodies are not supported; send Content-Length")
            return
        if length > self.max_body:
            self.close_connection = True
            self._send_json(
                {"error": "payload too large", "max_bytes": self.max_body}, 413
            )
            return

        body = self.rfile.read(length) if length > 0 else b""
        path, _, query = self.path.partition("?")
        request = CapturedRequest(
            id=uuid.uuid4().hex[:12],
            method=self.command,
            path=path,
            query=query,
            headers=tuple(self.headers.items()),
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

    def _route(self) -> None:
        path = self.path.split("?")[0]
        if path == "/__clear":
            if self.command == "GET":
                self.close_connection = True
                self._send_json({"error": "use POST or DELETE to clear"}, 405)
            else:
                self._clear()
        elif path == "/__inbox":
            try:
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
        self._capture()

    def do_PATCH(self) -> None:
        self._capture()

    def do_DELETE(self) -> None:
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
