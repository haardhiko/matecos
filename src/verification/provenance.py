"""
provenance.py
=============
Provenance tracking — records the lineage of data and decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
import ulid

logger = structlog.get_logger(__name__)


@dataclass
class ProvenanceRecord:
    """A single provenance record linking an output to its sources.

    Attributes:
        record_id: Unique identifier (ULID).
        output_id: What was produced (e.g. task result ID).
        output_type: Category of output (task_result, tool_output, etc.).
        source_ids: What contributed to this output.
        source_types: Types of each source.
        agent_id: Which agent produced this output.
        tool_id: Which tool was used (if applicable).
        execution_id: Parent execution.
        created_at: When this record was created.
        metadata: Additional provenance metadata.
    """

    record_id: str = field(default_factory=lambda: ulid.new().str)
    output_id: str = ""
    output_type: str = ""
    source_ids: list[str] = field(default_factory=list)
    source_types: list[str] = field(default_factory=list)
    agent_id: str = ""
    tool_id: str = ""
    execution_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)


class ProvenanceTracker:
    """Tracks data provenance across the execution lifecycle.

    Records which inputs produced which outputs, forming a
    directed lineage graph.
    """

    def __init__(self) -> None:
        self._records: dict[str, ProvenanceRecord] = {}
        self._log = logger.bind(component="ProvenanceTracker")

    def record(
        self,
        output_id: str,
        output_type: str,
        source_ids: list[str],
        agent_id: str = "",
        tool_id: str = "",
        execution_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Record a provenance relationship.

        Args:
            output_id: What was produced.
            output_type: Category of the output.
            source_ids: What contributed to this output.
            agent_id: Which agent produced it.
            tool_id: Which tool was used.
            execution_id: Parent execution.
            metadata: Additional metadata.

        Returns:
            The provenance record ID.
        """
        record = ProvenanceRecord(
            output_id=output_id,
            output_type=output_type,
            source_ids=source_ids,
            agent_id=agent_id,
            tool_id=tool_id,
            execution_id=execution_id,
            metadata=metadata or {},
        )
        self._records[record.record_id] = record

        self._log.debug(
            "provenance.recorded",
            record_id=record.record_id,
            output_id=output_id,
            source_count=len(source_ids),
        )
        return record.record_id

    def get_lineage(self, output_id: str) -> list[ProvenanceRecord]:
        """Get the full lineage chain for an output.

        Traverses backward through source_ids to build the complete
        provenance chain.

        Args:
            output_id: The output to trace.

        Returns:
            List of provenance records from newest to oldest.
        """
        chain: list[ProvenanceRecord] = []
        visited: set[str] = set()
        queue = [output_id]

        while queue:
            current_id = queue.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)

            for record in self._records.values():
                if record.output_id == current_id:
                    chain.append(record)
                    queue.extend(record.source_ids)

        return chain

    def get_by_execution(self, execution_id: str) -> list[ProvenanceRecord]:
        """Get all provenance records for an execution.

        Args:
            execution_id: The execution ULID.

        Returns:
            List of provenance records.
        """
        return [
            r for r in self._records.values()
            if r.execution_id == execution_id
        ]

    @property
    def record_count(self) -> int:
        """Return the total number of provenance records."""
        return len(self._records)
