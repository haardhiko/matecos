"""
task_graph.py
=============
Directed acyclic graph (DAG) representation for task decomposition.

The ``TaskGraph`` is the central data structure produced by the planner
and consumed by the scheduler.  It enforces DAG invariants (no cycles)
and provides topological ordering for execution.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


class CyclicDependencyError(Exception):
    """Raised when a cycle is detected in the task graph."""


@dataclass
class TaskNode:
    """A single node in the task graph.

    Attributes:
        task_id: Unique identifier for this task.
        role: Agent role that should execute this task.
        objective: What this task should accomplish.
        dependencies: List of task_ids this task depends on.
        acceptance_criteria: Criteria the output must satisfy.
        status: Current status (PENDING, RUNNING, COMPLETED, FAILED, SKIPPED).
        assigned_agent_id: ULID of the agent executing this task.
        result: Output of the completed task.
        error: Error message if the task failed.
    """

    task_id: str
    role: str
    objective: str
    dependencies: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    status: str = "PENDING"
    assigned_agent_id: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskGraph:
    """Directed acyclic graph of tasks with topological ordering.

    Usage::

        graph = TaskGraph()
        graph.add_node(TaskNode(task_id="t1", role="researcher", objective="..."))
        graph.add_node(TaskNode(task_id="t2", role="analyst", objective="...",
                                dependencies=["t1"]))
        graph.validate()
        order = graph.topological_sort()
        ready = graph.get_ready_tasks()
    """

    def __init__(self) -> None:
        self._nodes: dict[str, TaskNode] = {}

    def add_node(self, node: TaskNode) -> None:
        """Add a task node to the graph.

        Args:
            node: The task node to add.

        Raises:
            ValueError: If a node with the same task_id already exists.
        """
        if node.task_id in self._nodes:
            raise ValueError(f"Duplicate task_id: '{node.task_id}'")
        self._nodes[node.task_id] = node

    def get_node(self, task_id: str) -> TaskNode | None:
        """Retrieve a node by task_id."""
        return self._nodes.get(task_id)

    @property
    def nodes(self) -> list[TaskNode]:
        """Return all nodes in insertion order."""
        return list(self._nodes.values())

    @property
    def node_count(self) -> int:
        """Return the number of nodes."""
        return len(self._nodes)

    def validate(self) -> list[str]:
        """Validate the graph structure.

        Checks:
        1. No dangling dependency references.
        2. No cycles (via Kahn's algorithm).

        Returns:
            List of warning messages (empty if clean).

        Raises:
            CyclicDependencyError: If a cycle is detected.
            ValueError: If a dependency references a non-existent task.
        """
        warnings: list[str] = []

        # Check dangling references
        for node in self._nodes.values():
            for dep_id in node.dependencies:
                if dep_id not in self._nodes:
                    raise ValueError(
                        f"Task '{node.task_id}' depends on unknown task '{dep_id}'"
                    )

        # Cycle detection via Kahn's algorithm
        in_degree: dict[str, int] = {tid: 0 for tid in self._nodes}
        for node in self._nodes.values():
            for dep_id in node.dependencies:
                in_degree[node.task_id] += 0  # just ensure key exists
                # dep_id -> node.task_id (node depends on dep_id)

        # Build adjacency list: edge from A -> B means B depends on A
        adj: dict[str, list[str]] = {tid: [] for tid in self._nodes}
        for node in self._nodes.values():
            for dep_id in node.dependencies:
                adj[dep_id].append(node.task_id)

        in_degree = {tid: 0 for tid in self._nodes}
        for node in self._nodes.values():
            in_degree[node.task_id] = len(node.dependencies)

        queue: deque[str] = deque()
        for tid, deg in in_degree.items():
            if deg == 0:
                queue.append(tid)

        visited_count = 0
        while queue:
            current = queue.popleft()
            visited_count += 1
            for successor in adj[current]:
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    queue.append(successor)

        if visited_count != len(self._nodes):
            raise CyclicDependencyError(
                f"Cycle detected in task graph: processed {visited_count} of "
                f"{len(self._nodes)} tasks"
            )

        return warnings

    def topological_sort(self) -> list[str]:
        """Return task_ids in a valid execution order.

        Raises:
            CyclicDependencyError: If the graph contains a cycle.
        """
        self.validate()

        adj: dict[str, list[str]] = {tid: [] for tid in self._nodes}
        in_degree: dict[str, int] = {tid: 0 for tid in self._nodes}

        for node in self._nodes.values():
            in_degree[node.task_id] = len(node.dependencies)
            for dep_id in node.dependencies:
                adj[dep_id].append(node.task_id)

        queue: deque[str] = deque()
        for tid, deg in in_degree.items():
            if deg == 0:
                queue.append(tid)

        order: list[str] = []
        while queue:
            current = queue.popleft()
            order.append(current)
            for successor in adj[current]:
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    queue.append(successor)

        return order

    def get_ready_tasks(self) -> list[TaskNode]:
        """Return tasks whose dependencies are all completed and that are PENDING.

        Returns:
            List of ``TaskNode`` objects ready for execution.
        """
        ready: list[TaskNode] = []
        for node in self._nodes.values():
            if node.status != "PENDING":
                continue

            all_deps_met = all(
                self._nodes[dep_id].status == "COMPLETED"
                for dep_id in node.dependencies
                if dep_id in self._nodes
            )
            if all_deps_met:
                ready.append(node)

        return ready

    def mark_completed(self, task_id: str, result: dict[str, Any]) -> None:
        """Mark a task as completed with its result.

        Args:
            task_id: The task identifier.
            result: The task output.

        Raises:
            KeyError: If no task with the given ID exists.
        """
        node = self._nodes.get(task_id)
        if not node:
            raise KeyError(f"Unknown task_id: '{task_id}'")
        node.status = "COMPLETED"
        node.result = result

    def mark_failed(self, task_id: str, error: str) -> None:
        """Mark a task as failed with an error message.

        Args:
            task_id: The task identifier.
            error: The error message.

        Raises:
            KeyError: If no task with the given ID exists.
        """
        node = self._nodes.get(task_id)
        if not node:
            raise KeyError(f"Unknown task_id: '{task_id}'")
        node.status = "FAILED"
        node.error = error

    def mark_running(self, task_id: str, agent_id: str) -> None:
        """Mark a task as running with the assigned agent.

        Args:
            task_id: The task identifier.
            agent_id: ULID of the executing agent.

        Raises:
            KeyError: If no task with the given ID exists.
        """
        node = self._nodes.get(task_id)
        if not node:
            raise KeyError(f"Unknown task_id: '{task_id}'")
        node.status = "RUNNING"
        node.assigned_agent_id = agent_id

    @property
    def is_complete(self) -> bool:
        """Check if all tasks are in a terminal state (COMPLETED, FAILED, SKIPPED)."""
        terminal = {"COMPLETED", "FAILED", "SKIPPED"}
        return all(n.status in terminal for n in self._nodes.values())

    @property
    def has_failures(self) -> bool:
        """Check if any task has FAILED status."""
        return any(n.status == "FAILED" for n in self._nodes.values())

    def to_dict(self) -> list[dict[str, Any]]:
        """Serialise the graph to a list of dicts for API responses."""
        return [
            {
                "task_id": n.task_id,
                "role": n.role,
                "objective": n.objective,
                "dependencies": n.dependencies,
                "acceptance_criteria": n.acceptance_criteria,
                "status": n.status,
                "assigned_agent_id": n.assigned_agent_id,
                "error": n.error,
            }
            for n in self._nodes.values()
        ]

    @classmethod
    def from_plan(cls, task_list: list[dict[str, Any]]) -> "TaskGraph":
        """Build a TaskGraph from the planner's output.

        Args:
            task_list: List of task dicts (as produced by PLANNER_SYSTEM).

        Returns:
            A validated ``TaskGraph``.
        """
        graph = cls()
        for item in task_list:
            node = TaskNode(
                task_id=item["task_id"],
                role=item["role"],
                objective=item["objective"],
                dependencies=item.get("dependencies", []),
                acceptance_criteria=item.get("acceptance_criteria", []),
            )
            graph.add_node(node)

        graph.validate()
        return graph
