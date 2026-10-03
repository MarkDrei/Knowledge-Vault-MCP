# ADR-0002: Single SQLite file for the index

Status: accepted (2026-10-03)

**Context.** We need keyword search, vector search and relational data (notes, chunks, links, tags) for < 10k notes on a small VPS. Options: SQLite (FTS5 + sqlite-vec), PostgreSQL + pgvector, a dedicated vector DB, or Tantivy + a vector library.

**Decision.** One SQLite file `index.db`: FTS5 for BM25, `sqlite-vec` for vectors, plain tables for the rest. The index is derived data and can always be rebuilt from the vault.

**Consequences.** No extra service, transactional updates across text, vectors and links, trivial backup. Vector search is brute force, which is fast enough at this size (10k × ~5 chunks × 384 dims). `sqlite-vec` is pre-1.0. If the vault grows by an order of magnitude, revisit.
