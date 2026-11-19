# Postbox

<!-- aegean:media:start -->
<p align="center"><a href="LICENSE"><img src="https://img.shields.io/badge/license-AL--1.1-1c7ed6" alt="License"></a> <a href="https://github.com/AegeanWorks/AIKit"><img src="https://img.shields.io/badge/AIKit-integrated-6f42c1" alt="AIKit Integrated"></a></p>
<!-- aegean:media:end -->

A local webhook inbox with replay. Point a webhook at it, inspect what arrived,
then replay it to another target. Zero dependencies, Python 3.10+, localhost only.

## Why

Debugging webhooks usually means pasting a payload into a public request-bin
service — which sends your data to someone else's machine. Postbox keeps the
whole loop on your own machine and stores requests in one readable, versioned
JSONL file.

- Threaded HTTP server captures method, path, query, headers and body.
- Append-only, fsynced persistence; a kill does not lose a captured request.
- A format version with refuse-on-unknown loading and in-memory migration (G-001).
- Bounded body size and optional retention keep the inbox from growing forever.
- A small CLI to list, filter, inspect, replay and clear captured requests.

## Cold-Start Quick Reference

- **Runtime Prerequisites**: Python 3.10+ (standard library only); no network, no account.
- **Version**: `postbox --version`

**Canonical Regression Shield Gate**:
```
python -m unittest discover -s tests
```

- **Project gate from the workspace root**: `python aikit.py check postbox --allow-dirty`
- **Install as a command**: `pip install .` then `postbox --help`
- **Start the inbox**: `python -m postbox.cli serve --port 8787` (`--port 0` prints the OS-assigned port; ports are validated to 0..65535)
- **List captures**: `python -m postbox.cli --file inbox.jsonl list`
- **Inspect one**: `python -m postbox.cli --file inbox.jsonl show <id>`
- **Replay one**: `python -m postbox.cli --file inbox.jsonl replay <id> --url http://127.0.0.1:3000/hook`
- **Delete one**: `python -m postbox.cli --file inbox.jsonl delete <id>`
- **Clear**: `python -m postbox.cli --file inbox.jsonl clear`
- **Agent-friendly output**: `python -m postbox.cli --file inbox.jsonl --json list --method POST --path /hook`
- **Flag placement**: `--file` and `--json` also work after the subcommand, e.g. `postbox list --json`
- **Agent errors**: with `--json`, a missing or ambiguous id prints `{"error": "not_found"|"ambiguous_id", "prefix": ...}`
- **Agent results**: with `--json`, `clear`/`import`/`delete`/`replay` print `{"cleared": N}`, `{"imported": N, "skipped": M}`, `{"deleted": "id"}` or `{"method": ..., "url": ..., "status": ...}`
- **Replay failures**: with `--json`, a failed replay prints `{"error": "replay_failed", "detail": ...}`
- **Timeouts**: `--timeout` must be finite, non-negative and at most 86400 seconds
- **Time window**: `python -m postbox.cli --file inbox.jsonl list --after 2026-11-19 --before 2026-11-20`
- **Portable snapshot**: `python -m postbox.cli --file inbox.jsonl export --output snapshot.json`
- **Restore snapshot**: `python -m postbox.cli --file other.jsonl import snapshot.json`
- **Replay to another host with the original route**: `python -m postbox.cli --file inbox.jsonl replay <id> --url http://localhost:3000 --preserve-path`

## What's Inside

The workspace is the project root; every path below is relative to it.

```text
postbox/
├── .aikit/                   <- project memory: guidelines, decisions, ledger, map
│   ├── project.json          <- project identity and test command
│   ├── guidelines.md         <- project rules
│   ├── decisions.md          <- ADR register
│   ├── task_ledger.json      <- delivery ledger
│   ├── task_ledger_archive.jsonl <- archived checkpoints
│   ├── media.json            <- reviewed brand media state
│   └── codebase_map.md       <- generated AST symbol catalog
├── .github/                  <- project-local CI
│   └── workflows/            <- workflow definitions
│       └── tests.yml         <- regression suite on push and PR
├── .gitignore                <- transient AIKit state excluded
├── assets/                   <- generated brand candidates (pending review)
│   ├── logo.svg              <- project mark
│   └── social-preview.svg    <- social card
├── COMMERCIAL.md             <- commercial licensing tiers
├── LICENSE                   <- Aegean License 1.1
├── LICENSES/                 <- pinned license text for REUSE
│   └── LicenseRef-AL-1.1.txt  <- AL-1.1 canonical text
├── REUSE.toml                <- REUSE aggregate license annotations
├── pyproject.toml            <- package metadata and console script
├── postbox/                  <- package
│   ├── __init__.py           <- version marker
│   ├── lock.py               <- cross-platform single-writer OS lock
│   ├── models.py             <- immutable CapturedRequest and JSON/base64 codec
│   ├── store.py              <- versioned append-only JSONL, atomic writes, retention
│   ├── server.py             <- threaded HTTP handler, health and size limits
│   └── cli.py                <- serve / list / show / clear / replay
├── tests/                    <- test suite
│   └── test_postbox.py       <- models, store, server and CLI over real HTTP
└── README.md                 <- this file
```

