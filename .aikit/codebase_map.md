# Codebase Map & Symbol Catalog 🗺️

> **AUTO-GENERATED & VERIFIED BY `scripts/update_knowledge_base.py`**
> This document catalogues Python modules, classes, and exported functions. See Map Coverage for any stack it does not index.

---

## 📊 Codebase Metrics
- **Total Python Modules**: `8`
- **Total Classes**: `16`
- **Total Top-Level Functions**: `19`
- **Total Class Methods**: `163`
- **Total Source Lines**: `2349`

## 🧭 Map Coverage
- **Indexed**: python (`8` module(s))
- **Not indexed by this map**: none (no non-Python source files were found)

---

## 📦 Modules & Exported Symbols

### [`postbox/__init__.py`](postbox/__init__.py)
> *Postbox: a local webhook inbox with replay.*

**Size**: `8` lines

---

### [`postbox/__main__.py`](postbox/__main__.py)
> *Allow ``python -m postbox.cli`` to run the CLI as documented.*

**Size**: `5` lines

---

### [`postbox/cli.py`](postbox/cli.py)
> *Command-line interface for Postbox.*

**Size**: `472` lines

**Functions:**
- `def _positive_int(value)`
- `def _iso_time(value)` - *Parse an ISO date or datetime into an aware UTC datetime.*
- `def _in_time_window(request, after, before)`
- `def is_loopback(host)` - *Return True for loopback names and addresses only.*
- `def _non_negative_float(value)`
- `def _port(value)`
- `def _add_global_flags(subparser)` - *Accept parent flags after the subcommand without overriding earlier values.*
- `def _build_parser()`
- `def _lookup_error(prefix, context, json_mode, ambiguous)` - *Report a failed lookup in the calling command's own language.*
- `def _find_or_error(store, prefix, context, json_mode)`
- `def _replay(request, url, timeout, preserve_path, json_mode)`
- `def _dispatch(args, store)`
- `def main(argv)` - *Run the CLI and return an exit code.*

---

### [`postbox/lock.py`](postbox/lock.py)
> *Cross-platform single-writer lock for a Postbox inbox file.*

**Size**: `61` lines

**Classes:**
- `class SingleWriterLock` - *An advisory exclusive lock on ``<inbox>.lock`` for a process lifetime.*
  - Method `.__init__(inbox_path)`
  - Method `.acquire()` - *Take the lock, returning False when another writer already holds it.*
  - Method `.release()` - *Release the lock and close its descriptor; safe to call twice.*

---

### [`postbox/models.py`](postbox/models.py)
> *The captured-request value object.*

**Size**: `168` lines

**Classes:**
- `class CorruptInboxError` - *A record in the inbox cannot be decoded safely.*
- `class CapturedRequest` - *An immutable record of one HTTP request received by the inbox.*
  - Method `.__post_init__()`
  - Method `.to_dict()` - *Return a JSON-safe representation (body is base64 encoded).*
  - Method `.from_dict(data)` - *Rebuild a request from its JSON-safe representation.*
  - Method `.text()` - *Return the body decoded as UTF-8, replacing invalid bytes.*
  - Method `.header(name)` - *Return the first header matching ``name`` case-insensitively.*

**Functions:**
- `def redact_headers(headers)` - *Replace credential-bearing header values with a fixed marker.*
- `def redact_query(query)` - *Replace credential-bearing query values while preserving other keys.*
- `def utcnow()` - *Return a stable, timezone-aware ISO-8601 timestamp.*

---

### [`postbox/server.py`](postbox/server.py)
> *A small threaded HTTP server that records every incoming request.*

**Size**: `318` lines

**Classes:**
- `class _BadChunkedBody` - *The chunked stream could not be decoded safely.*
- `class _OversizeChunkedBody` - *The decoded chunked body exceeds the configured limit.*
- `class InboxHandler` - *Handler that captures requests and exposes a small inspection API.*
  - Property `.store`
  - Property `.max_body`
  - Method `._send_json(payload, status, extra_headers)`
  - Method `._inbox_error(exc)` - *Report a store failure as JSON instead of dropping the connection.*
  - Method `._bad_request(reason)`
  - Method `._read_chunked_body()` - *Decode a chunked body with bounded reads and a cumulative size cap.*
  - Method `._capture()`
  - Method `._clear()`
  - Method `._paged_inbox(query)`
  - Method `._route()`
  - Method `.do_GET()`
  - Method `.do_POST()`
  - Method `.do_PUT()`
  - Method `.do_PATCH()`
  - Method `.do_DELETE()`
  - Method `.do_HEAD()`
  - Method `.do_OPTIONS()`
  - Method `.do_CONNECT()`
  - Method `.do_TRACE()`
  - Method `.log_message(format, *args)` - *Silence default stderr logging; the inbox is the record.*
