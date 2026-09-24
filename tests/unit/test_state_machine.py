"""
tests/unit/test_state_machine.py
=================================
Comprehensive pytest unit-tests for ``src.orchestration.state_machine``.

Coverage targets
----------------
* Every valid transition in ExecutionFSM, AgentFSM, ToolInvocationFSM
* Every invalid (state, trigger) pair raises ``StateTransitionError`` with the
  correct structured fields
* Terminal state detection (``is_terminal``)
* ``allowed_transitions()`` listing
* ``can_transition()`` boolean (with and without trigger)
* ``on_transition`` callback receives a correctly-populated ``TransitionEvent``
* Concurrent ``transition()`` calls on the same instance are serialised (not
  corrupting state)
* ``StateTransitionError`` raised for unknown transitions and for bad triggers
  on known (from, to) pairs

Markers
-------
All tests carry ``pytest.mark.unit`` so they can be selected with
``pytest -m unit``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.orchestration.state_machine import (
    AGENT_TERMINAL_STATES,
    AGENT_TRANSITIONS,
    EXECUTION_TERMINAL_STATES,
    EXECUTION_TRANSITIONS,
    TOOL_TERMINAL_STATES,
    TOOL_INVOCATION_TRANSITIONS,
    AgentStatus,
    ExecutionStatus,
    StateTransitionError,
    StateMachine,
    ToolInvocationStatus,
    TransitionEvent,
    agent_state_machine,
    execution_state_machine,
    tool_invocation_state_machine,
)

# ---------------------------------------------------------------------------
# Pytest configuration
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_exec(
    state: ExecutionStatus = ExecutionStatus.CREATED,
    callback=None,
    eid: str = "exec-1",
) -> StateMachine[ExecutionStatus]:
    """Convenience: execution FSM starting at *state*."""
    return execution_state_machine(eid, state, callback)


def _make_agent(
    state: AgentStatus = AgentStatus.CREATED,
    callback=None,
    aid: str = "agent-1",
) -> StateMachine[AgentStatus]:
    """Convenience: agent FSM starting at *state*."""
    return agent_state_machine(aid, state, callback)


def _make_tool(
    state: ToolInvocationStatus = ToolInvocationStatus.REQUESTED,
    callback=None,
    iid: str = "inv-1",
) -> StateMachine[ToolInvocationStatus]:
    """Convenience: tool-invocation FSM starting at *state*."""
    return tool_invocation_state_machine(iid, state, callback)


# ---------------------------------------------------------------------------
# Parametrised: valid Execution transitions
# ---------------------------------------------------------------------------

_VALID_EXEC_TRANSITIONS: list[tuple[ExecutionStatus, ExecutionStatus, str]] = [
    (ExecutionStatus.CREATED, ExecutionStatus.VALIDATING, "start_validation"),
    (ExecutionStatus.VALIDATING, ExecutionStatus.PLANNING, "validation_passed"),
    (ExecutionStatus.VALIDATING, ExecutionStatus.FAILED, "validation_failed"),
    (ExecutionStatus.PLANNING, ExecutionStatus.AWAITING_APPROVAL, "plan_requires_approval"),
    (ExecutionStatus.PLANNING, ExecutionStatus.RUNNING, "plan_approved_auto"),
    (ExecutionStatus.PLANNING, ExecutionStatus.FAILED, "planning_failed"),
    (ExecutionStatus.AWAITING_APPROVAL, ExecutionStatus.RUNNING, "approved"),
    (ExecutionStatus.AWAITING_APPROVAL, ExecutionStatus.CANCELLED, "rejected"),
    (ExecutionStatus.AWAITING_APPROVAL, ExecutionStatus.CANCELLED, "cancelled"),
    (ExecutionStatus.RUNNING, ExecutionStatus.PARTIALLY_COMPLETED, "partial_completion"),
    (ExecutionStatus.RUNNING, ExecutionStatus.VERIFYING, "all_tasks_complete"),
    (ExecutionStatus.RUNNING, ExecutionStatus.FAILED, "runtime_error"),
    (ExecutionStatus.RUNNING, ExecutionStatus.CANCELLED, "cancelled"),
    (ExecutionStatus.RUNNING, ExecutionStatus.TIMED_OUT, "timeout"),
    (ExecutionStatus.PARTIALLY_COMPLETED, ExecutionStatus.VERIFYING, "verification_started"),
    (ExecutionStatus.PARTIALLY_COMPLETED, ExecutionStatus.FAILED, "irrecoverable_failure"),
    (ExecutionStatus.PARTIALLY_COMPLETED, ExecutionStatus.CANCELLED, "cancelled"),
    (ExecutionStatus.VERIFYING, ExecutionStatus.COMPLETED, "verification_passed"),
    (ExecutionStatus.VERIFYING, ExecutionStatus.FAILED, "verification_failed"),
    (ExecutionStatus.VERIFYING, ExecutionStatus.RUNNING, "reverification_needed"),
]


@pytest.mark.parametrize("from_s,to_s,trigger", _VALID_EXEC_TRANSITIONS)
async def test_execution_valid_transition(
    from_s: ExecutionStatus,
    to_s: ExecutionStatus,
    trigger: str,
) -> None:
    """Every valid execution transition succeeds and updates current_state."""
    sm = _make_exec(state=from_s)
    event = await sm.transition(to_s, trigger)

    assert sm.current_state == to_s
    assert event.from_state == from_s
    assert event.to_state == to_s
    assert event.trigger == trigger
    assert event.entity_type == "execution"
    assert isinstance(event.timestamp, datetime)


# ---------------------------------------------------------------------------
# Parametrised: valid Agent transitions
# ---------------------------------------------------------------------------

_VALID_AGENT_TRANSITIONS: list[tuple[AgentStatus, AgentStatus, str]] = [
    (AgentStatus.CREATED, AgentStatus.READY, "initialized"),
    (AgentStatus.READY, AgentStatus.RUNNING, "task_assigned"),
    (AgentStatus.RUNNING, AgentStatus.WAITING_FOR_DEPENDENCY, "dependency_blocked"),
    (AgentStatus.RUNNING, AgentStatus.WAITING_FOR_APPROVAL, "approval_required"),
    (AgentStatus.RUNNING, AgentStatus.RETRYING, "transient_error"),
    (AgentStatus.RUNNING, AgentStatus.COMPLETED, "task_complete"),
    (AgentStatus.RUNNING, AgentStatus.FAILED, "fatal_error"),
    (AgentStatus.RUNNING, AgentStatus.FAILED, "budget_exhausted"),
    (AgentStatus.RUNNING, AgentStatus.CANCELLED, "cancelled"),
    (AgentStatus.WAITING_FOR_DEPENDENCY, AgentStatus.RUNNING, "dependency_resolved"),
    (AgentStatus.WAITING_FOR_DEPENDENCY, AgentStatus.CANCELLED, "cancelled"),
    (AgentStatus.WAITING_FOR_APPROVAL, AgentStatus.RUNNING, "approved"),
    (AgentStatus.WAITING_FOR_APPROVAL, AgentStatus.CANCELLED, "rejected"),
    (AgentStatus.WAITING_FOR_APPROVAL, AgentStatus.CANCELLED, "cancelled"),
    (AgentStatus.RETRYING, AgentStatus.RUNNING, "retry_ready"),
    (AgentStatus.RETRYING, AgentStatus.FAILED, "max_retries_exceeded"),
]


@pytest.mark.parametrize("from_s,to_s,trigger", _VALID_AGENT_TRANSITIONS)
async def test_agent_valid_transition(
    from_s: AgentStatus,
    to_s: AgentStatus,
    trigger: str,
) -> None:
    """Every valid agent transition succeeds and updates current_state."""
    sm = _make_agent(state=from_s)
    event = await sm.transition(to_s, trigger)

    assert sm.current_state == to_s
    assert event.from_state == from_s
    assert event.to_state == to_s
    assert event.trigger == trigger
    assert event.entity_type == "agent"


# ---------------------------------------------------------------------------
# Parametrised: valid Tool-Invocation transitions
# ---------------------------------------------------------------------------

_VALID_TOOL_TRANSITIONS: list[tuple[ToolInvocationStatus, ToolInvocationStatus, str]] = [
    (ToolInvocationStatus.REQUESTED, ToolInvocationStatus.RISK_CHECKING, "risk_check_started"),
    (ToolInvocationStatus.RISK_CHECKING, ToolInvocationStatus.APPROVED, "risk_approved"),
    (ToolInvocationStatus.RISK_CHECKING, ToolInvocationStatus.BLOCKED, "risk_blocked"),
    (ToolInvocationStatus.RISK_CHECKING, ToolInvocationStatus.BLOCKED, "risk_needs_human"),
    (ToolInvocationStatus.APPROVED, ToolInvocationStatus.RUNNING, "execution_started"),
    (ToolInvocationStatus.RUNNING, ToolInvocationStatus.SUCCEEDED, "execution_complete"),
    (ToolInvocationStatus.RUNNING, ToolInvocationStatus.FAILED, "execution_error"),
    (ToolInvocationStatus.RUNNING, ToolInvocationStatus.TIMED_OUT, "timeout"),
    (ToolInvocationStatus.TIMED_OUT, ToolInvocationStatus.RUNNING, "retry_after_timeout"),
    (ToolInvocationStatus.FAILED, ToolInvocationStatus.RUNNING, "retry"),
]


@pytest.mark.parametrize("from_s,to_s,trigger", _VALID_TOOL_TRANSITIONS)
async def test_tool_valid_transition(
    from_s: ToolInvocationStatus,
    to_s: ToolInvocationStatus,
    trigger: str,
) -> None:
    """Every valid tool-invocation transition succeeds and updates current_state."""
    sm = _make_tool(state=from_s)
    event = await sm.transition(to_s, trigger)

    assert sm.current_state == to_s
    assert event.from_state == from_s
    assert event.to_state == to_s
    assert event.trigger == trigger
    assert event.entity_type == "tool_invocation"


# ---------------------------------------------------------------------------
# Invalid transitions — StateTransitionError must be raised
# ---------------------------------------------------------------------------

_INVALID_EXEC_TRANSITIONS: list[tuple[ExecutionStatus, ExecutionStatus, str]] = [
    # From CREATED, cannot jump to RUNNING
    (ExecutionStatus.CREATED, ExecutionStatus.RUNNING, "plan_approved_auto"),
    # From COMPLETED (terminal), nothing is allowed
    (ExecutionStatus.COMPLETED, ExecutionStatus.RUNNING, "reverification_needed"),
    # From RUNNING, cannot go to CREATED
    (ExecutionStatus.RUNNING, ExecutionStatus.CREATED, "start_validation"),
    # Valid pair but wrong trigger
    (ExecutionStatus.CREATED, ExecutionStatus.VALIDATING, "wrong_trigger"),
    # From FAILED (terminal)
    (ExecutionStatus.FAILED, ExecutionStatus.RUNNING, "restart"),
    # From CANCELLED (terminal)
    (ExecutionStatus.CANCELLED, ExecutionStatus.RUNNING, "approved"),
]


@pytest.mark.parametrize("from_s,to_s,trigger", _INVALID_EXEC_TRANSITIONS)
async def test_execution_invalid_transition_raises(
    from_s: ExecutionStatus,
    to_s: ExecutionStatus,
    trigger: str,
) -> None:
    """Invalid execution transitions raise StateTransitionError with full context."""
    sm = _make_exec(state=from_s)
    with pytest.raises(StateTransitionError) as exc_info:
        await sm.transition(to_s, trigger)

    err = exc_info.value
    assert err.entity_type == "execution"
    assert err.entity_id == "exec-1"
    assert err.from_state == from_s
    assert err.to_state == to_s
    assert err.trigger == trigger
    assert isinstance(err.message, str) and len(err.message) > 0


_INVALID_AGENT_TRANSITIONS: list[tuple[AgentStatus, AgentStatus, str]] = [
    (AgentStatus.CREATED, AgentStatus.RUNNING, "task_assigned"),   # must pass READY
    (AgentStatus.COMPLETED, AgentStatus.RUNNING, "task_assigned"), # terminal
    (AgentStatus.FAILED, AgentStatus.READY, "initialized"),        # terminal
    (AgentStatus.RUNNING, AgentStatus.CREATED, "initialized"),
    (AgentStatus.READY, AgentStatus.RUNNING, "wrong_trigger"),     # bad trigger
]


@pytest.mark.parametrize("from_s,to_s,trigger", _INVALID_AGENT_TRANSITIONS)
async def test_agent_invalid_transition_raises(
    from_s: AgentStatus,
    to_s: AgentStatus,
    trigger: str,
) -> None:
    """Invalid agent transitions raise StateTransitionError with full context."""
    sm = _make_agent(state=from_s)
    with pytest.raises(StateTransitionError) as exc_info:
        await sm.transition(to_s, trigger)

    err = exc_info.value
    assert err.from_state == from_s
    assert err.to_state == to_s
    assert err.trigger == trigger


_INVALID_TOOL_TRANSITIONS: list[tuple[ToolInvocationStatus, ToolInvocationStatus, str]] = [
    (ToolInvocationStatus.REQUESTED, ToolInvocationStatus.RUNNING, "execution_started"),  # skip risk check
    (ToolInvocationStatus.SUCCEEDED, ToolInvocationStatus.RUNNING, "retry"),             # terminal
    (ToolInvocationStatus.BLOCKED, ToolInvocationStatus.RUNNING, "retry"),               # terminal
    (ToolInvocationStatus.RISK_CHECKING, ToolInvocationStatus.APPROVED, "bad_trigger"),  # wrong trigger
    (ToolInvocationStatus.APPROVED, ToolInvocationStatus.SUCCEEDED, "execution_complete"), # skip RUNNING
]


@pytest.mark.parametrize("from_s,to_s,trigger", _INVALID_TOOL_TRANSITIONS)
async def test_tool_invalid_transition_raises(
    from_s: ToolInvocationStatus,
    to_s: ToolInvocationStatus,
    trigger: str,
) -> None:
    """Invalid tool-invocation transitions raise StateTransitionError with full context."""
    sm = _make_tool(state=from_s)
    with pytest.raises(StateTransitionError) as exc_info:
        await sm.transition(to_s, trigger)

    err = exc_info.value
    assert err.from_state == from_s
    assert err.to_state == to_s
    assert err.trigger == trigger


# ---------------------------------------------------------------------------
# Terminal state detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", list(EXECUTION_TERMINAL_STATES))
def test_execution_terminal_states_are_terminal(state: ExecutionStatus) -> None:
    """is_terminal() returns True for all execution terminal states."""
    sm = _make_exec(state=state)
    assert sm.is_terminal() is True


@pytest.mark.parametrize(
    "state",
    [s for s in ExecutionStatus if s not in EXECUTION_TERMINAL_STATES],
)
def test_execution_non_terminal_states_are_not_terminal(state: ExecutionStatus) -> None:
    """is_terminal() returns False for non-terminal execution states."""
    sm = _make_exec(state=state)
    assert sm.is_terminal() is False


@pytest.mark.parametrize("state", list(AGENT_TERMINAL_STATES))
def test_agent_terminal_states_are_terminal(state: AgentStatus) -> None:
    """is_terminal() returns True for all agent terminal states."""
    sm = _make_agent(state=state)
    assert sm.is_terminal() is True


@pytest.mark.parametrize(
    "state",
    [s for s in AgentStatus if s not in AGENT_TERMINAL_STATES],
)
def test_agent_non_terminal_states_are_not_terminal(state: AgentStatus) -> None:
    """is_terminal() returns False for non-terminal agent states."""
    sm = _make_agent(state=state)
    assert sm.is_terminal() is False


@pytest.mark.parametrize("state", list(TOOL_TERMINAL_STATES))
def test_tool_terminal_states_are_terminal(state: ToolInvocationStatus) -> None:
    """is_terminal() returns True for all tool-invocation terminal states."""
    sm = _make_tool(state=state)
    assert sm.is_terminal() is True


@pytest.mark.parametrize(
    "state",
    [s for s in ToolInvocationStatus if s not in TOOL_TERMINAL_STATES],
)
def test_tool_non_terminal_states_are_not_terminal(state: ToolInvocationStatus) -> None:
    """is_terminal() returns False for non-terminal tool-invocation states."""
    sm = _make_tool(state=state)
    assert sm.is_terminal() is False


# ---------------------------------------------------------------------------
# allowed_transitions()
# ---------------------------------------------------------------------------


def test_allowed_transitions_from_created() -> None:
    """CREATED execution state only allows VALIDATING."""
    sm = _make_exec(state=ExecutionStatus.CREATED)
    assert sm.allowed_transitions() == [ExecutionStatus.VALIDATING]


def test_allowed_transitions_from_running_execution() -> None:
    """RUNNING execution state allows five target states."""
    sm = _make_exec(state=ExecutionStatus.RUNNING)
    allowed = set(sm.allowed_transitions())
    expected = {
        ExecutionStatus.PARTIALLY_COMPLETED,
        ExecutionStatus.VERIFYING,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.TIMED_OUT,
    }
    assert allowed == expected


def test_allowed_transitions_from_terminal() -> None:
    """Terminal states return an empty list."""
    sm = _make_exec(state=ExecutionStatus.COMPLETED)
    assert sm.allowed_transitions() == []


def test_allowed_transitions_agent_running() -> None:
    """RUNNING agent can reach multiple states."""
    sm = _make_agent(state=AgentStatus.RUNNING)
    allowed = set(sm.allowed_transitions())
    expected = {
        AgentStatus.WAITING_FOR_DEPENDENCY,
        AgentStatus.WAITING_FOR_APPROVAL,
        AgentStatus.RETRYING,
        AgentStatus.COMPLETED,
        AgentStatus.FAILED,
        AgentStatus.CANCELLED,
    }
    assert allowed == expected


def test_allowed_transitions_tool_risk_checking() -> None:
    """RISK_CHECKING can reach APPROVED or BLOCKED."""
    sm = _make_tool(state=ToolInvocationStatus.RISK_CHECKING)
    # Both APPROVED and BLOCKED are reachable; BLOCKED appears once (dict keys unique)
    allowed = set(sm.allowed_transitions())
    assert ToolInvocationStatus.APPROVED in allowed
    assert ToolInvocationStatus.BLOCKED in allowed


# ---------------------------------------------------------------------------
# can_transition()
# ---------------------------------------------------------------------------


def test_can_transition_true_without_trigger() -> None:
    """can_transition returns True for a valid (from, to) pair, no trigger."""
    sm = _make_exec(state=ExecutionStatus.CREATED)
    assert sm.can_transition(ExecutionStatus.VALIDATING) is True


def test_can_transition_true_with_valid_trigger() -> None:
    """can_transition returns True when trigger matches the allowed list."""
    sm = _make_exec(state=ExecutionStatus.CREATED)
    assert sm.can_transition(ExecutionStatus.VALIDATING, "start_validation") is True


def test_can_transition_false_with_invalid_trigger() -> None:
    """can_transition returns False when trigger is wrong even if pair is valid."""
    sm = _make_exec(state=ExecutionStatus.CREATED)
    assert sm.can_transition(ExecutionStatus.VALIDATING, "wrong_trigger") is False


def test_can_transition_false_unknown_pair() -> None:
    """can_transition returns False for a pair not in the table."""
    sm = _make_exec(state=ExecutionStatus.CREATED)
    assert sm.can_transition(ExecutionStatus.COMPLETED) is False


def test_can_transition_false_from_terminal() -> None:
    """can_transition always returns False from a terminal state."""
    sm = _make_exec(state=ExecutionStatus.COMPLETED)
    for target in ExecutionStatus:
        assert sm.can_transition(target) is False


# ---------------------------------------------------------------------------
# on_transition callback
# ---------------------------------------------------------------------------


async def test_callback_called_on_success() -> None:
    """on_transition callback is called once after a valid transition."""
    received: list[TransitionEvent] = []

    async def cb(event: TransitionEvent) -> None:
        received.append(event)

    sm = _make_exec(state=ExecutionStatus.CREATED, callback=cb)
    await sm.transition(ExecutionStatus.VALIDATING, "start_validation")

    assert len(received) == 1
    evt = received[0]
    assert evt.from_state == ExecutionStatus.CREATED
    assert evt.to_state == ExecutionStatus.VALIDATING
    assert evt.trigger == "start_validation"
    assert evt.entity_type == "execution"
    assert evt.entity_id == "exec-1"
    assert isinstance(evt.timestamp, datetime)
    assert evt.timestamp.tzinfo == timezone.utc


async def test_callback_not_called_on_error() -> None:
    """on_transition callback must NOT be called when transition fails."""
    received: list[TransitionEvent] = []

    async def cb(event: TransitionEvent) -> None:
        received.append(event)

    sm = _make_exec(state=ExecutionStatus.CREATED, callback=cb)
    with pytest.raises(StateTransitionError):
        await sm.transition(ExecutionStatus.COMPLETED, "bad_trigger")

    assert len(received) == 0


async def test_callback_receives_metadata() -> None:
    """on_transition callback event carries the metadata passed to transition()."""
    received: list[TransitionEvent] = []

    async def cb(event: TransitionEvent) -> None:
        received.append(event)

    sm = _make_exec(state=ExecutionStatus.CREATED, callback=cb)
    meta = {"reason": "test", "attempt": 1}
    await sm.transition(ExecutionStatus.VALIDATING, "start_validation", metadata=meta)

    assert received[0].metadata == meta


async def test_callback_called_multiple_times() -> None:
    """Callback is invoked for each successful transition in sequence."""
    call_count = 0

    async def cb(event: TransitionEvent) -> None:
        nonlocal call_count
        call_count += 1

    sm = _make_agent(state=AgentStatus.CREATED, callback=cb)
    await sm.transition(AgentStatus.READY, "initialized")
    await sm.transition(AgentStatus.RUNNING, "task_assigned")

    assert call_count == 2


async def test_callback_as_async_mock() -> None:
    """on_transition works correctly with unittest.mock.AsyncMock."""
    mock_cb = AsyncMock()
    sm = _make_exec(state=ExecutionStatus.CREATED, callback=mock_cb)
    await sm.transition(ExecutionStatus.VALIDATING, "start_validation")

    mock_cb.assert_awaited_once()
    call_args = mock_cb.call_args[0][0]
    assert isinstance(call_args, TransitionEvent)


# ---------------------------------------------------------------------------
# No callback (None) path
# ---------------------------------------------------------------------------


async def test_no_callback_does_not_raise() -> None:
    """Passing callback=None and doing a valid transition must not raise."""
    sm = _make_exec(state=ExecutionStatus.CREATED, callback=None)
    await sm.transition(ExecutionStatus.VALIDATING, "start_validation")
    assert sm.current_state == ExecutionStatus.VALIDATING


# ---------------------------------------------------------------------------
# State does not change on error
# ---------------------------------------------------------------------------


async def test_state_unchanged_after_failed_transition() -> None:
    """current_state must not change if transition() raises StateTransitionError."""
    sm = _make_exec(state=ExecutionStatus.RUNNING)
    with pytest.raises(StateTransitionError):
        await sm.transition(ExecutionStatus.CREATED, "start_validation")
    assert sm.current_state == ExecutionStatus.RUNNING


# ---------------------------------------------------------------------------
# Concurrent transitions (serialisation via asyncio.Lock)
# ---------------------------------------------------------------------------


async def test_concurrent_transitions_are_serialised() -> None:
    """Concurrent callers on the same FSM are serialised; one wins, others fail."""
    sm = execution_state_machine("exec-concurrent", ExecutionStatus.CREATED)
    results: list[bool] = []

    async def try_transition() -> None:
        try:
            await sm.transition(ExecutionStatus.VALIDATING, "start_validation")
            results.append(True)
        except StateTransitionError:
            results.append(False)

    await asyncio.gather(*[try_transition() for _ in range(5)])
    # Exactly one should succeed; the rest see VALIDATING → VALIDATING = invalid
    assert results.count(True) == 1
    assert results.count(False) == 4


# ---------------------------------------------------------------------------
# StateTransitionError attributes and repr
# ---------------------------------------------------------------------------


def test_state_transition_error_fields() -> None:
    """StateTransitionError carries all expected structured attributes."""
    err = StateTransitionError(
        entity_type="execution",
        entity_id="eid-42",
        from_state=ExecutionStatus.CREATED,
        to_state=ExecutionStatus.COMPLETED,
        trigger="bad",
        message="Nope.",
    )
    assert err.entity_type == "execution"
    assert err.entity_id == "eid-42"
    assert err.from_state == ExecutionStatus.CREATED
    assert err.to_state == ExecutionStatus.COMPLETED
    assert err.trigger == "bad"
    assert err.message == "Nope."
    assert str(err) == "Nope."


def test_state_transition_error_repr_contains_fields() -> None:
    """StateTransitionError.__repr__ includes all key attributes."""
    err = StateTransitionError(
        entity_type="agent",
        entity_id="a1",
        from_state=AgentStatus.RUNNING,
        to_state=AgentStatus.CREATED,
        trigger="go_back",
        message="Cannot.",
    )
    r = repr(err)
    assert "agent" in r
    assert "a1" in r
    assert "go_back" in r


# ---------------------------------------------------------------------------
# TransitionEvent immutability
# ---------------------------------------------------------------------------


def test_transition_event_is_frozen() -> None:
    """TransitionEvent is a frozen dataclass — mutation must raise."""
    event = TransitionEvent(
        entity_type="execution",
        entity_id="e1",
        from_state=ExecutionStatus.CREATED,
        to_state=ExecutionStatus.VALIDATING,
        trigger="start_validation",
        timestamp=datetime.now(tz=timezone.utc),
    )
    with pytest.raises((AttributeError, TypeError)):
        event.trigger = "mutated"  # type: ignore[misc]


def test_transition_event_default_metadata() -> None:
    """TransitionEvent metadata defaults to an empty dict."""
    event = TransitionEvent(
        entity_type="agent",
        entity_id="a1",
        from_state=AgentStatus.CREATED,
        to_state=AgentStatus.READY,
        trigger="initialized",
        timestamp=datetime.now(tz=timezone.utc),
    )
    assert event.metadata == {}


# ---------------------------------------------------------------------------
# Factory functions wire entity_type correctly
# ---------------------------------------------------------------------------


def test_execution_factory_entity_type() -> None:
    """execution_state_machine produces a machine with entity_type='execution'."""
    sm = execution_state_machine("eid", ExecutionStatus.CREATED)
    assert sm._entity_type == "execution"  # noqa: SLF001


def test_agent_factory_entity_type() -> None:
    """agent_state_machine produces a machine with entity_type='agent'."""
    sm = agent_state_machine("aid", AgentStatus.CREATED)
    assert sm._entity_type == "agent"  # noqa: SLF001


def test_tool_factory_entity_type() -> None:
    """tool_invocation_state_machine produces a machine with entity_type='tool_invocation'."""
    sm = tool_invocation_state_machine("iid", ToolInvocationStatus.REQUESTED)
    assert sm._entity_type == "tool_invocation"  # noqa: SLF001


# ---------------------------------------------------------------------------
# current_state reflects latest transition
# ---------------------------------------------------------------------------


async def test_current_state_updates_through_chain() -> None:
    """current_state tracks state through a multi-hop chain."""
    sm = _make_exec()
    await sm.transition(ExecutionStatus.VALIDATING, "start_validation")
    assert sm.current_state == ExecutionStatus.VALIDATING
    await sm.transition(ExecutionStatus.PLANNING, "validation_passed")
    assert sm.current_state == ExecutionStatus.PLANNING
    await sm.transition(ExecutionStatus.RUNNING, "plan_approved_auto")
    assert sm.current_state == ExecutionStatus.RUNNING
    await sm.transition(ExecutionStatus.VERIFYING, "all_tasks_complete")
    assert sm.current_state == ExecutionStatus.VERIFYING
    await sm.transition(ExecutionStatus.COMPLETED, "verification_passed")
    assert sm.current_state == ExecutionStatus.COMPLETED
    assert sm.is_terminal() is True


async def test_agent_full_happy_path() -> None:
    """Agent full happy-path: CREATED → READY → RUNNING → COMPLETED."""
    sm = _make_agent()
    await sm.transition(AgentStatus.READY, "initialized")
    await sm.transition(AgentStatus.RUNNING, "task_assigned")
    await sm.transition(AgentStatus.COMPLETED, "task_complete")
    assert sm.current_state == AgentStatus.COMPLETED
    assert sm.is_terminal() is True


async def test_agent_retry_path() -> None:
    """Agent retry path: RUNNING → RETRYING → RUNNING → COMPLETED."""
    sm = _make_agent(state=AgentStatus.RUNNING)
    await sm.transition(AgentStatus.RETRYING, "transient_error")
    await sm.transition(AgentStatus.RUNNING, "retry_ready")
    await sm.transition(AgentStatus.COMPLETED, "task_complete")
    assert sm.current_state == AgentStatus.COMPLETED


async def test_tool_full_happy_path() -> None:
    """Tool full happy-path: REQUESTED → RISK_CHECKING → APPROVED → RUNNING → SUCCEEDED."""
    sm = _make_tool()
    await sm.transition(
        ToolInvocationStatus.RISK_CHECKING, "risk_check_started"
    )
    await sm.transition(ToolInvocationStatus.APPROVED, "risk_approved")
    await sm.transition(ToolInvocationStatus.RUNNING, "execution_started")
    await sm.transition(ToolInvocationStatus.SUCCEEDED, "execution_complete")
    assert sm.current_state == ToolInvocationStatus.SUCCEEDED
    assert sm.is_terminal() is True


async def test_tool_timeout_and_retry_path() -> None:
    """Tool timeout-and-retry: RUNNING → TIMED_OUT → RUNNING → SUCCEEDED."""
    sm = _make_tool(state=ToolInvocationStatus.RUNNING)
    await sm.transition(ToolInvocationStatus.TIMED_OUT, "timeout")
    await sm.transition(ToolInvocationStatus.RUNNING, "retry_after_timeout")
    await sm.transition(ToolInvocationStatus.SUCCEEDED, "execution_complete")
    assert sm.current_state == ToolInvocationStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# Error message content
# ---------------------------------------------------------------------------


async def test_error_message_mentions_trigger() -> None:
    """StateTransitionError message must mention the bad trigger name."""
    sm = _make_exec(state=ExecutionStatus.CREATED)
    with pytest.raises(StateTransitionError) as exc_info:
        await sm.transition(ExecutionStatus.VALIDATING, "obviously_wrong")
    assert "obviously_wrong" in exc_info.value.message


async def test_error_message_mentions_states() -> None:
    """StateTransitionError message must mention both from/to state labels."""
    sm = _make_exec(state=ExecutionStatus.COMPLETED)
    with pytest.raises(StateTransitionError) as exc_info:
        await sm.transition(ExecutionStatus.RUNNING, "reverification_needed")
    assert "COMPLETED" in exc_info.value.message or "completed" in exc_info.value.message.lower()
