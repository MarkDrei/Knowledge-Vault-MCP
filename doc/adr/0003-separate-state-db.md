# ADR-0003: Separate state database

Status: accepted (2026-10-03)

**Context.** OAuth clients and tokens must survive restarts (otherwise Claude mobile has to reconnect after every deploy). The index database is disposable by design.

**Decision.** Keep persistent server state in its own SQLite file, `state.db`. Codes and tokens are stored only as SHA-256 hashes.

**Consequences.** `index.db` can be deleted at any time without logging anyone out. `state.db` is the only file that needs backing up besides the vault remote. A leaked `state.db` does not reveal usable tokens.
