"""Tests for Postbox: models, store, server and CLI."""

from __future__ import annotations

import contextlib
import io
import json
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

from postbox.cli import is_loopback, main
from postbox.lock import SingleWriterLock
from postbox.models import (
    CapturedRequest,
    CorruptInboxError,
    redact_headers,
    redact_query,
)
from postbox.server import InboxServer
from postbox.store import AmbiguousIdError, InboxStore, UnsupportedFormatError


def make_request(
    method: str = "POST",
    path: str = "/hook",
    body: bytes = b"",
    id: str = "abc123",
    headers: tuple[tuple[str, str], ...] | None = None,
    query: str = "",
    received_at: str = "2026-11-19T00:00:00+00:00",
) -> CapturedRequest:
    return CapturedRequest(
        id=id,
        method=method,
        path=path,
        headers=headers if headers is not None else (("Content-Type", "application/json"),),
        body=body,
        received_at=received_at,
        query=query,
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

    def test_corrupt_base64_is_refused(self) -> None:
        data = make_request().to_dict()
        data["body_b64"] = "@@@@"
        with self.assertRaises(CorruptInboxError):
            CapturedRequest.from_dict(data)

    def test_invalid_timestamp_is_refused(self) -> None:
        with self.assertRaises(CorruptInboxError):
            CapturedRequest("x", "GET", "/x", (), b"", "not-a-timestamp")

    def test_z_suffix_timestamp_is_accepted(self) -> None:
        record = CapturedRequest("x", "GET", "/x", (), b"", "2026-11-19T10:00:00Z")
        self.assertEqual(record.received_at, "2026-11-19T10:00:00Z")

    def test_missing_record_key_is_refused(self) -> None:
        with self.assertRaises(CorruptInboxError):
            CapturedRequest.from_dict({"id": "x"})

    def test_sensitive_headers_are_redacted(self) -> None:
        result = dict(
            redact_headers(
                (
                    ("Authorization", "Bearer secret"),
                    ("Content-Type", "application/json"),
                    ("Cookie", "session=abc"),
                    ("X-Api-Key", "abc123"),
                )
            )
        )
        self.assertEqual(result["Authorization"], "[REDACTED]")
        self.assertEqual(result["Cookie"], "[REDACTED]")
        self.assertEqual(result["X-Api-Key"], "[REDACTED]")
        self.assertEqual(result["Content-Type"], "application/json")

    def test_oauth_header_names_are_redacted(self) -> None:
        result = dict(
            redact_headers(
                (
                    ("X-Access-Token", "secret"),
                    ("X-CSRF-Token", "secret"),
                    ("Api-Key", "secret"),
                    ("Authentication", "secret"),
                )
            )
        )
        for name in ("X-Access-Token", "X-CSRF-Token", "Api-Key", "Authentication"):
            self.assertEqual(result[name], "[REDACTED]")

    def test_oauth_query_keys_are_redacted(self) -> None:
        result = dict(
            urllib.parse.parse_qsl(
                redact_query("access_token=a&refresh_token=b&auth=c&page=1")
            )
        )
        self.assertEqual(result["access_token"], "[REDACTED]")
        self.assertEqual(result["refresh_token"], "[REDACTED]")
        self.assertEqual(result["auth"], "[REDACTED]")
        self.assertEqual(result["page"], "1")

    def test_sensitive_query_keys_are_redacted(self) -> None:
        result = dict(urllib.parse.parse_qsl(redact_query("a=1&token=s3cr3t&b=2")))
        self.assertEqual(result["token"], "[REDACTED]")
        self.assertEqual(result["a"], "1")
        self.assertEqual(result["b"], "2")


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

    def test_ambiguous_prefix_is_refused(self) -> None:
        self.store.append(make_request(id="abc1"))
        self.store.append(make_request(id="abc2"))
        with self.assertRaises(AmbiguousIdError):
            self.store.find("abc")

    def test_retention_keeps_newest_requests(self) -> None:
        store = InboxStore(self.path, max_requests=2)
        for index in range(3):
            store.append(make_request(id=f"id{index}"))
        self.assertEqual([r.id for r in store.all()], ["id1", "id2"])

    def test_delete_removes_only_the_match(self) -> None:
        self.store.append(make_request(id="a"))
        self.store.append(make_request(id="b"))
        removed = self.store.delete("a")
        self.assertEqual(removed.id, "a")
        self.assertEqual([r.id for r in self.store.all()], ["b"])

    def test_delete_returns_none_when_missing(self) -> None:
        self.assertIsNone(self.store.delete("zzz"))

    def test_delete_refuses_ambiguous_prefix(self) -> None:
        self.store.append(make_request(id="abc1"))
        self.store.append(make_request(id="abc2"))
        with self.assertRaises(AmbiguousIdError):
            self.store.delete("abc")

    def test_extend_preserves_existing_records(self) -> None:
        self.store.append(make_request(id="a"))
        self.store.extend([make_request(id="b")])
        self.assertEqual([r.id for r in self.store.all()], ["a", "b"])

    def test_extend_appends_a_batch(self) -> None:
        self.store.extend([make_request(id="a"), make_request(id="b")])
        self.assertEqual([r.id for r in self.store.all()], ["a", "b"])

    def test_extend_honours_retention(self) -> None:
        store = InboxStore(self.path, max_requests=2)
        store.extend([make_request(id="a"), make_request(id="b"), make_request(id="c")])
        self.assertEqual([r.id for r in store.all()], ["b", "c"])

    def test_clear_empties_without_temp_files(self) -> None:
        self.store.append(make_request())
        self.store.clear()
        self.assertEqual(self.store.count(), 0)
        self.assertEqual(list(self.path.parent.glob(".postbox-*")), [])

    def test_missing_file_is_empty(self) -> None:
        self.assertEqual(InboxStore(self.path / "nested.jsonl").all(), [])

    def test_corrupt_line_is_refused(self) -> None:
        self.path.write_text(
            '{"format":"postbox-inbox","version":1}\nnot-json\n', encoding="utf-8"
        )
        with self.assertRaises(CorruptInboxError):
            self.store.all()

    def test_bad_record_shape_is_refused(self) -> None:
        header = json.dumps({"format": "postbox-inbox", "version": 1}) + "\n"
        self.path.write_text(header + json.dumps({"id": "x"}) + "\n", encoding="utf-8")
        with self.assertRaises(CorruptInboxError):
            self.store.all()

    def test_failed_trim_leaves_no_orphan_line(self) -> None:
        self.path.write_text(
            '{"format":"postbox-inbox","version":1}\nnot-json\n', encoding="utf-8"
        )
        store = InboxStore(self.path, max_requests=1)
        before = self.path.read_bytes()
        with self.assertRaises(CorruptInboxError):
            store.append(make_request())
        self.assertEqual(self.path.read_bytes(), before)

    def test_directory_is_fsynced_on_create_and_clear(self) -> None:
        with mock.patch("postbox.store._fsync_dir") as fsync_dir:
            self.store.append(make_request())
            self.store.clear()
        self.assertGreaterEqual(fsync_dir.call_count, 2)

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


class LockTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        stdout_silence = contextlib.redirect_stdout(io.StringIO())
        stdout_silence.__enter__()
        self.addCleanup(stdout_silence.__exit__, None, None, None)
        stderr_silence = contextlib.redirect_stderr(io.StringIO())
        stderr_silence.__enter__()
        self.addCleanup(stderr_silence.__exit__, None, None, None)
        self.path = Path(tmp.name) / "inbox.jsonl"

    def test_lock_is_exclusive_and_releasable(self) -> None:
        first = SingleWriterLock(self.path)
        second = SingleWriterLock(self.path)
        self.assertTrue(first.acquire())
        self.assertTrue((Path(str(self.path) + ".lock")).exists())
        self.assertFalse(second.acquire())
        first.release()
        self.assertTrue(second.acquire())
        second.release()

    def test_cli_refuses_when_lock_is_held(self) -> None:
        holder = SingleWriterLock(self.path)
        self.assertTrue(holder.acquire())
        try:
            self.assertEqual(main(["--file", str(self.path), "serve"]), 1)
        finally:
            holder.release()

    def test_cli_writes_refused_while_lock_is_held(self) -> None:
        store = InboxStore(self.path)
        store.append(make_request(id="abc"))
        snapshot = Path(self.path).parent / "snapshot.json"
        snapshot.write_text(
            json.dumps({"format": "postbox-export", "version": 1, "requests": []}),
            encoding="utf-8",
        )
        holder = SingleWriterLock(self.path)
        self.assertTrue(holder.acquire())
        try:
            self.assertEqual(main(["--file", str(self.path), "delete", "abc"]), 1)
            self.assertEqual(main(["--file", str(self.path), "clear"]), 1)
            self.assertEqual(
                main(["--file", str(self.path), "import", str(snapshot)]), 1
            )
            self.assertEqual([r.id for r in store.all()], ["abc"])
        finally:
            holder.release()

    def test_read_commands_allowed_while_lock_is_held(self) -> None:
        store = InboxStore(self.path)
        store.append(make_request(id="abc"))
        holder = SingleWriterLock(self.path)
        self.assertTrue(holder.acquire())
        try:
            self.assertEqual(main(["--file", str(self.path), "list"]), 0)
            self.assertEqual(main(["--file", str(self.path), "export"]), 0)
        finally:
            holder.release()


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

    def _raw(self, payload: bytes) -> bytes:
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall(payload)
            chunks: list[bytes] = []
            while True:
                try:
                    part = sock.recv(4096)
                except socket.timeout:
                    break
                if not part:
                    break
                chunks.append(part)
            return b"".join(chunks)

    def _post(self, path: str, payload: bytes) -> str:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.read().decode()

    def test_url_reports_the_bound_port(self) -> None:
        self.assertIn(f":{self.port}", self.server.url())
        self.assertTrue(self.server.url().startswith("http://"))

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
        clear_request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/__clear", data=b"", method="POST"
        )
        with urllib.request.urlopen(clear_request, timeout=5) as response:
            self.assertTrue(json.loads(response.read())["cleared"])
        self.assertEqual(self.store.count(), 0)

    def test_inbox_pagination(self) -> None:
        for _ in range(3):
            self._post("/hook", b"x")
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/__inbox?limit=2&offset=0", timeout=5
        ) as response:
            page = json.loads(response.read())
        self.assertEqual(page["total"], 3)
        self.assertEqual(len(page["requests"]), 2)
        self.assertEqual(page["next_offset"], 2)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/__inbox", timeout=5
        ) as response:
            self.assertIsInstance(json.loads(response.read()), list)

    def test_inbox_pagination_rejects_bad_values(self) -> None:
        response = self._raw(b"GET /__inbox?limit=-1 HTTP/1.0\r\nHost: x\r\n\r\n")
        self.assertIn(b"400", response)

    def test_non_utf8_inbox_returns_json_500(self) -> None:
        self.store.path.write_bytes(
            b'{"format":"postbox-inbox","version":1}\n{"id":"x","body_b64":"\xff"}\n'
        )
        response = self._raw(b"GET /__inbox HTTP/1.0\r\nHost: x\r\n\r\n")
        self.assertIn(b"500", response)
        self.assertIn(b"inbox error", response.lower())

    def test_health_endpoint(self) -> None:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/__health", timeout=5
        ) as response:
            payload = json.loads(response.read())
        self.assertEqual(payload, {"ok": True, "count": 0})

    def test_malformed_content_length_is_rejected(self) -> None:
        response = self._raw(
            b"POST /hook HTTP/1.0\r\nHost: x\r\nContent-Length: abc\r\n\r\n"
        )
        self.assertIn(b"400", response)
        self.assertEqual(self.store.count(), 0)

    def test_negative_content_length_is_rejected(self) -> None:
        response = self._raw(
            b"POST /hook HTTP/1.0\r\nHost: x\r\nContent-Length: -5\r\n\r\n"
        )
        self.assertIn(b"400", response)
        self.assertEqual(self.store.count(), 0)

    def test_chunked_body_is_decoded(self) -> None:
        response = self._raw(
            b"POST /hook HTTP/1.0\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"4\r\nwiki\r\n0\r\n\r\n"
        )
        self.assertIn(b"200", response)
        self.assertEqual(self.store.all()[0].body, b"wiki")

    def test_chunked_oversize_body_is_rejected(self) -> None:
        payload = (
            b"POST /hook HTTP/1.0\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"80\r\n" + b"x" * 128 + b"\r\n0\r\n\r\n"
        )
        response = self._raw(payload)
        self.assertIn(b"413", response)
        self.assertEqual(self.store.count(), 0)

    def test_malformed_chunk_size_is_rejected(self) -> None:
        payload = (
            b"POST /hook HTTP/1.0\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
            b"zz\r\nwiki\r\n0\r\n\r\n"
        )
        response = self._raw(payload)
        self.assertIn(b"400", response)
        self.assertEqual(self.store.count(), 0)

    def test_get_clear_is_a_noop(self) -> None:
        self._post("/hook", b"data")
        response = self._raw(b"GET /__clear HTTP/1.0\r\nHost: x\r\n\r\n")
        self.assertIn(b"405", response)
        self.assertEqual(self.store.count(), 1)
        with urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:{self.port}/__clear", data=b"", method="POST"
            ),
            timeout=5,
        ) as result:
            self.assertTrue(json.loads(result.read())["cleared"])
        self.assertEqual(self.store.count(), 0)

    def test_read_only_methods_on_clear_are_noops(self) -> None:
        for method in (b"HEAD", b"OPTIONS", b"CONNECT", b"TRACE"):
            self._post("/hook", b"data")
            response = self._raw(method + b" /__clear HTTP/1.0\r\nHost: x\r\n\r\n")
            self.assertIn(b"405", response, method.decode())
            self.assertEqual(self.store.count(), 1, method.decode())
            self.store.clear()

    def test_truncated_body_is_rejected(self) -> None:
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall(
                b"POST /hook HTTP/1.0\r\nHost: x\r\nContent-Length: 50\r\n\r\nabc"
            )
            sock.shutdown(socket.SHUT_WR)
            self.assertIn(b"400", sock.recv(4096))
        self.assertEqual(self.store.count(), 0)

    def test_clear_with_body_does_not_abort_client(self) -> None:
        with socket.create_connection(("127.0.0.1", self.port), timeout=15) as sock:
            sock.settimeout(15)
            sock.sendall(
                b"POST /__clear HTTP/1.0\r\nHost: x\r\nContent-Length: 2097152\r\n\r\n"
            )
            sock.sendall(b"Z" * (2 * 1024 * 1024))
            self.assertIn(b"200", sock.recv(4096))

    def test_pagination_detected_by_query_key_not_substring(self) -> None:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/__inbox?filter=limit", timeout=5
        ) as response:
            self.assertIsInstance(json.loads(response.read()), list)

    def test_corrupt_inbox_returns_json_error(self) -> None:
        self.store.path.write_text(
            '{"format":"postbox-inbox","version":1}\nnot-json\n', encoding="utf-8"
        )
        response = self._raw(b"GET /__inbox HTTP/1.0\r\nHost: x\r\n\r\n")
        self.assertIn(b"500", response)
        self.assertIn(b"inbox error", response.lower())

    def test_head_and_options_are_captured(self) -> None:
        head = self._raw(b"HEAD /hook HTTP/1.0\r\nHost: x\r\n\r\n")
        self.assertIn(b"200", head)
        self.assertTrue(head.endswith(b"\r\n\r\n"))
        options = self._raw(b"OPTIONS /hook HTTP/1.0\r\nHost: x\r\n\r\n")
        self.assertIn(b"200", options)
        self.assertEqual(
            [(r.method, r.path) for r in self.store.all()],
            [("HEAD", "/hook"), ("OPTIONS", "/hook")],
        )

    def test_secrets_are_redacted_on_capture(self) -> None:
        response = self._raw(
            b"POST /hook?token=s3cr3t HTTP/1.0\r\nHost: x\r\n"
            b"Authorization: Bearer abc\r\nContent-Length: 2\r\n\r\nhi"
        )
        self.assertIn(b"200", response)
        record = self.store.all()[0]
        self.assertEqual(record.header("Authorization"), "[REDACTED]")
        self.assertEqual(dict(urllib.parse.parse_qsl(record.query))["token"], "[REDACTED]")

    def test_oversized_body_is_rejected(self) -> None:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/hook", data=b"x" * 128, method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        caught.exception.close()
        self.assertEqual(caught.exception.code, 413)
        self.assertEqual(self.store.count(), 0)


