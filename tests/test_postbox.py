"""Tests for Postbox: models, store, server and CLI."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from postbox.cli import main
from postbox.models import CapturedRequest
from postbox.server import InboxServer
from postbox.store import InboxStore, UnsupportedFormatError


def make_request(
    method: str = "POST",
    path: str = "/hook",
    body: bytes = b"",
    id: str = "abc123",
) -> CapturedRequest:
    return CapturedRequest(
        id=id,
        method=method,
        path=path,
        headers=(("Content-Type", "application/json"),),
        body=body,
        received_at="2026-11-19T00:00:00+00:00",
    )


class ModelTests(unittest.TestCase):
    def test_json_roundtrip_preserves_body(self) -> None:
        original = make_request(body=b'{"x": 1}')
        restored = CapturedRequest.from_dict(original.to_dict())
        self.assertEqual(restored, original)

    def test_text_decodes_body(self) -> None:
        self.assertEqual(make_request(body=b"hello").text(), "hello")

    def test_header_lookup_is_case_insensitive(self) -> None:
        self.assertEqual(make_request().header("content-type"), "application/json")
        self.assertIsNone(make_request().header("missing"))


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "inbox.jsonl"
        self.store = InboxStore(self.path)

    def test_append_and_list(self) -> None:
        self.store.append(make_request())
        self.store.append(make_request(id="def456", method="GET"))
        self.assertEqual([r.id for r in self.store.all()], ["abc123", "def456"])

    def test_file_starts_with_versioned_header(self) -> None:
        self.store.append(make_request())
        first = json.loads(self.path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(first["format"], "postbox-inbox")
        self.assertEqual(first["version"], 1)

    def test_unknown_version_is_refused_without_modifying_file(self) -> None:
        record = make_request().to_dict()
        self.path.write_text(
            json.dumps({"format": "postbox-inbox", "version": 99}) + "\n"
            + json.dumps(record) + "\n",
            encoding="utf-8",
        )
        before = self.path.read_bytes()
        with self.assertRaises(UnsupportedFormatError):
            self.store.all()
        self.assertEqual(self.path.read_bytes(), before)

    def test_headerless_file_is_migrated_in_memory(self) -> None:
        self.path.write_text(json.dumps(make_request().to_dict()) + "\n", encoding="utf-8")
        self.assertEqual([r.id for r in self.store.all()], ["abc123"])

    def test_find_by_prefix(self) -> None:
        self.store.append(make_request())
        self.assertEqual(self.store.find("abc").id, "abc123")
        self.assertIsNone(self.store.find("zzz"))

    def test_retention_keeps_newest_requests(self) -> None:
        store = InboxStore(self.path, max_requests=2)
        for index in range(3):
            store.append(make_request(id=f"id{index}"))
        self.assertEqual([r.id for r in store.all()], ["id1", "id2"])

    def test_clear_empties_without_temp_files(self) -> None:
        self.store.append(make_request())
        self.store.clear()
        self.assertEqual(self.store.count(), 0)
        self.assertEqual(list(self.path.parent.glob(".postbox-*")), [])

    def test_missing_file_is_empty(self) -> None:
        self.assertEqual(InboxStore(self.path / "nested.jsonl").all(), [])

    def test_concurrent_appends_lose_no_requests(self) -> None:
        threads = [
            threading.Thread(
                target=lambda n=index: self.store.append(make_request(id=f"t{n}"))
            )
            for index in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(self.store.count(), 8)


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = InboxStore(Path(tmp.name) / "inbox.jsonl")
        self.server = InboxServer(("127.0.0.1", 0), self.store, max_body=64)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _post(self, path: str, payload: bytes) -> str:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.read().decode()

    def test_request_is_captured(self) -> None:
        self._post("/hook", b'{"event": "push"}')
        requests = self.store.all()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].method, "POST")
        self.assertEqual(requests[0].text(), '{"event": "push"}')

    def test_inspect_and_clear_endpoints(self) -> None:
        self._post("/hook", b"data")
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/__inbox", timeout=5
        ) as response:
            payload = json.loads(response.read())
        self.assertEqual(len(payload), 1)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/__clear", timeout=5
        ) as response:
            self.assertTrue(json.loads(response.read())["cleared"])
        self.assertEqual(self.store.count(), 0)

    def test_health_endpoint(self) -> None:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/__health", timeout=5
        ) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload, {"ok": True, "count": 0})

    def test_oversized_body_is_rejected(self) -> None:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/hook", data=b"x" * 128, method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        caught.exception.close()
        self.assertEqual(caught.exception.code, 413)
        self.assertEqual(self.store.count(), 0)


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        silence = contextlib.redirect_stdout(io.StringIO())
        silence.__enter__()
        self.addCleanup(silence.__exit__, None, None, None)
        self.path = Path(tmp.name) / "inbox.jsonl"
        self.store = InboxStore(self.path)
        self.store.append(make_request(body=b"payload"))
        self.store.append(make_request(id="def456", method="GET", path="/health"))

    def test_list_and_show(self) -> None:
        self.assertEqual(main(["--file", str(self.path), "list"]), 0)
        self.assertEqual(main(["--file", str(self.path), "show", "abc"]), 0)

    def test_list_filters_by_method_and_path(self) -> None:
        self.assertEqual(
            main(["--file", str(self.path), "list", "--method", "GET"]), 0
        )
        self.assertEqual(
            main(["--file", str(self.path), "list", "--path", "/hook"]), 0
        )

    def test_json_output_is_parseable(self) -> None:
        self.assertEqual(main(["--file", str(self.path), "--json", "list"]), 0)

    def test_show_missing_returns_error(self) -> None:
        self.assertEqual(main(["--file", str(self.path), "show", "nope"]), 1)

    def test_clear(self) -> None:
        self.assertEqual(main(["--file", str(self.path), "clear"]), 0)
        self.assertEqual(self.store.count(), 0)


if __name__ == "__main__":
    unittest.main()
