"""Local text embeddings (ADR-0004).

`FastEmbedEmbedder` runs an ONNX export of the model on CPU via fastembed (no PyTorch).
The model is downloaded once into `MODEL_CACHE_DIR`; afterwards no network is needed.
`HashEmbedder` (`EMBEDDING_MODEL=hash`) has no semantics at all: it exists for tests and
for running without a model download.
"""

import hashlib
import logging
import re
import threading
from pathlib import Path
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

# Models fastembed does not ship a description for. They are registered from their
# Hugging Face repositories, which contain ONNX exports.
_CUSTOM_MODELS = {
    "intfloat/multilingual-e5-small": {"dim": 384, "pooling": "MEAN", "size_in_gb": 0.47},
    "intfloat/multilingual-e5-base": {"dim": 768, "pooling": "MEAN", "size_in_gb": 1.11},
    "BAAI/bge-m3": {
        "dim": 1024,
        "pooling": "CLS",
        "size_in_gb": 2.27,
        "additional_files": ["onnx/model.onnx_data"],
    },
}


class Embedder(Protocol):
    name: str

    def embed_passages(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


def to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


class HashEmbedder:
    """Deterministic bag-of-words hashing. Only for tests and offline development."""

    name = "hash"

    def __init__(self, dim: int = 256):
        self.dim = dim

    def _embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        for token in re.findall(r"\w+", text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dim
            vec[idx] += 1.0 if digest[4] & 1 else -1.0
        norm = np.linalg.norm(vec)
        return vec / norm if norm else vec

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._embed(t) for t in texts]) if texts else np.zeros((0, self.dim), np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed(text)


class FastEmbedEmbedder:
    def __init__(self, name: str, cache_dir: Path, threads: int | None = None, batch_size: int = 32):
        self.name = name
        self.cache_dir = Path(cache_dir)
        self.threads = threads
        self.batch_size = batch_size
        self._model = None
        self._lock = threading.Lock()
        # e5 models are trained with these prefixes and lose quality without them.
        is_e5 = "e5" in name.lower()
        self._query_prefix = "query: " if is_e5 else ""
        self._passage_prefix = "passage: " if is_e5 else ""

    def _load(self):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding

                _register_custom_model(self.name)
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                log.info("loading embedding model %s", self.name)
                self._model = TextEmbedding(self.name, cache_dir=str(self.cache_dir), threads=self.threads)
            return self._model

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), np.float32)
        model = self._load()
        vectors = model.embed([self._passage_prefix + t for t in texts], batch_size=self.batch_size)
        return np.stack(list(vectors)).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        model = self._load()
        return np.asarray(next(iter(model.query_embed(self._query_prefix + text))), dtype=np.float32)


def _register_custom_model(name: str) -> None:
    from fastembed import TextEmbedding
    from fastembed.common.model_description import ModelSource, PoolingType

    spec = _CUSTOM_MODELS.get(name)
    if spec is None or any(m["model"] == name for m in TextEmbedding.list_supported_models()):
        return
    TextEmbedding.add_custom_model(
        model=name,
        pooling=PoolingType[spec["pooling"]],
        normalization=True,
        sources=ModelSource(hf=name),
        dim=spec["dim"],
        model_file="onnx/model.onnx",
        size_in_gb=spec["size_in_gb"],
        additional_files=spec.get("additional_files"),
    )


def make_embedder(name: str, cache_dir: Path) -> Embedder:
    if name == "hash":
        return HashEmbedder()
    return FastEmbedEmbedder(name, cache_dir)
