# Decisions

Why this project is built the way it is, oldest first. One entry per decision that changes a
contract, a dependency, a data format or the architecture; IDs are consecutive and never reused.

<!-- Entry format:
### ADR-NNN: <decision in one line>
- **Date**: YYYY-MM-DD
- **Status**: Accepted
- **Context**: <what forced the decision>
- **Decision**: <what was decided>
- **Consequences**: <what follows, including what it costs>
-->

### ADR-001: Versioned append-only JSONL inbox
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: The project guideline requires persisted state to carry a format version, and captured requests must survive an abrupt stop without a rewrite of the whole file.
- **Decision**: Use a versioned header line in an append-only JSONL file, fsync every append, refuse unknown versions without modifying the file, and migrate older versions in memory through a registered migration table.
- **Consequences**: Readers must handle the header and the migration table; adds one line of overhead per file and a small dispatch cost, in exchange for safe evolution and crash durability.

### ADR-002: Localhost-only server with bounded input
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A webhook catcher accepts untrusted payloads from anything that can reach its port, and an unbounded inbox can exhaust disk during a flood.
- **Decision**: Bind to 127.0.0.1 by default, reject bodies above a configurable limit with HTTP 413, and offer an optional newest-N retention cap applied by atomic replacement.
- **Consequences**: Callers sending large payloads get an explicit 413 instead of a silent memory spike; enabling --max rewrites the file after a trim, which is atomic but costs one write per overflow.

### ADR-003: Clear endpoint requires POST or DELETE
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: GET /__clear destroyed captures when a browser or prefetch followed the link, and GET is defined as safe and idempotent.
- **Decision**: Clear is now POST /__clear or DELETE /__clear; GET /__clear returns 405 without mutating state.
- **Consequences**: Existing callers using GET must switch methods; captures are no longer lost to prefetch, and the endpoint contract is documented in the README.

### ADR-004: Single-writer persistence model
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: The store lock is in-process. Two serve processes sharing one --file can interleave appends with an atomic trim and lose updates.
- **Decision**: Keep the in-process lock and document a single-writer-per-file rule instead of adding cross-platform OS file locking.
- **Consequences**: Users must run one inbox per file; avoids msvcrt/fcntl branches and stale-lock recovery, with the limit stated in the README and here.

### ADR-005: Redact credentials at capture and refuse public binds
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed Authorization, Cookie and token values stored verbatim, and --host 0.0.0.0 was accepted despite an unauthenticated handler and a 'localhost only' claim.
- **Decision**: Store sensitive headers and query values as [REDACTED], skip redacted headers on replay, bind to loopback by default, and require --allow-public for any other host.
- **Consequences**: Debuggers keep method, path and non-sensitive headers; credentials are not persisted or replayed. Users who need a shared inbox must opt in explicitly and accept the lack of authentication.

### ADR-006: Decode chunked request bodies
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: Transfer-Encoding: chunked was rejected with 400, so senders that stream without Content-Length could not be captured.
- **Decision**: Decode chunked bodies with bounded line reads, a cumulative max_body cap, trailer consumption, and 400/413 for malformed or oversize streams.
- **Consequences**: More clients can be captured; the decoder adds a small parsing surface that is covered by raw-socket tests, and no body is stored until decoding succeeds.

### ADR-007: Enforce single writer with an OS lock
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: ADR-004 documented a single-writer rule but nothing prevented a second serve process from sharing a file and losing updates during an atomic trim.
- **Decision**: Hold an OS-level exclusive lock on <file>.lock for the serve process lifetime (msvcrt on Windows, flock elsewhere); a second serve exits 1 with a Why/Fix message.
- **Consequences**: The documented limit is now enforced. The OS releases the lock on process death, so no stale-lock recovery is needed; read commands remain available.

### ADR-008: Versioned export envelope and opt-in faithful replay
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: The inbox was portable only by copying raw JSONL, and replay discarded the captured path and query, so it could not reproduce the original endpoint.
- **Decision**: Export a versioned postbox-export JSON envelope with refuse-on-unknown import, and add an opt-in --preserve-path replay flag that uses the captured route while dropping redacted query values.
- **Consequences**: Snapshots can evolve safely and replays can target another host without losing the original route; default replay behaviour is unchanged.

### ADR-009: Capture every method and paginate the inbox endpoint
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: HEAD and OPTIONS requests returned 501 and were not captured, there was no targeted delete, and the inbox endpoint always returned the whole file.
- **Decision**: Route all common HTTP methods through the capture path with bodyless HEAD responses, add an ambiguity-safe delete command, and support limit/offset pagination while keeping the bare-array default.
- **Consequences**: No webhook method is silently missed and large inboxes can be paged; existing callers of the inbox endpoint keep the same default response shape.