State is value-based: `CapturedRequest` is immutable and JSON-safe, so a run can
be replayed or inspected without side effects. Writes are serialized with a lock
because the server is threaded, and every append is flushed and fsynced. The
file starts with `{"format": "postbox-inbox", "version": 1}`; an unknown version
is refused without touching the file, and a registered older version migrates
in memory.

## HTTP surface

| Endpoint | Purpose |
| --- | --- |
| any path, any method (including HEAD and OPTIONS) | capture the request and return `{"ok": true, "id": ...}` |
| `GET /__inbox` | all captured requests as JSON |
| `GET /__inbox?limit=N&offset=N` | one page plus `total`, `offset`, `limit`, `next_offset` |
| `GET /__health` | readiness and current request count |
| `POST /__clear` or `DELETE /__clear` | empty the inbox |

The read-only paths `/__inbox` and `/__health` are controlled only by GET and HEAD;
any other method sent there is captured like an ordinary webhook. HEAD requests
are captured but their responses carry headers only, as HTTP requires.
Invalid pagination values get `400`; without query parameters the inbox still returns
the original bare JSON array.

Only `POST /__clear` and `DELETE /__clear` clear the inbox; every other method,
including HEAD and OPTIONS, returns `405` with `Allow: POST, DELETE` so a prefetch
cannot destroy captures and clients can discover the supported methods. The same
`Content-Length` validation as capture applies to clear, so malformed framing gets
`400` before anything is removed.
Bodies above
`--max-body` bytes (default 1 MiB) get a `413` and are not stored. Chunked bodies
are decoded with the same cumulative size limit; malformed chunk framing gets
`400`. Malformed, negative or duplicate `Content-Length` headers also get `400`,
as does a body
that is shorter than the declared length; incomplete bodies are never stored.
A corrupt or non-UTF-8 inbox
produces a JSON `500` rather than a dropped connection, including on capture:
an unreadable file is never acknowledged with an id that could not be retrieved. `serve --max N` keeps only
the newest N requests by rewriting the file atomically.

Only one `serve` process can write a given `--file`: an OS-level lock on
`<file>.lock` is held for the server's lifetime, and a second server exits with a
clear error. `clear`, `delete` and `import` also refuse to write while that lock is held, so a
CLI command cannot race a server append; `list`, `show` and `export` remain readable.
A client that
disconnects before reading a response does not produce a server traceback; the
request is captured first and only the response write is skipped.

## Security

The server binds to `127.0.0.1` by default and refuses non-loopback hosts unless
`--allow-public` is passed deliberately; that option prints a warning because the
inbox has no authentication.

Captured `Authorization`, `Proxy-Authorization`, `Cookie`, `Set-Cookie`,
`X-Api-Key`, `X-Auth-Token`, `X-Access-Token`, `X-CSRF-Token`, `Api-Key` and
`Authentication` headers are stored as `[REDACTED]`, as are query values for
`token`, `access_token`, `refresh_token`, `auth`, `api_key`, `apikey`, `secret`,
`password` and `key`. Replay
never sends a redacted header. This keeps credentials out of the JSONL file,
but bodies are still stored verbatim — do not send secrets in request bodies.

## Replay

Replay strips hop-by-hop headers (`host`, `content-length`, `connection`,
`transfer-encoding`, `accept-encoding`), then sets `Content-Length` from the
captured body — including `0` for bodyless requests — and sends that body for
every method, including GET and DELETE. By default it uses exactly the URL you pass;
`--preserve-path` instead combines that URL's scheme and host with the captured
path and query, dropping any query value that was redacted. Repeated headers are
preserved by sending them individually rather than through a name-keyed header map.
Id prefixes that
match more than one request are refused rather than silently resolving to the
first match. `list --after` and `list --before` accept ISO dates or datetimes
and compare against `received_at` in UTC.

`export` writes a versioned `postbox-export` JSON envelope to stdout or
`--output`; `import` validates the format and version before appending the
records atomically. Every record must carry an ISO-8601 `received_at` (a trailing
`Z` is accepted); snapshots with invalid timestamps are refused. It preserves
captures already in the target inbox and skips
records whose id is already present in the target or duplicated in the snapshot,
reporting both counts. Unknown versions
are refused without modifying the file.
It is a development tool: it binds to `127.0.0.1` by default (IPv6 loopback
`::1` is also supported), has no authentication, and is not meant to be exposed
to the public internet.

## 📜 License

<!-- aegean:license:start -->
Free for personal use under the Aegean License 1.1 (AL-1.1); see [LICENSE](LICENSE). Any commercial use needs the Aegean Commercial License 1.0 (ACL-1.0); see [COMMERCIAL.md](COMMERCIAL.md).
<!-- aegean:license:end -->
