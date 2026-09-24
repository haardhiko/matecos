"""
test_task_graph.py
==================
Tests for the task graph DAG.
"""

from __future__ import annotations

import pytest

from src.orchestration.task_graph import (
    CyclicDependencyError,
    TaskGraph,
    TaskNode,
)


class TestTaskGraph:
    """Tests for the task graph."""

    def test_add_node(self) -> None:
        graph = TaskGraph()
        node = TaskNode(task_id="t1", role="researcher", objective="Find data")
        graph.add_node(node)
        assert graph.node_count == 1

    def test_duplicate_node_raises(self) -> None:
        graph = TaskGraph()
        graph.add_node(TaskNode(task_id="t1", role="researcher", objective="A"))
        with pytest.raises(ValueError, match="Duplicate"):
            graph.add_node(TaskNode(task_id="t1", role="analyst", objective="B"))

    def test_validate_clean_graph(self) -> None:
        graph = TaskGraph()
        graph.add_node(TaskNode(task_id="t1", role="researcher", objective="A"))
        graph.add_node(TaskNode(
            task_id="t2", role="analyst", objective="B",
            dependencies=["t1"],
        ))
        warnings = graph.validate()
        assert warnings == []

    def test_validate_dangling_dependency(self) -> None:
        graph = TaskGraph()
        graph.add_node(TaskNode(
            task_id="t1", role="researcher", objective="A",
            dependencies=["nonexistent"],
        ))
        with pytest.raises(ValueError, match="unknown task"):
            graph.validate()

    def test_validate_cycle_detected(self) -> None:
        graph = TaskGraph()
        graph.add_node(TaskNode(
            task_id="t1", role="researcher", objective="A",
            dependencies=["t2"],
        ))
        graph.add_node(TaskNode(
            task_id="t2", role="analyst", objective="B",
            dependencies=["t1"],
        ))
        with pytest.raises(CyclicDependencyError):
            graph.validate()

    def test_topological_sort(self) -> None:
        graph = TaskGraph()
        graph.add_node(TaskNode(task_id="t1", role="researcher", objective="A"))
        graph.add_node(TaskNode(
            task_id="t2", role="analyst", objective="B",
            dependencies=["t1"],
        ))
        graph.add_node(TaskNode(
            task_id="t3", role="writer", objective="C",
            dependencies=["t2"],
        ))
        order = graph.topological_sort()
        assert order.index("t1") < order.index("t2")
        assert order.index("t2") < order.index("t3")

    def test_get_ready_tasks(self) -> None:
        graph = TaskGraph()
        graph.add_node(TaskNode(task_id="t1", role="researcher", objective="A"))
        graph.add_node(TaskNode(
            task_id="t2", role="analyst", objective="B",
            dependencies=["t1"],
        ))

        ready = graph.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].task_id == "t1"

        # Complete t1, t2 becomes ready
        graph.mark_completed("t1", {"data": "found"})
        ready = graph.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].task_id == "t2"

    def test_mark_running(self) -> None:
        graph = TaskGraph()
        graph.add_node(TaskNode(task_id="t1", role="researcher", objective="A"))
        graph.mark_running("t1", "agent-123")
        node = graph.get_node("t1")
        assert node is not None
        assert node.status == "RUNNING"
        assert node.assigned_agent_id == "agent-123"

    def test_mark_failed(self) -> None:
        graph = TaskGraph()
        graph.add_node(TaskNode(task_id="t1", role="researcher", objective="A"))
        graph.mark_failed("t1", "Something broke")
        node = graph.get_node("t1")
        assert node is not None
        assert node.status == "FAILED"
        assert node.error == "Something broke"

    def test_is_complete(self) -> None:
        graph = TaskGraph()
        graph.add_node(TaskNode(task_id="t1", role="researcher", objective="A"))
        graph.add_node(TaskNode(task_id="t2", role="analyst", objective="B"))
        assert graph.is_complete is False

        graph.mark_completed("t1", {})
        assert graph.is_complete is False

        graph.mark_completed("t2", {})
        assert graph.is_complete is True

    def test_has_failures(self) -> None:
        graph = TaskGraph()
        graph.add_node(TaskNode(task_id="t1", role="researcher", objective="A"))
        assert graph.has_failures is False

        graph.mark_failed("t1", "Error")
        assert graph.has_failures is True

    def test_from_plan(self) -> None:
        plan = [
            {"task_id": "t1", "role": "researcher", "objective": "A"},
            {"task_id": "t2", "role": "analyst", "objective": "B", "dependencies": ["t1"]},
        ]
        graph = TaskGraph.from_plan(plan)
        assert graph.node_count == 2
        order = graph.topological_sort()
        assert order[0] == "t1"

    def test_to_dict(self) -> None:
        graph = TaskGraph()
        graph.add_node(TaskNode(task_id="t1", role="researcher", objective="A"))
        result = graph.to_dict()
        assert len(result) == 1
        assert result[0]["task_id"] == "t1"
        assert result[0]["role"] == "researcher"

    def test_parallel_ready_tasks(self) -> None:
        graph = TaskGraph()
        graph.add_node(TaskNode(task_id="t1", role="researcher", objective="A"))
        graph.add_node(TaskNode(task_id="t2", role="analyst", objective="B"))
        graph.add_node(TaskNode(
            task_id="t3", role="writer", objective="C",
            dependencies=["t1", "t2"],
        ))
        ready = graph.get_ready_tasks()
        assert len(ready) == 2
        ready_ids = {t.task_id for t in ready}
        assert ready_ids == {"t1", "t2"}
