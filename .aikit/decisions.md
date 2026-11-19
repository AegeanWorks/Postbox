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
