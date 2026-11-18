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
