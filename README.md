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

**Canonical Regression Shield Gate**:
```
python -m unittest discover -s tests
```

- **Project gate from the workspace root**: `python aikit.py check postbox --allow-dirty`
- **Start the inbox**: `python -m postbox.cli serve --port 8787`
- **List captures**: `python -m postbox.cli --file inbox.jsonl list`
- **Inspect one**: `python -m postbox.cli --file inbox.jsonl show <id>`
- **Replay one**: `python -m postbox.cli --file inbox.jsonl replay <id> --url http://127.0.0.1:3000/hook`
- **Clear**: `python -m postbox.cli --file inbox.jsonl clear`
- **Agent-friendly output**: `python -m postbox.cli --file inbox.jsonl --json list --method POST --path /hook`

## What's Inside

The workspace is the project root; every path below is relative to it.

```text
postbox/
├── .aikit/                   <- project memory: guidelines, decisions, ledger, map
│   ├── project.json          <- project identity and test command
│   ├── guidelines.md         <- project rules
│   ├── decisions.md          <- ADR register
│   ├── task_ledger.json      <- delivery ledger
│   ├── media.json            <- reviewed brand media state
│   └── codebase_map.md       <- generated AST symbol catalog
├── .gitignore                <- transient AIKit state excluded
├── assets/                   <- generated brand candidates (pending review)
│   ├── logo.svg              <- project mark
│   └── social-preview.svg    <- social card
├── COMMERCIAL.md             <- commercial licensing tiers
├── LICENSE                   <- Aegean License 1.1
├── LICENSES/                 <- pinned license text for REUSE
│   └── LicenseRef-AL-1.1.txt  <- AL-1.1 canonical text
├── REUSE.toml                <- REUSE aggregate license annotations
├── postbox/                  <- package
│   ├── __init__.py           <- version marker
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
| any path, any method | capture the request and return `{"ok": true, "id": ...}` |
| `GET /__inbox` | all captured requests as JSON |
| `GET /__health` | readiness and current request count |
| `GET /__clear` | empty the inbox |

Bodies above `--max-body` bytes (default 1 MiB) get a `413` and are not stored.
`serve --max N` keeps only the newest N requests by rewriting the file atomically.

## Replay

Replay strips hop-by-hop headers (`host`, `content-length`, `connection`,
`transfer-encoding`, `accept-encoding`) and uses the captured method and body.
It is a development tool: it binds to `127.0.0.1` by default, has no
authentication, and is not meant to be exposed to the public internet.

## 📜 License

<!-- aegean:license:start -->
Free for personal use under the Aegean License 1.1 (AL-1.1); see [LICENSE](LICENSE). Any commercial use needs the Aegean Commercial License 1.0 (ACL-1.0); see [COMMERCIAL.md](COMMERCIAL.md).
<!-- aegean:license:end -->