- `class InboxServer` - *Threaded HTTP server bound to an :class:`InboxStore`.*
  - Method `.__init__(address, store, max_body)`
  - Method `.url()` - *Return the bound URL, including an OS-assigned port when requested.*

**Functions:**
- `def serve(port, store, host, max_body)` - *Run the inbox until interrupted.*

---

### [`postbox/store.py`](postbox/store.py)
> *Append-only JSONL persistence with atomic writes and a format version.*

**Size**: `221` lines

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
  - Method `.extend(requests)` - *Append a batch in one atomic write, honouring retention.*
  - Method `.all()` - *Return every captured request in arrival order.*
  - Method `.find(prefix)` - *Find a request by id prefix, refusing an ambiguous match.*
  - Method `.delete(prefix)` - *Remove one request by id prefix, refusing an ambiguous match.*
  - Method `.clear()` - *Empty the inbox with a temp-file + os.replace atomic write.*
  - Method `.count()` - *Return the number of captured requests.*

**Functions:**
- `def _fsync_dir(path)` - *Durably commit a directory entry where the platform allows it.*

---

### [`tests/test_postbox.py`](tests/test_postbox.py)
> *Tests for Postbox: models, store, server and CLI.*

**Size**: `1096` lines

**Classes:**
- `class ModelTests`
  - Method `.test_json_roundtrip_preserves_body()`
  - Method `.test_text_decodes_body()`
  - Method `.test_header_lookup_is_case_insensitive()`
  - Method `.test_corrupt_base64_is_refused()`
  - Method `.test_invalid_timestamp_is_refused()`
  - Method `.test_z_suffix_timestamp_is_accepted()`
  - Method `.test_missing_record_key_is_refused()`
  - Method `.test_sensitive_headers_are_redacted()`
  - Method `.test_oauth_header_names_are_redacted()`
  - Method `.test_oauth_query_keys_are_redacted()`
  - Method `.test_sensitive_query_keys_are_redacted()`
- `class StoreTests`
  - Method `.setUp()`
  - Method `.test_append_and_list()`
  - Method `.test_file_starts_with_versioned_header()`
  - Method `.test_unknown_version_is_refused_without_modifying_file()`
  - Method `.test_headerless_file_is_migrated_in_memory()`
  - Method `.test_find_by_prefix()`
  - Method `.test_ambiguous_prefix_is_refused()`
  - Method `.test_retention_keeps_newest_requests()`
  - Method `.test_delete_removes_only_the_match()`
  - Method `.test_delete_returns_none_when_missing()`
  - Method `.test_delete_refuses_ambiguous_prefix()`
  - Method `.test_extend_preserves_existing_records()`
  - Method `.test_extend_appends_a_batch()`
  - Method `.test_extend_honours_retention()`
  - Method `.test_clear_empties_without_temp_files()`
  - Method `.test_missing_file_is_empty()`
  - Method `.test_corrupt_line_is_refused()`
  - Method `.test_bad_record_shape_is_refused()`
  - Method `.test_failed_trim_leaves_no_orphan_line()`
  - Method `.test_directory_is_fsynced_on_create_and_clear()`
  - Method `.test_concurrent_appends_lose_no_requests()`
- `class LockTests`
  - Method `.setUp()`
  - Method `.test_lock_is_exclusive_and_releasable()`
  - Method `.test_cli_refuses_when_lock_is_held()`
  - Method `.test_cli_writes_refused_while_lock_is_held()`
  - Method `.test_read_commands_allowed_while_lock_is_held()`
