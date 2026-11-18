# Codebase Map & Symbol Catalog 🗺️

> **AUTO-GENERATED & VERIFIED BY `scripts/update_knowledge_base.py`**
> This document catalogues Python modules, classes, and exported functions. See Map Coverage for any stack it does not index.

---

## 📊 Codebase Metrics
- **Total Python Modules**: `6`
- **Total Classes**: `11`
- **Total Top-Level Functions**: `11`
- **Total Class Methods**: `75`
- **Total Source Lines**: `1042`

## 🧭 Map Coverage
- **Indexed**: python (`6` module(s))
- **Not indexed by this map**: none (no non-Python source files were found)

---

## 📦 Modules & Exported Symbols

### [`postbox/__init__.py`](postbox/__init__.py)
> *Postbox: a local webhook inbox with replay.*

**Size**: `8` lines

---

### [`postbox/cli.py`](postbox/cli.py)
> *Command-line interface for Postbox.*

**Size**: `199` lines

**Functions:**
- `def _positive_int(value)`
- `def _non_negative_float(value)`
- `def _build_parser()`
- `def _find_or_error(store, prefix)`
- `def _replay(request, url, timeout)`
- `def _dispatch(args, store)`
- `def main(argv)` - *Run the CLI and return an exit code.*

---

### [`postbox/models.py`](postbox/models.py)
> *The captured-request value object.*

**Size**: `107` lines

**Classes:**
- `class CorruptInboxError` - *A record in the inbox cannot be decoded safely.*
- `class CapturedRequest` - *An immutable record of one HTTP request received by the inbox.*
  - Method `.to_dict()` - *Return a JSON-safe representation (body is base64 encoded).*
  - Method `.from_dict(data)` - *Rebuild a request from its JSON-safe representation.*
  - Method `.text()` - *Return the body decoded as UTF-8, replacing invalid bytes.*
  - Method `.header(name)` - *Return the first header matching ``name`` case-insensitively.*

**Functions:**
- `def utcnow()` - *Return a stable, timezone-aware ISO-8601 timestamp.*

---

### [`postbox/server.py`](postbox/server.py)
> *A small threaded HTTP server that records every incoming request.*

**Size**: `163` lines

**Classes:**
- `class InboxHandler` - *Handler that captures requests and exposes a small inspection API.*
  - Property `.store`
  - Property `.max_body`
  - Method `._send_json(payload, status)`
  - Method `._inbox_error(exc)` - *Report a store failure as JSON instead of dropping the connection.*
  - Method `._bad_request(reason)`
  - Method `._capture()`
  - Method `._clear()`
  - Method `._route()`
  - Method `.do_GET()`
  - Method `.do_POST()`
  - Method `.do_PUT()`
  - Method `.do_PATCH()`
  - Method `.do_DELETE()`
  - Method `.log_message(format, *args)` - *Silence default stderr logging; the inbox is the record.*
- `class InboxServer` - *Threaded HTTP server bound to an :class:`InboxStore`.*
  - Method `.__init__(address, store, max_body)`

**Functions:**
- `def serve(port, store, host, max_body)` - *Run the inbox until interrupted.*

---

### [`postbox/store.py`](postbox/store.py)
> *Append-only JSONL persistence with atomic writes and a format version.*

**Size**: `198` lines

**Classes:**
- `class UnsupportedFormatError` - *The inbox uses a format or version this loader cannot read.*
- `class AmbiguousIdError` - *A short id prefix matches more than one captured request.*
  - Method `.__init__(prefix, matches)`
- `class InboxStore` - *A thread-safe, append-only, versioned store of captured requests.*
  - Method `.__init__(path, max_requests)`
  - Method `._header_line()`
  - Method `._ensure_file()` - *Create the file with its header when it is absent or empty.*
  - Method `._read_lines()` - *Read and validate the file, returning its record lines.*
  - Method `._decode(lines)` - *Decode record lines, converting any parse failure to one error type.*
  - Method `._atomic_write(requests)` - *Replace the file with a fresh header and exactly ``requests``.*
  - Method `.append(request)` - *Append one request, fsync it, and enforce retention if bounded.*
  - Method `.all()` - *Return every captured request in arrival order.*
  - Method `.find(prefix)` - *Find a request by id prefix, refusing an ambiguous match.*
  - Method `.clear()` - *Empty the inbox with a temp-file + os.replace atomic write.*
  - Method `.count()` - *Return the number of captured requests.*

**Functions:**
- `def _fsync_dir(path)` - *Durably commit a directory entry where the platform allows it.*

---

### [`tests/test_postbox.py`](tests/test_postbox.py)
> *Tests for Postbox: models, store, server and CLI.*

**Size**: `367` lines

**Classes:**
- `class ModelTests`
  - Method `.test_json_roundtrip_preserves_body()`
  - Method `.test_text_decodes_body()`
  - Method `.test_header_lookup_is_case_insensitive()`
  - Method `.test_corrupt_base64_is_refused()`
  - Method `.test_missing_record_key_is_refused()`
- `class StoreTests`
  - Method `.setUp()`
  - Method `.test_append_and_list()`
  - Method `.test_file_starts_with_versioned_header()`
  - Method `.test_unknown_version_is_refused_without_modifying_file()`
  - Method `.test_headerless_file_is_migrated_in_memory()`
  - Method `.test_find_by_prefix()`
  - Method `.test_ambiguous_prefix_is_refused()`
  - Method `.test_retention_keeps_newest_requests()`
  - Method `.test_clear_empties_without_temp_files()`
  - Method `.test_missing_file_is_empty()`
  - Method `.test_corrupt_line_is_refused()`
  - Method `.test_bad_record_shape_is_refused()`
  - Method `.test_failed_trim_leaves_no_orphan_line()`
  - Method `.test_directory_is_fsynced_on_create_and_clear()`
  - Method `.test_concurrent_appends_lose_no_requests()`
- `class ServerTests`
  - Method `.setUp()`
  - Method `._stop_server()`
  - Method `._raw(payload)`
  - Method `._post(path, payload)`
  - Method `.test_request_is_captured()`
  - Method `.test_inspect_and_clear_endpoints()`
  - Method `.test_health_endpoint()`
  - Method `.test_malformed_content_length_is_rejected()`
  - Method `.test_negative_content_length_is_rejected()`
  - Method `.test_chunked_body_is_rejected()`
  - Method `.test_get_clear_is_a_noop()`
  - Method `.test_corrupt_inbox_returns_json_error()`
  - Method `.test_oversized_body_is_rejected()`
- `class CliTests`
  - Method `.setUp()`
  - Method `.test_list_and_show()`
  - Method `.test_list_filters_by_method_and_path()`
  - Method `.test_json_output_is_parseable()`
  - Method `.test_show_missing_returns_error()`
  - Method `.test_clear()`
  - Method `.test_ambiguous_prefix_returns_error()`
  - Method `.test_invalid_numeric_flags_return_error()`
  - Method `.test_bad_replay_url_returns_error()`
  - Method `.test_replay_sends_body_for_delete()`
  - Method `.test_corrupt_inbox_gets_friendly_error()`

**Functions:**
- `def make_request(method, path, body, id)`

---
