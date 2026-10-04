"""Unit tests for fast_json utility."""

from src.utils.fast_json import dumps, loads, get_json_backend


def test_fast_json_roundtrip():
    assert get_json_backend() in ("orjson_rust", "stdlib_json")

    data = {
        "status": "success",
        "count": 42,
        "items": ["apple", "banana", "cherry"],
        "nested": {"valid": True, "ratio": 3.14159},
    }

    serialized = dumps(data)
    assert isinstance(serialized, str)

    deserialized = loads(serialized)
    assert deserialized == data