- `class ServerTests`
  - Method `.setUp()`
  - Method `._stop_server()`
  - Method `._raw(payload)`
  - Method `._post(path, payload)`
  - Method `.test_ipv6_loopback_binds_and_brackets_url()`
  - Method `.test_url_reports_the_bound_port()`
  - Method `.test_request_is_captured()`
  - Method `.test_inspect_and_clear_endpoints()`
  - Method `.test_non_get_requests_to_read_only_paths_are_captured()`
  - Method `.test_inbox_pagination()`
  - Method `.test_inbox_pagination_rejects_bad_values()`
  - Method `.test_capture_into_unsupported_version_is_refused()`
  - Method `.test_capture_into_corrupt_inbox_is_refused()`
  - Method `.test_non_utf8_inbox_returns_json_500()`
  - Method `.test_health_endpoint()`
  - Method `.test_conflicting_content_length_is_rejected()`
  - Method `.test_malformed_content_length_is_rejected()`
  - Method `.test_negative_content_length_is_rejected()`
  - Method `.test_chunked_body_is_decoded()`
  - Method `.test_chunked_oversize_body_is_rejected()`
  - Method `.test_malformed_chunk_size_is_rejected()`
  - Method `.test_get_clear_is_a_noop()`
  - Method `.test_clear_with_malformed_content_length_is_rejected()`
  - Method `.test_clear_rejects_truncated_body()`
  - Method `.test_clear_with_negative_content_length_is_rejected()`
  - Method `.test_clear_405_advertises_allowed_methods()`
  - Method `.test_read_only_methods_on_clear_are_noops()`
  - Method `.test_truncated_body_is_rejected()`
  - Method `.test_clear_with_body_does_not_abort_client()`
  - Method `.test_pagination_detected_by_query_key_not_substring()`
  - Method `.test_corrupt_inbox_returns_json_error()`
  - Method `.test_head_and_options_are_captured()`
  - Method `.test_secrets_are_redacted_on_capture()`
  - Method `.test_oversized_body_is_rejected()`
- `class ResponseWriteTests`
  - Method `.test_send_json_tolerates_client_disconnect()`
- `class CliTests`
  - Method `.setUp()`
  - Method `.test_list_and_show()`
  - Method `.test_list_filters_by_method_and_path()`
  - Method `.test_global_flags_after_subcommand()`
  - Method `.test_global_file_flag_after_subcommand()`
  - Method `.test_json_output_is_parseable()`
  - Method `.test_show_missing_returns_error()`
  - Method `.test_python_m_entrypoint_works()`
  - Method `.test_clear()`
  - Method `.test_clear_json_output()`
  - Method `.test_import_json_output()`
  - Method `.test_ambiguous_prefix_returns_error()`
  - Method `.test_empty_method_filter_is_not_ignored()`
  - Method `.test_empty_path_filter_is_not_ignored()`
  - Method `.test_delete_command()`
  - Method `.test_delete_missing_returns_error()`
  - Method `.test_export_import_roundtrip()`
  - Method `.test_export_stdout_is_json()`
  - Method `.test_import_preserves_existing_records()`
  - Method `.test_import_dedupes_ids_within_snapshot()`
  - Method `.test_import_skips_duplicate_ids()`
  - Method `.test_import_refuses_unknown_version()`
  - Method `.test_import_refuses_bad_timestamp()`
  - Method `.test_import_refuses_bad_record()`
  - Method `.test_list_filters_by_time()`
  - Method `.test_invalid_time_filter_returns_error()`
  - Method `.test_version_flag()`
  - Method `.test_loopback_detection()`
  - Method `.test_non_loopback_host_is_refused()`
  - Method `.test_serve_rejects_out_of_range_port()`
  - Method `.test_replay_rejects_nonfinite_timeout()`
  - Method `.test_invalid_numeric_flags_return_error()`
  - Method `.test_replay_error_is_machine_readable_under_json()`
  - Method `.test_bad_replay_url_returns_error()`
  - Method `.test_replay_json_output()`
  - Method `.test_replay_keeps_content_length_zero_for_bodyless_post()`
  - Method `.test_replay_preserves_duplicate_headers()`
  - Method `.test_replay_sends_body_for_delete()`
  - Method `._replay_target(request_id, extra)`
  - Method `.test_replay_preserves_captured_path_and_query()`
  - Method `.test_replay_preserve_path_omits_redacted_query()`
  - Method `.test_replay_does_not_send_redacted_headers()`
  - Method `.test_show_error_is_not_replay_specific()`
  - Method `.test_delete_error_is_not_replay_specific()`
  - Method `.test_lookup_error_is_machine_readable_under_json()`
  - Method `.test_clear_can_remove_corrupt_inbox()`
  - Method `.test_corrupt_inbox_gets_friendly_error()`

**Functions:**
- `def make_request(method, path, body, id, headers, query, received_at)`

---
