"""
Builtin tool: CSV file profiler.

Tool ID: ``data.csv.profile``

Profiles a CSV file and returns schema information, value distributions,
missing value counts, and quality warnings.  The implementation runs
entirely in-process as a builtin, but applies defence-in-depth security
checks before touching any file bytes.
"""

from __future__ import annotations

import csv
import os
import urllib.parse
from typing import TYPE_CHECKING

import structlog

from src.tools.manifests import (
    ResourceLimits,
    RuntimeConfig,
    SideEffects,
    ToolManifest,
)

if TYPE_CHECKING:
    from src.tools.registry import ToolExecutionContext

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FILE_SIZE_BYTES: int = 100 * 1024 * 1024  # 100 MB hard cap
SECURE_URI_SCHEME: str = "secure"
MAX_SAMPLE_VALUES: int = 5
DEFAULT_SAMPLE_ROWS: int = 1_000

# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------

from pydantic import BaseModel, Field


class ColumnProfile(BaseModel):
    """Per-column profiling result."""

    name: str
    dtype: str
    null_count: int
    null_pct: float
    unique_count: int
    sample_values: list = Field(default_factory=list)  # max MAX_SAMPLE_VALUES
    min: float | str | None = None
    max: float | str | None = None
    mean: float | None = None
    std: float | None = None


class CsvProfileOutput(BaseModel):
    """Full output of the CSV profile tool."""

    columns: list[ColumnProfile]
    row_count: int
    sample_count: int
    quality_warnings: list[str]
    file_size_bytes: int


# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------


def _resolve_secure_uri(file_uri: str) -> str:
    """
    Resolve a ``secure://`` URI to an absolute filesystem path.

    The scheme ``secure://`` signals that the path has been pre-validated by
    the upload subsystem and lives under an allowlisted directory.  We do not
    accept any other scheme.

    Raises:
        ValueError: If the URI scheme is not ``secure`` or the path contains
            path-traversal sequences.
    """
    parsed = urllib.parse.urlparse(file_uri)
    if parsed.scheme != SECURE_URI_SCHEME:
        raise ValueError(
            f"file_uri must use the 'secure://' scheme; got '{parsed.scheme}://'."
            " Raw file paths and other URI schemes are not accepted."
        )

    # Reconstruct path — netloc is treated as the top-level dir name
    raw_path = os.path.normpath(os.path.join("/uploads", parsed.netloc, parsed.path.lstrip("/")))

    # Reject any residual path traversal (should be impossible after normpath, but belt+braces)
    if ".." in raw_path.split(os.sep):
        raise ValueError("Path traversal detected in file_uri.")

    return raw_path


def _check_file_size(path: str) -> int:
    """
    Return file size in bytes, raising ValueError if it exceeds the cap.
    """
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise ValueError(f"Cannot stat file '{path}': {exc}") from exc
    if size > MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"File size {size:,} bytes exceeds maximum {MAX_FILE_SIZE_BYTES:,} bytes (100 MB)."
        )
    return size


# ---------------------------------------------------------------------------
# Profiling logic
# ---------------------------------------------------------------------------


def _infer_dtype(values: list[str]) -> str:
    """
    Infer the dominant dtype of a column from its string values.

    Checks in order: integer, float, boolean, then falls back to string.
    """
    non_null = [v for v in values if v.strip() != ""]
    if not non_null:
        return "empty"

    def _is_int(s: str) -> bool:
        try:
            int(s)
            return True
        except ValueError:
            return False

    def _is_float(s: str) -> bool:
        try:
            float(s)
            return True
        except ValueError:
            return False

    BOOL_VALUES = {"true", "false", "yes", "no", "1", "0", "t", "f"}

    if all(_is_int(v) for v in non_null):
        return "integer"
    if all(_is_float(v) for v in non_null):
        return "float"
    if all(v.strip().lower() in BOOL_VALUES for v in non_null):
        return "boolean"
    return "string"