### ADR-010: Batch appends preserve existing records and imports are idempotent
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed extend() replaced the whole inbox when no retention cap was set, so an import into a non-empty inbox destroyed its captures; a second import would then duplicate ids.
- **Decision**: Always decode existing records in extend(), and skip imported records whose id already exists in the target, reporting imported and skipped counts.
- **Consequences**: Imports are safe to repeat and non-destructive; a caller who wants a fresh inbox must clear it or use a different --file before importing.

### ADR-011: Strict clear-method guard and key-based pagination detection
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed HEAD/OPTIONS /__clear destroyed captures despite the documented protection, POST /__clear with a body aborted the client, and substring matching made ?filter=limit look like pagination.
- **Decision**: Allow only POST and DELETE to clear, drain a bounded body prefix before answering, detect pagination by parsed query keys, and treat an explicitly empty --method/--path as a real filter.
- **Consequences**: Read-only probes are harmless, large clear bodies no longer reset the connection, and endpoint/filter parsing follows the documented contract.

### ADR-012: Reject bodies shorter than Content-Length
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed a client declaring 100 bytes but sending 3 got 200, and the partial body was stored as if complete, corrupting replays and analysis.
- **Decision**: Compare the received byte count with Content-Length before constructing a record; return 400 and capture nothing on a mismatch.
- **Consequences**: Every stored body matches its declared length. Clients that close early now get an explicit error instead of a silently truncated capture.

### ADR-013: Validate capture timestamps at construction
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A record with received_at='not-a-timestamp' could be stored or imported, after which a single such record made every time-filtered list fail with a misleading file/permissions error.
- **Decision**: Parse received_at as ISO-8601 in CapturedRequest.__post_init__, accepting a trailing Z, and raise CorruptInboxError otherwise.
- **Consequences**: Bad snapshots are refused at import and corrupt records cannot enter the inbox; time filtering is guaranteed to parse every stored record.

### ADR-014: Treat client disconnect as a normal response-write outcome
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed a routine client disconnect before reading the response produced a full ConnectionAbortedError traceback from _send_json, even though the capture succeeded.
- **Decision**: Ignore OS-level OSError when writing the response body; the record is already persisted and no retry is meaningful once the client is gone.
- **Consequences**: Operator logs stay clean under normal client churn; a genuine store failure is still reported as JSON 500 because it happens before the response write.

### ADR-015: Cover common OAuth credential names in redaction
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed X-Access-Token, Api-Key, access_token and refresh_token were stored verbatim despite redaction being enabled.
- **Decision**: Add those names (plus X-CSRF-Token, Authentication and auth) to the sensitive header and query sets used at capture and replay.
- **Consequences**: Common OAuth and API credentials no longer persist in the inbox; the defensive lists are exact case-insensitive matches, so unrelated fields keep their values.

### ADR-016: Advertise the bound port after server creation
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: With --port 0 the OS assigns an ephemeral port, but the CLI printed the requested 0, giving users an unusable URL.
- **Decision**: Derive the startup URL from server.server_address after binding, via InboxServer.url(); the CLI no longer formats the requested port itself.
- **Consequences**: Fixed-port output is unchanged; ephemeral-port runs now print a connectable URL.

### ADR-017: Guard CLI writes with the single-writer lock
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed delete succeeded while serve held the lock, so CLI writes could race server appends and atomic trims and lose data.
- **Decision**: Acquire the same OS lock for clear, delete and import, refusing with Why/Fix when held; leave list, show and export unlocked.
- **Consequences**: All processes that mutate an inbox are serialized; read commands stay available while the server runs.

### ADR-018: Normalize inbox read errors and accept flags after subcommands
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A non-UTF-8 byte made the server drop the connection with a traceback instead of the documented JSON 500, and postbox list --json failed because global flags were parent-only.
- **Decision**: Include UnicodeDecodeError in the server's inbox error tuple, and accept --file/--json after each subcommand using suppressed defaults so earlier values are preserved.
- **Consequences**: Every inbox read failure is a JSON 500, and either flag placement works without changing the pre-subcommand form.

### ADR-019: Context-aware, machine-readable lookup errors
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A shared lookup helper printed replay-specific guidance for show and delete, and emitted human text even with --json, breaking agent parsing.
- **Decision**: Parameterise the helper with command context and JSON mode; emit not_found/ambiguous_id JSON objects under --json and use the calling command's name in human guidance.
- **Consequences**: Agents can parse failures consistently; delete now validates through the same helper before removing by full id.

### ADR-020: Structured success output for mutating and replay commands
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed clear and import printed human text under --json, so agents could not parse the result even though delete and error paths were structured.
- **Decision**: Emit JSON success objects for clear, import and replay (in addition to the existing delete object), leaving human output unchanged.
- **Consequences**: Every command result is consistently parseable in --json mode; the shapes are documented in the README.

