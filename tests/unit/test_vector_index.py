"""Unit tests for native-accelerated VectorIndex."""

import numpy as np
import pytest

from src.memory.vector_index import VectorIndex, text_to_embedding


def test_text_to_embedding():
    v1 = text_to_embedding("Calculate sum of numbers in python")
    assert isinstance(v1, np.ndarray)
    assert v1.shape == (128,)
    # Should be normalized
    norm = np.linalg.norm(v1)
    assert pytest.approx(norm, rel=1e-3) == 1.0

    # Determinism
    v2 = text_to_embedding("Calculate sum of numbers in python")
    np.testing.assert_allclose(v1, v2)

    # Different text yields different vector
    v3 = text_to_embedding("Http fetch external website")
    assert not np.allclose(v1, v3)


def test_vector_index_add_and_search():
    index = VectorIndex(dim=128)
    assert index.backend in ("native_usearch", "numpy_fallback")

    # Add items
    id1 = index.add_text("Calculate math expression with calculator", metadata={"tool": "math.calculator"})
    id2 = index.add_text("Fetch data from remote URL endpoint", metadata={"tool": "web.http_fetch"})
    id3 = index.add_text("Profile tabular CSV data with statistics", metadata={"tool": "data.csv.profile"})

    assert index.size == 3

    # Search for math
    results = index.search_text("Evaluate math calculation", k=1)
    assert len(results) == 1
    top_key, score, meta = results[0]
    assert top_key == id1
    assert meta["tool"] == "math.calculator"
    assert score > 0.0

    # Search for web
    results_web = index.search_text("Fetch web page content", k=1)
    assert len(results_web) == 1
    assert results_web[0][2]["tool"] == "web.http_fetch"


def test_vector_index_dense_vectors():
    index = VectorIndex(dim=4)
    v1 = [1.0, 0.0, 0.0, 0.0]
    v2 = [0.0, 1.0, 0.0, 0.0]

    k1 = index.add(v1, metadata={"name": "axis_x"})
    k2 = index.add(v2, metadata={"name": "axis_y"})

    matches = index.search([0.9, 0.1, 0.0, 0.0], k=1)
    assert len(matches) == 1
    assert matches[0][0] == k1
    assert matches[0][2]["name"] == "axis_x"


def test_vector_index_clear():
    index = VectorIndex(dim=128)
    index.add_text("test sentence")
    assert index.size == 1
    index.clear()
    assert index.size == 0
    assert index.search_text("test") == []