class ResponseWriteTests(unittest.TestCase):
    def test_send_json_tolerates_client_disconnect(self) -> None:
        from postbox.server import InboxHandler

        handler = object.__new__(InboxHandler)
        handler.command = "GET"
        handler.wfile = mock.Mock()
        handler.wfile.write.side_effect = OSError("client gone")
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        handler._send_json({"ok": True})


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self._stdout = io.StringIO()
        silence = contextlib.redirect_stdout(self._stdout)
        silence.__enter__()
        self.addCleanup(silence.__exit__, None, None, None)
        silence_error = contextlib.redirect_stderr(io.StringIO())
        silence_error.__enter__()
        self.addCleanup(silence_error.__exit__, None, None, None)
        self.path = Path(tmp.name) / "inbox.jsonl"
        self.store = InboxStore(self.path)
        self.store.append(make_request(body=b"payload"))
        self.store.append(make_request(id="def456", method="GET", path="/health"))
        self.store.append(make_request(id="ghi789", method="DELETE", path="/item", body=b"bye"))

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

    def test_global_flags_after_subcommand(self) -> None:
        self.assertEqual(main(["--file", str(self.path), "list", "--json"]), 0)
        self.assertEqual(len(json.loads(self._stdout.getvalue())), 3)

    def test_global_file_flag_after_subcommand(self) -> None:
        other = Path(self.path).parent / "other.jsonl"
        InboxStore(other).append(make_request(id="other"))
        main(["list", "--file", str(other), "--json"])
        self.assertEqual(json.loads(self._stdout.getvalue())[0]["id"], "other")

    def test_json_output_is_parseable(self) -> None:
        self.assertEqual(main(["--file", str(self.path), "--json", "list"]), 0)

    def test_show_missing_returns_error(self) -> None:
        self.assertEqual(main(["--file", str(self.path), "show", "nope"]), 1)

    def test_clear(self) -> None:
        self.assertEqual(main(["--file", str(self.path), "clear"]), 0)
        self.assertEqual(self.store.count(), 0)

    def test_ambiguous_prefix_returns_error(self) -> None:
        self.store.append(make_request(id="abc2"))
        self.assertEqual(main(["--file", str(self.path), "show", "abc"]), 1)

    def test_empty_method_filter_is_not_ignored(self) -> None:
        main(["--file", str(self.path), "--json", "list", "--method", ""])
        self.assertEqual(json.loads(self._stdout.getvalue()), [])

    def test_empty_path_filter_is_not_ignored(self) -> None:
        main(["--file", str(self.path), "--json", "list", "--path", ""])
        self.assertEqual(json.loads(self._stdout.getvalue()), [])

    def test_delete_command(self) -> None:
        self.assertEqual(main(["--file", str(self.path), "delete", "abc"]), 0)
        self.assertNotIn("abc123", [r.id for r in self.store.all()])

    def test_delete_missing_returns_error(self) -> None:
        self.assertEqual(main(["--file", str(self.path), "delete", "zzz"]), 1)

    def test_export_import_roundtrip(self) -> None:
        export_path = Path(self.path).parent / "export.json"
        self.assertEqual(
            main(["--file", str(self.path), "export", "--output", str(export_path)]), 0
        )
        imported_path = Path(self.path).parent / "imported.jsonl"
        self.assertEqual(
            main(["--file", str(imported_path), "import", str(export_path)]), 0
        )
        self.assertEqual(
            [r.id for r in InboxStore(imported_path).all()], ["abc123", "def456", "ghi789"]
        )

    def test_export_stdout_is_json(self) -> None:
        self.assertEqual(main(["--file", str(self.path), "export"]), 0)
        payload = json.loads(self._stdout.getvalue())
        self.assertEqual(payload["format"], "postbox-export")
        self.assertEqual(len(payload["requests"]), 3)

    def test_import_preserves_existing_records(self) -> None:
        export_path = Path(self.path).parent / "snapshot.json"
        self.assertEqual(
            main(["--file", str(self.path), "export", "--output", str(export_path)]), 0
        )
        other = Path(self.path).parent / "other.jsonl"
        other_store = InboxStore(other)
        other_store.append(make_request(id="kept"))
        self.assertEqual(main(["--file", str(other), "import", str(export_path)]), 0)
        self.assertEqual(
            [r.id for r in other_store.all()], ["kept", "abc123", "def456", "ghi789"]
        )

    def test_import_skips_duplicate_ids(self) -> None:
        export_path = Path(self.path).parent / "dupe-snapshot.json"
        main(["--file", str(self.path), "export", "--output", str(export_path)])
        target = Path(self.path).parent / "dupe-target.jsonl"
        main(["--file", str(target), "import", str(export_path)])
        self._stdout.seek(0)
        self._stdout.truncate(0)
        main(["--file", str(target), "import", str(export_path)])
        self.assertIn("skipped 3", self._stdout.getvalue())
        self.assertEqual(len(InboxStore(target).all()), 3)

    def test_import_refuses_unknown_version(self) -> None:
        export_path = Path(self.path).parent / "bad-version.json"
        export_path.write_text(
            json.dumps({"format": "postbox-export", "version": 99, "requests": []}),
            encoding="utf-8",
        )
        self.assertEqual(
            main(["--file", str(self.path), "import", str(export_path)]), 1
        )

    def test_import_refuses_bad_timestamp(self) -> None:
        export_path = Path(self.path).parent / "bad-time.json"
        record = make_request(id="badt").to_dict()
        record["received_at"] = "not-a-timestamp"
        export_path.write_text(
            json.dumps(
                {"format": "postbox-export", "version": 1, "requests": [record]}
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            main(["--file", str(self.path), "import", str(export_path)]), 1
        )

    def test_import_refuses_bad_record(self) -> None:
        export_path = Path(self.path).parent / "bad-record.json"
        export_path.write_text(
            json.dumps(
                {"format": "postbox-export", "version": 1, "requests": [{"id": "x"}]}
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            main(["--file", str(self.path), "import", str(export_path)]), 1
        )

    def test_list_filters_by_time(self) -> None:
        self.store.append(
            make_request(id="t1", received_at="2026-11-19T10:00:00+00:00")
        )
        self.store.append(
            make_request(id="t2", received_at="2026-11-20T10:00:00+00:00")
        )
        self._stdout.seek(0)
        self._stdout.truncate(0)
        self.assertEqual(
            main(["--file", str(self.path), "list", "--after", "2026-11-20"]), 0
        )
        output = self._stdout.getvalue()
        self.assertIn("t2", output)
        self.assertNotIn("t1", output)

    def test_invalid_time_filter_returns_error(self) -> None:
        self.assertEqual(
            main(["--file", str(self.path), "list", "--before", "yesterday"]), 2
        )

    def test_version_flag(self) -> None:
        self.assertEqual(main(["--version"]), 0)

    def test_loopback_detection(self) -> None:
        self.assertTrue(is_loopback("127.0.0.1"))
        self.assertTrue(is_loopback("localhost"))
        self.assertTrue(is_loopback("::1"))
        self.assertFalse(is_loopback("0.0.0.0"))
        self.assertFalse(is_loopback("192.168.1.5"))

    def test_non_loopback_host_is_refused(self) -> None:
        self.assertEqual(
            main(["--file", str(self.path), "serve", "--host", "0.0.0.0"]), 2
        )

    def test_invalid_numeric_flags_return_error(self) -> None:
        self.assertEqual(main(["--file", str(self.path), "serve", "--max", "0"]), 2)
        self.assertEqual(main(["--file", str(self.path), "list", "--limit", "-1"]), 2)

    def test_bad_replay_url_returns_error(self) -> None:
        self.assertEqual(
            main(["--file", str(self.path), "replay", "abc", "--url", "not-a-url"]), 1
        )

    def test_replay_sends_body_for_delete(self) -> None:
        target_store = InboxStore(Path(self.path).parent / "target.jsonl")
        server = InboxServer(("127.0.0.1", 0), target_store)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = main(
                ["--file", str(self.path), "replay", "ghi", "--url",
                 f"http://127.0.0.1:{port}/target", "--timeout", "3"]
            )
            self.assertEqual(result, 0)
            self.assertEqual(target_store.all()[0].body, b"bye")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def _replay_target(self, request_id: str, extra: list[str] | None = None) -> CapturedRequest:
        target_store = InboxStore(Path(self.path).parent / f"target-{request_id}.jsonl")
        server = InboxServer(("127.0.0.1", 0), target_store)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            args = [
                "--file", str(self.path), "replay", request_id,
                "--url", f"http://127.0.0.1:{port}/ignored", "--timeout", "3",
            ]
            if extra:
                args.extend(extra)
            result = main(args)
            self.assertEqual(result, 0)
            return target_store.all()[0]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_replay_preserves_captured_path_and_query(self) -> None:
        self.store.append(
            make_request(id="path1", method="GET", path="/original/route", query="a=1&b=2")
        )
        replayed = self._replay_target("path1", ["--preserve-path"])
        self.assertEqual(replayed.path, "/original/route")
        self.assertEqual(dict(urllib.parse.parse_qsl(replayed.query)), {"a": "1", "b": "2"})

    def test_replay_preserve_path_omits_redacted_query(self) -> None:
        self.store.append(
            make_request(id="path2", query="token=%5BREDACTED%5D&page=1")
        )
        replayed = self._replay_target("path2", ["--preserve-path"])
        self.assertNotIn("token", replayed.query)
        self.assertEqual(dict(urllib.parse.parse_qsl(replayed.query)), {"page": "1"})

    def test_replay_does_not_send_redacted_headers(self) -> None:
        self.store.append(
            make_request(
                id="sec1",
                headers=(("Authorization", "[REDACTED]"), ("Content-Type", "application/json")),
                query="token=%5BREDACTED%5D",
            )
        )
        target_store = InboxStore(Path(self.path).parent / "target-secrets.jsonl")
        server = InboxServer(("127.0.0.1", 0), target_store)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = main(
                ["--file", str(self.path), "replay", "sec1", "--url",
                 f"http://127.0.0.1:{port}/target", "--timeout", "3"]
            )
            self.assertEqual(result, 0)
            replayed = target_store.all()[0]
            self.assertIsNone(replayed.header("Authorization"))
            self.assertNotIn("s3cr3t", replayed.query)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_corrupt_inbox_gets_friendly_error(self) -> None:
        self.path.write_text(
            '{"format":"postbox-inbox","version":1}\nnot-json\n', encoding="utf-8"
        )
        self.assertEqual(main(["--file", str(self.path), "list"]), 1)


if __name__ == "__main__":
    unittest.main()
