"""
vector_index.py
===============
Native-accelerated SIMD vector search engine for capability discovery and episodic memory.

Uses `usearch` (Native C++ HNSW / SIMD AVX-512 / AVX2 / ARM NEON) when available,
with automatic graceful fallback to NumPy / Python cosine similarity.
"""

from __future__ import annotations

import hashlib
import struct
from typing import Any, Sequence

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

# Try importing native SIMD usearch engine
try:
    import usearch.index
    from usearch.index import Index as UsearchIndex

    _USEARCH_AVAILABLE = True
except ImportError:
    _USEARCH_AVAILABLE = False


def text_to_embedding(text: str, dim: int = 128) -> np.ndarray:
    """Generate a deterministic, normalized n-dimensional embedding vector from text.

    Uses subword character n-grams and token feature hashing to project vocabulary
    into a dense unit hypersphere without external network calls.

    Args:
        text: Input string.
        dim: Dimensionality of the resulting vector (default 128).

    Returns:
        L2-normalized float32 numpy vector of shape (dim,).
    """
    vec = np.zeros(dim, dtype=np.float32)
    tokens = text.lower().split()
    if not tokens:
        vec[0] = 1.0
        return vec

    for tok in tokens:
        # Full token hash
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        bucket = struct.unpack("<I", h[:4])[0] % dim
        vec[bucket] += 2.0

        # Subword character 3-grams
        tok_clean = "".join(c for c in tok if c.isalnum())
        for i in range(len(tok_clean) - 2):
            ngram = tok_clean[i : i + 3]
            hn = hashlib.sha256(ngram.encode("utf-8")).digest()
            b = struct.unpack("<I", hn[:4])[0] % dim
            vec[b] += 0.5

    # L2 normalize
    norm = np.linalg.norm(vec)
    if norm > 1e-9:
        vec /= norm
    else:
        vec[0] = 1.0
    return vec


class VectorIndex:
    """High-performance vector index supporting both dense vector and semantic text search.

    Parameters
    ----------
    dim:
        Vector dimensions (default 128).
    metric:
        Distance metric ('cos' for cosine distance or 'ip' for inner product).
    """

    def __init__(self, dim: int = 128, metric: str = "cos") -> None:
        self.dim = dim
        self.metric = metric
        self._log = logger.bind(component="VectorIndex")
        self._key_to_meta: dict[int, dict[str, Any]] = {}
        self._next_key: int = 1

        if _USEARCH_AVAILABLE:
            self._backend = "native_usearch"
            self._index: Any = UsearchIndex(ndim=dim, metric=metric)
            self._log.debug("vector_index.initialized", backend="native_usearch_simd", dim=dim)
        else:
            self._backend = "numpy_fallback"
            self._vectors: dict[int, np.ndarray] = {}
            self._log.debug("vector_index.initialized", backend="numpy_fallback", dim=dim)

    @property
    def backend(self) -> str:
        """Return the active acceleration backend ('native_usearch' or 'numpy_fallback')."""
        return self._backend

    @property
    def size(self) -> int:
        """Number of items in the index."""
        return len(self._key_to_meta)

    def add(
        self,
        vector: np.ndarray | Sequence[float],
        metadata: dict[str, Any] | None = None,
        key: int | None = None,
    ) -> int:
        """Add a dense vector to the index.

        Args:
            vector: Float array of shape (dim,).
            metadata: Associated metadata dictionary.
            key: Optional positive integer identifier.

        Returns:
            The integer key assigned to this vector.
        """
        if key is None:
            key = self._next_key
            self._next_key += 1

        vec_np = np.asarray(vector, dtype=np.float32)
        if vec_np.shape != (self.dim,):
            raise ValueError(f"Expected vector shape ({self.dim},), got {vec_np.shape}")

        # Ensure normalized for cosine metric
        norm = np.linalg.norm(vec_np)
        if norm > 1e-9:
            vec_np = vec_np / norm

        self._key_to_meta[key] = metadata or {}

        if self._backend == "native_usearch":
            self._index.add(key, vec_np)
        else:
            self._vectors[key] = vec_np

        return key

    def add_text(self, text: str, metadata: dict[str, Any] | None = None, key: int | None = None) -> int:
        """Embed and index a text string."""
        meta = metadata.copy() if metadata else {}
        meta["text"] = text
        vec = text_to_embedding(text, dim=self.dim)
        return self.add(vec, metadata=meta, key=key)

    def search(
        self,
        query_vector: np.ndarray | Sequence[float],
        k: int = 5,
    ) -> list[tuple[int, float, dict[str, Any]]]:
        """Search the top-k nearest neighbors for a query vector.

        Returns:
            List of (key, similarity_score, metadata) ordered by highest similarity.
        """
        if not self._key_to_meta:
            return []

        vec_np = np.asarray(query_vector, dtype=np.float32)
        norm = np.linalg.norm(vec_np)
        if norm > 1e-9:
            vec_np = vec_np / norm

        k = min(k, len(self._key_to_meta))

        if self._backend == "native_usearch":
            matches = self._index.search(vec_np, k)
            results = []
            for key, dist in zip(matches.keys, matches.distances):
                # In usearch cosine metric: dist = 1 - cos_sim -> similarity = 1 - dist
                similarity = float(max(0.0, 1.0 - dist))
                int_key = int(key)
                meta = self._key_to_meta.get(int_key, {})
                results.append((int_key, similarity, meta))
            return results
        else:
            # NumPy fallback
            scores = []
            for k_id, v in self._vectors.items():
                sim = float(np.dot(vec_np, v))
                scores.append((k_id, sim, self._key_to_meta.get(k_id, {})))
            scores.sort(key=lambda x: x[1], reverse=True)
            return scores[:k]

    def search_text(self, query_text: str, k: int = 5) -> list[tuple[int, float, dict[str, Any]]]:
        """Embed a query string and return top-k semantic matches."""
        query_vec = text_to_embedding(query_text, dim=self.dim)
        return self.search(query_vec, k=k)

    def clear(self) -> None:
        """Clear all entries from the index."""
        self._key_to_meta.clear()
        self._next_key = 1
        if self._backend == "native_usearch":
            self._index = UsearchIndex(ndim=self.dim, metric=self.metric)
        else:
            self._vectors.clear()
