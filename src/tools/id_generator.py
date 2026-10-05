"""
id_generator.py
===============
Centralized, deterministic Tool ID generation and normalization for MATECOS.

All Tool IDs in MATECOS must satisfy the strict ToolManifest regular expression:
    ^[a-z][a-z0-9]*(\\.[a-z][a-z0-9_]*)+$

Rules enforced:
1. At least two dot-separated segments: root (source) and at least one child.
2. First segment: starts with [a-z], followed by [a-z0-9]* (NO underscores allowed).
3. Subsequent segments: each starts with [a-z], followed by [a-z0-9_]*.
4. No empty segments, no consecutive dots, no leading or trailing dots/underscores.
5. Deterministic fallback generation with stable SHA-256 hashes when components are missing or empty.
6. Optional collision disambiguation with existing IDs.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Sequence

# The canonical strict pattern from ToolManifest
TOOL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9_]*)+$")


def _stable_hash(value: str, length: int = 6) -> str:
    """Generate a short deterministic lowercase alphanumeric hash."""
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:length]


def normalize_segment(raw: str | None, *, is_root: bool = False, fallback: str = "tool") -> str:
    """Normalize an individual segment of a Tool ID.

    Args:
        raw: Raw segment string (can be dirty, containing spaces, unicode, punctuation, etc.).
        is_root: If True, enforces strict ^[a-z][a-z0-9]*$ (no underscores).
                 If False, enforces ^[a-z][a-z0-9_]*$.
        fallback: Fallback name if segment collapses to empty.

    Returns:
        A strictly valid segment string.
    """
    if not raw:
        raw = fallback

    # Normalize unicode to ASCII equivalent if possible (e.g. café -> cafe)
    text = unicodedata.normalize("NFKD", str(raw))
    text = text.encode("ascii", "ignore").decode("ascii").lower().strip()

    if is_root:
        # First segment allows only lowercase letters and numbers; strip all other characters
        text = re.sub(r"[^a-z0-9]", "", text)
        # Must start with a letter
        text = re.sub(r"^[^a-z]+", "", text)
        if not text:
            # Deterministic letter-starting fallback
            h = _stable_hash(str(raw))
            text = f"src{h}"
        return text

    # Non-root segment: replace invalid characters (spaces, hyphens, slashes, punctuation, dots) with underscores
    text = re.sub(r"[^a-z0-9_]", "_", text)

    # Collapse repeated underscores
    text = re.sub(r"_+", "_", text)

    # Strip leading and trailing underscores and non-alphanumerics
    text = text.strip("_")

    # A non-root segment MUST start with [a-z]
    # If it starts with digits, prefix with 't_' (e.g. '123' -> 't_123')
    if text and text[0].isdigit():
        text = f"t_{text}"
    else:
        # Strip any leading characters until an [a-z] is found
        while text and not text[0].isalpha():
            text = text[1:].lstrip("_")

    if not text:
        h = _stable_hash(str(raw))
        text = f"{fallback}_{h}"

    return text


def generate_tool_id(
    source: str | None = "github",
    repository: str | None = None,
    namespace: str | None = None,
    tool_name: str | None = None,
    *,
    existing_ids: set[str] | None = None,
    extra_parts: Sequence[str] | None = None,
) -> str:
    """Generate a normalized, deterministic, strictly valid MATECOS Tool ID.

    Guarantees fullmatch against:
        ^[a-z][a-z0-9]*(\\.[a-z][a-z0-9_]*)+$

    Args:
        source: Root source name (e.g. 'github', 'matecos', 'container'). Default 'github'.
        repository: Repository or project name (e.g. 'bottle', 'flask', 'my-repo').
        namespace: Optional intermediate namespace or category (e.g. 'cli', 'api', 'tools').
        tool_name: The tool or script name (e.g. 'search', 'profile_data', 'actor').
        existing_ids: Set of already-registered IDs for collision avoidance.
        extra_parts: Any additional hierarchical parts.

    Returns:
        A strictly valid, non-colliding Tool ID string.
    """
    raw_parts: list[str] = []

    # 1. Collect candidate parts
    if source is not None:
        raw_parts.append(str(source))
    if repository is not None:
        raw_parts.append(str(repository))
    if namespace is not None:
        raw_parts.append(str(namespace))
    if extra_parts:
        raw_parts.extend(str(p) for p in extra_parts if p is not None)
    if tool_name is not None:
        raw_parts.append(str(tool_name))

    # Split any parts that themselves contain dots or slashes
    split_parts: list[str] = []
    for part in raw_parts:
        for sub in re.split(r"[/\\.]+", str(part)):
            sub_clean = sub.strip()
            if sub_clean:
                split_parts.append(sub_clean)

    # 2. Handle completely empty inputs
    if not split_parts:
        split_parts = ["github", "tool"]

    # 3. Normalize root segment
    root = normalize_segment(split_parts[0], is_root=True, fallback="github")

    # 4. Normalize subsequent segments
    subsequent: list[str] = []
    raw_subsequent = split_parts[1:]
    if not raw_subsequent:
        raw_subsequent = ["tool"]

    for i, p in enumerate(raw_subsequent):
        norm = normalize_segment(p, is_root=False, fallback=f"part_{i}")
        if norm:
            subsequent.append(norm)

    if not subsequent:
        subsequent.append("tool")

    # Combine into candidate ID
    candidate = f"{root}." + ".".join(subsequent)

    # Final guarantee check against strict regex
    if not TOOL_ID_PATTERN.fullmatch(candidate):
        # Emergency deterministic fallback if something unexpected passed through
        h = _stable_hash(f"{source}:{repository}:{namespace}:{tool_name}")
        candidate = f"github.tool_{h}"

    # 5. Collision avoidance if existing_ids provided
    if existing_ids is not None:
        base_id = candidate
        counter = 1
        while candidate in existing_ids:
            candidate = f"{base_id}_{counter}"
            # Ensure the counter addition didn't break pattern (it won't since suffix is _N)
            if not TOOL_ID_PATTERN.fullmatch(candidate):
                candidate = f"{base_id}.v{counter}"
            counter += 1

    return candidate


def is_valid_tool_id(tool_id: str) -> bool:
    """Return True if tool_id strictly matches the canonical pattern."""
    return bool(TOOL_ID_PATTERN.fullmatch(tool_id))