### ADR-021: Structured replay failure output
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed replay failures still printed prose under --json, unlike all other command results documented in ADR-020.
- **Decision**: Print a replay_failed JSON object with an error detail field on replay failure in JSON mode, preserving the human Why and Fix text otherwise.
- **Consequences**: Agents can parse replay success and failure uniformly; the exit code stays 1.

### ADR-022: Only GET and HEAD own the read-only control paths
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed POST /__inbox and PUT /__health received a control response and were never captured, silently discarding webhooks.
- **Decision**: Fall through to capture for any non-GET/HEAD request to /__inbox or /__health; clear remains POST/DELETE only.
- **Consequences**: No request is lost because of its path; the control responses stay unchanged for the documented GET/HEAD callers.

### ADR-023: Advertise allowed methods on the clear 405
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed GET /__clear returned 405 without the Allow header required for that status.
- **Decision**: Send Allow: POST, DELETE on the clear-endpoint 405 by allowing extra headers in the JSON response helper.
- **Consequences**: The response is RFC-compliant and clients can discover the supported methods; the status and body are unchanged.

### ADR-024: Dedupe within snapshots and reject duplicate Content-Length
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed a snapshot with the same id twice created ambiguous records, and two conflicting Content-Length values were accepted with a truncated body.
- **Decision**: Track seen ids while importing so duplicates inside a snapshot are skipped, and reject any request carrying more than one Content-Length header with 400.
- **Consequences**: Imports cannot create unusable ambiguous ids; smuggling-shaped requests are refused before capture.

### ADR-025: Validate ports, preserve repeated headers and support IPv6 loopback
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed an out-of-range port crashed with OverflowError, replay dropped a repeated header, and ::1 could not bind despite being recognized as loopback.
- **Decision**: Validate ports in argparse to 0..65535, replay headers as ordered pairs through http.client, and select AF_INET6 for colon hosts with a bracketed URL.
- **Consequences**: Port typos get a friendly error, repeated webhook headers replay faithfully, and IPv6 loopback works; replay no longer follows redirects automatically.

### ADR-026: Validate inbox on every append and keep zero Content-Length on replay
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed a capture into a corrupt or unsupported-version inbox was acknowledged with 200 and an id but could never be read back, and bodyless replay omitted Content-Length so strict targets answered 411.
- **Decision**: Decode existing records before every append regardless of retention settings, and always send Content-Length in replay based on the captured body.
- **Consequences**: No capture is acknowledged into an unreadable file; bodyless webhooks replay faithfully. Append now reads the file once per capture, which was already the case with retention enabled.

### ADR-027: Reject non-finite timeouts and validate clear framing
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed --timeout inf crashed with OverflowError, and malformed or negative Content-Length on POST /__clear silently wiped the inbox while capture rejected the same input.
- **Decision**: Validate --timeout as finite and bounded, catch OverflowError as a backstop, and apply capture's Content-Length checks before any clear.
- **Consequences**: Invalid timeouts get friendly errors, and a malformed clear cannot destroy captures.

### ADR-028: Make documented entry point run and harden clear paths
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed python -m postbox.cli was a silent no-op, a truncated clear body wiped the inbox, and CLI clear refused the corrupt file it should fix.
- **Decision**: Add a __main__ guard and package __main__.py, reject short clear bodies like capture does, and let CLI clear succeed without decoding a corrupt inbox.
- **Consequences**: Documented commands work, malformed clear requests leave captures intact, and a corrupt inbox can be cleared from either CLI or HTTP.

### ADR-029: Read JSONL by newline and reject blank pagination
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed str.splitlines() splits on U+0085/U+2028/U+2029, so a captured header containing one made every later read fail, and ?limit= was silently ignored.
- **Decision**: Split records only on \\n and parse query strings with keep_blank_values so blank pagination values are rejected.
- **Consequences**: Unicode content inside records stays readable, and malformed pagination gets 400 as documented.

### ADR-030: Structured top-level errors and honest empty-list output
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed main() printed prose for corrupt/unsupported/OS errors even under --json, and a filtered-empty list claimed the inbox itself was empty.
- **Decision**: Emit error objects for those top-level exceptions in JSON mode and distinguish an empty inbox from a filter with no matches.
- **Consequences**: All CLI output is parseable under --json, and empty-list messages accurately describe the result.

### ADR-031: Drain clear bodies fully and validate chunked framing
- **Date**: 2026-11-19
- **Status**: Accepted
- **Context**: A probe showed a complete 16 MiB + 1 clear body was rejected as incomplete, and malformed chunked clear destroyed the inbox while capture rejected the same input.
- **Decision**: Drain declared bodies in bounded chunks until complete and validate chunked bodies with the shared decoder before clearing.
- **Consequences**: Large legitimate clears succeed, malformed framing gets 400 without data loss, and clear now shares capture's framing rules.
