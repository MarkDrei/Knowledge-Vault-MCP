# ADR-0007: Hybrid retrieval with RRF, no reranker

Status: accepted (2026-10-03)

**Context.** Keyword search finds exact terms, names and codes; vector search finds paraphrases and cross-language matches. Score scales of BM25 and cosine similarity are not comparable. Cross-encoder rerankers improve quality but cost CPU time and RAM.

**Decision.** Run BM25 (FTS5) and vector KNN separately and fuse with Reciprocal Rank Fusion (`score = Σ 1/(60 + rank)`). No reranker in v1.

**Consequences.** Simple, no score normalisation or tuning needed. Quality is measured with the evaluation set (roadmap step 7); a reranker can be added later behind the same `search` tool.