def _profile_column(name: str, values: list[str]) -> ColumnProfile:
    """Build a :class:`ColumnProfile` for a single CSV column."""
    total = len(values)
    null_count = sum(1 for v in values if v.strip() == "")
    null_pct = (null_count / total * 100) if total else 0.0
    non_null = [v for v in values if v.strip() != ""]
    unique_count = len(set(values))

    # Sample up to MAX_SAMPLE_VALUES distinct non-null values
    seen: list = []
    seen_set: set = set()
    for v in non_null:
        if v not in seen_set:
            seen.append(v)
            seen_set.add(v)
        if len(seen) >= MAX_SAMPLE_VALUES:
            break

    dtype = _infer_dtype(values)

    col_min: float | str | None = None
    col_max: float | str | None = None
    col_mean: float | None = None
    col_std: float | None = None

    if dtype in ("integer", "float") and non_null:
        try:
            nums = [float(v) for v in non_null]
            col_min = min(nums)
            col_max = max(nums)
            col_mean = sum(nums) / len(nums)
            variance = sum((x - col_mean) ** 2 for x in nums) / len(nums)
            col_std = variance**0.5
        except ValueError:
            pass
    elif dtype == "string" and non_null:
        sorted_vals = sorted(non_null)
        col_min = sorted_vals[0]
        col_max = sorted_vals[-1]

    return ColumnProfile(
        name=name,
        dtype=dtype,
        null_count=null_count,
        null_pct=round(null_pct, 4),
        unique_count=unique_count,
        sample_values=seen,
        min=col_min,
        max=col_max,
        mean=round(col_mean, 6) if col_mean is not None else None,
        std=round(col_std, 6) if col_std is not None else None,
    )


