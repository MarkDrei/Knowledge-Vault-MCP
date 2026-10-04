# ADR-0004: Embedding model multilingual-e5-small

Status: accepted, provisional until benchmarked (2026-10-03)

**Context.** Notes are German and English; queries may cross languages. Embeddings must be computed on CPU on a 4–8 GB VPS, with no network calls. Candidates:

| Model | Params | Dims | RAM (fp32) | Notes |
|---|---|---|---|---|
| intfloat/multilingual-e5-small | 118M | 384 | ~0.5 GB | Good multilingual quality for its size, 512-token input |
| intfloat/multilingual-e5-base | 278M | 768 | ~1.1 GB | Better quality, ~2–3× slower |
| BAAI/bge-m3 | 568M | 1024 | ~2.3 GB | Strongest, 8k context, too heavy for a 4 GB VPS next to everything else |

**Decision.** Default to `intfloat/multilingual-e5-small`, configurable via `EMBEDDING_MODEL`. Inputs get the required e5 prefixes: `query: ` for queries, `passage: ` for chunks. Chunks are kept under the 512-token limit.

**Consequences.** Fast full reindex on CPU and small vectors (smaller index, faster brute-force KNN). Some quality is left on the table; BM25 in the hybrid search compensates for exact terms. Changing the model requires a full reindex (dimension changes). Roadmap step 7 benchmarks e5-small vs. e5-base vs. bge-m3 on a real German/English evaluation set; this ADR is superseded if a different model wins clearly.

**Implementation note (2026-10-04).** Runs through `fastembed` (ONNX, no PyTorch) as a custom model with mean pooling and normalization, loading `onnx/model.onnx` from the Hugging Face repo into `MODEL_CACHE_PATH`. The index stores the model name and dimension and rebuilds itself when they change. Not yet benchmarked.
