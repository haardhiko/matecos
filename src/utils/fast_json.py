"""
fast_json.py
============
High-speed JSON serialization using Rust-based `orjson` with standard library fallback.
"""

from __future__ import annotations

from typing import Any

try:
    import orjson

    def dumps(obj: Any, default: Any = None) -> str:
        """Serialize obj to a JSON formatted string using orjson."""
        return orjson.dumps(obj, default=default).decode("utf-8")

    def loads(s: str | bytes) -> Any:
        """Deserialize s (a str or bytes instance) to a Python object using orjson."""
        return orjson.loads(s)

    _BACKEND = "orjson_rust"

except ImportError:
    import json

    def dumps(obj: Any, default: Any = None) -> str:
        """Serialize obj to a JSON formatted string using stdlib json."""
        return json.dumps(obj, default=default)

    def loads(s: str | bytes) -> Any:
        """Deserialize s to a Python object using stdlib json."""
        return json.loads(s)

    _BACKEND = "stdlib_json"


def get_json_backend() -> str:
    """Return the active JSON serialization engine."""
    return _BACKEND