def _generate_quality_warnings(
    columns: list[ColumnProfile],
    row_count: int,
    sample_count: int,
) -> list[str]:
    """Generate human-readable quality warnings from profiling results."""
    warnings: list[str] = []

    if sample_count < row_count:
        warnings.append(
            f"File was sampled ({sample_count:,} of {row_count:,} rows); "
            "statistics may not be fully representative."
        )

    for col in columns:
        if col.null_pct > 50.0:
            warnings.append(f"Column '{col.name}' has {col.null_pct:.1f}% missing values.")
        if col.dtype == "empty":
            warnings.append(f"Column '{col.name}' contains only empty values.")
        if col.unique_count == 1 and col.null_count == 0:
            warnings.append(
                f"Column '{col.name}' has only one unique value (possible constant column)."
            )

    return warnings


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def csv_profile_handler(
    payload: dict,
    context: ToolExecutionContext,
) -> dict:
    """
    Profile a CSV file and return schema, distributions, missing values, and quality warnings.

    Input payload keys:
    - ``file_uri`` (str, required): A ``secure://`` URI pointing to the file.
    - ``sample_rows`` (int, optional, default 1000): Maximum rows to sample.
    - ``include_distributions`` (bool, optional, default True): Unused in builtin mode
      (distributions are always computed); reserved for future sandbox variant.

    Security constraints applied:
    1. ``file_uri`` must use ``secure://`` scheme — no raw paths accepted.
    2. File contents are parsed as CSV only — never executed.
    3. File size is capped at 100 MB before reading.
    4. All string operations use bounded memory via ``sample_rows``.

    Returns a dict matching :class:`CsvProfileOutput`.
    """
    log = logger.bind(
        tool_id="data.csv.profile",
        execution_id=context.execution_id,
        agent_id=context.agent_id,
    )

    file_uri: str = payload["file_uri"]
    sample_rows: int = int(payload.get("sample_rows", DEFAULT_SAMPLE_ROWS))
    sample_rows = max(1, min(sample_rows, 100_000))  # clamp

    log.info("csv_profile.start", file_uri=file_uri, sample_rows=sample_rows)

    # Security: resolve URI — raises ValueError for untrusted schemes
    resolved_path = _resolve_secure_uri(file_uri)

    # Security: check file size before reading
    file_size = _check_file_size(resolved_path)

    # Read CSV using stdlib csv module (pandas not available in builtin context)
    try:
        with open(resolved_path, encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames is None:
                raise ValueError("CSV file has no headers or is empty.")

            headers: list[str] = list(reader.fieldnames)
            column_data: dict[str, list[str]] = {h: [] for h in headers}

            row_count = 0
            sample_count = 0
            for row in reader:
                row_count += 1
                if sample_count < sample_rows:
                    for h in headers:
                        column_data[h].append(row.get(h, "") or "")
                    sample_count += 1

    except (OSError, csv.Error) as exc:
        raise ValueError(f"Failed to read CSV file: {exc}") from exc

    # Profile each column
    columns = [_profile_column(name, column_data[name]) for name in headers]

    quality_warnings = _generate_quality_warnings(columns, row_count, sample_count)

    output = CsvProfileOutput(
        columns=columns,
        row_count=row_count,
        sample_count=sample_count,
        quality_warnings=quality_warnings,
        file_size_bytes=file_size,
    )

    log.info(
        "csv_profile.complete",
        row_count=row_count,
        column_count=len(columns),
        warnings=len(quality_warnings),
    )
    return output.model_dump()


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def get_manifest() -> ToolManifest:
    """Return the :class:`ToolManifest` for the ``data.csv.profile`` tool."""
    return ToolManifest(
        tool_id="data.csv.profile",
        name="CSV Profile Tool",
        version="1.0.0",
        description=(
            "Profiles a CSV file and returns column schema, missing value counts, "
            "value distributions, and data quality warnings."
        ),
        capabilities=["data_analysis", "csv", "profiling"],
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["file_uri"],
            "additionalProperties": False,
            "properties": {
                "file_uri": {
                    "type": "string",
                    "description": "A secure:// URI pointing to the uploaded CSV file.",
                    "pattern": "^secure://",
                },
                "sample_rows": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100000,
                    "default": 1000,
                    "description": "Maximum number of rows to sample for profiling.",
                },
                "include_distributions": {
                    "type": "boolean",
                    "default": True,
                    "description": "Whether to include value distribution statistics.",
                },
            },
        },
        output_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": [
                "columns",
                "row_count",
                "sample_count",
                "quality_warnings",
                "file_size_bytes",
            ],
            "properties": {
                "columns": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "name",
                            "dtype",
                            "null_count",
                            "null_pct",
                            "unique_count",
                            "sample_values",
                        ],
                        "properties": {
                            "name": {"type": "string"},
                            "dtype": {
                                "type": "string",
                                "enum": ["integer", "float", "boolean", "string", "empty"],
                            },
                            "null_count": {"type": "integer", "minimum": 0},
                            "null_pct": {"type": "number", "minimum": 0, "maximum": 100},
                            "unique_count": {"type": "integer", "minimum": 0},
                            "sample_values": {"type": "array"},
                            "min": {},
                            "max": {},
                            "mean": {"type": ["number", "null"]},
                            "std": {"type": ["number", "null"]},
                        },
                    },
                },
                "row_count": {"type": "integer", "minimum": 0},
                "sample_count": {"type": "integer", "minimum": 0},
                "quality_warnings": {"type": "array", "items": {"type": "string"}},
                "file_size_bytes": {"type": "integer", "minimum": 0},
            },
        },
        risk_level="low",
        side_effects=SideEffects.READ_ONLY,
        permissions=["read:uploaded_file"],
        runtime=RuntimeConfig(type="builtin"),
        limits=ResourceLimits(
            timeout_seconds=120,
            max_memory_mb=1024,
            max_output_kb=512,
        ),
    )


CSV_PROFILE_MANIFEST = get_manifest()
profile_csv = csv_profile_handler
