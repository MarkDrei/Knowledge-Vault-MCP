# ADR-0009: ONNX embeddings via fastembed

Status: accepted (2026-10-04)

**Context.** ADR-0004 picks the embedding model but not how it runs. Options: `sentence-transformers` (PyTorch, ~1–2 GB of wheels, high RAM), ONNX Runtime with our own tokenizer/pooling code, or `fastembed` (ONNX Runtime plus tokenizers, model download and pooling built in).

**Decision.** Run embeddings with `fastembed` on ONNX Runtime, CPU only. Models fastembed does not describe itself (`intfloat/multilingual-e5-small`, `-e5-base`, `BAAI/bge-m3`) are registered from the ONNX export in their Hugging Face repository (mean pooling for e5, CLS for bge-m3, L2-normalised). The e5 prefixes `query: ` / `passage: ` are added by our code. Vectors are stored as float32 blobs and compared with sqlite-vec's `vec_distance_cosine`. `EMBEDDING_MODEL=hash` selects a deterministic, meaning-free hashing embedder for tests and offline development.

**Consequences.** Small image, no PyTorch, low RAM; the model is downloaded once into `MODEL_CACHE_DIR` and then used offline. We depend on the upstream repositories keeping their ONNX exports at `onnx/model.onnx`; if one disappears, the model can be pinned by copying it into the cache directory. The custom registration of e5-base and bge-m3 is only exercised by the benchmark (roadmap step 7).
