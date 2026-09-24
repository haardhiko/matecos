"""
state_machine.py
================
Three finite state machines for MATECOS orchestration:

  * **ExecutionFSM** — lifecycle of a top-level execution request
  * **AgentFSM**     — lifecycle of a single agent within an execution
  * **ToolInvocationFSM** — lifecycle of a single tool-call attempt

Design notes
------------
* Pure Python, zero I/O — fully unit-testable without a database or event loop
  side-effects beyond the optional ``on_transition`` async callback.
* Generic ``StateMachine[S]`` works with any ``str``-based enum.
* Transitions are validated against an explicit allow-list; any attempt to move
  to an unlisted (from, to) pair raises ``StateTransitionError``.
* The ``trigger`` string must appear in the allowed-triggers list for that pair,
  preventing callers from using arbitrary strings to force transitions.
* ``on_transition`` is fire-and-forget from the caller's perspective; the
  ``transition()`` coroutine awaits it so errors propagate naturally.
* Concurrent calls to ``transition()`` on the *same* instance are serialised
  with ``asyncio.Lock`` to prevent race conditions.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Generic, TypeVar

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Status enumerations
# ---------------------------------------------------------------------------


class ExecutionStatus(str, Enum):
    """Lifecycle states for a top-level execution request."""

    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    PLANNING = "PLANNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    RUNNING = "RUNNING"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"


class AgentStatus(str, Enum):
    """Lifecycle states for a single agent within an execution."""

    CREATED = "CREATED"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_FOR_DEPENDENCY = "WAITING_FOR_DEPENDENCY"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    RETRYING = "RETRYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ToolInvocationStatus(str, Enum):
    """Lifecycle states for a single tool-call attempt."""

    REQUESTED = "REQUESTED"
    RISK_CHECKING = "RISK_CHECKING"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    BLOCKED = "BLOCKED"


# ---------------------------------------------------------------------------
# Transition tables
# ---------------------------------------------------------------------------

#: Allowed (from_state, to_state) → [trigger_names] for ExecutionFSM.
EXECUTION_TRANSITIONS: dict[tuple[ExecutionStatus, ExecutionStatus], list[str]] = {
    (ExecutionStatus.CREATED, ExecutionStatus.VALIDATING): ["start_validation"],
    (ExecutionStatus.VALIDATING, ExecutionStatus.PLANNING): ["validation_passed"],
    (ExecutionStatus.VALIDATING, ExecutionStatus.FAILED): ["validation_failed"],
    (ExecutionStatus.PLANNING, ExecutionStatus.AWAITING_APPROVAL): ["plan_requires_approval"],
    (ExecutionStatus.PLANNING, ExecutionStatus.RUNNING): ["plan_approved_auto"],
    (ExecutionStatus.PLANNING, ExecutionStatus.FAILED): ["planning_failed"],
    (ExecutionStatus.AWAITING_APPROVAL, ExecutionStatus.RUNNING): ["approved"],
    (ExecutionStatus.AWAITING_APPROVAL, ExecutionStatus.CANCELLED): ["rejected", "cancelled"],
    (ExecutionStatus.RUNNING, ExecutionStatus.PARTIALLY_COMPLETED): ["partial_completion"],
    (ExecutionStatus.RUNNING, ExecutionStatus.VERIFYING): ["all_tasks_complete"],
    (ExecutionStatus.RUNNING, ExecutionStatus.FAILED): ["runtime_error"],
    (ExecutionStatus.RUNNING, ExecutionStatus.CANCELLED): ["cancelled"],
    (ExecutionStatus.RUNNING, ExecutionStatus.TIMED_OUT): ["timeout"],
    (ExecutionStatus.PARTIALLY_COMPLETED, ExecutionStatus.VERIFYING): ["verification_started"],
    (ExecutionStatus.PARTIALLY_COMPLETED, ExecutionStatus.FAILED): ["irrecoverable_failure"],
    (ExecutionStatus.PARTIALLY_COMPLETED, ExecutionStatus.CANCELLED): ["cancelled"],
    (ExecutionStatus.VERIFYING, ExecutionStatus.COMPLETED): ["verification_passed"],
    (ExecutionStatus.VERIFYING, ExecutionStatus.FAILED): ["verification_failed"],
    (ExecutionStatus.VERIFYING, ExecutionStatus.RUNNING): ["reverification_needed"],
}

#: Allowed (from_state, to_state) → [trigger_names] for AgentFSM.
AGENT_TRANSITIONS: dict[tuple[AgentStatus, AgentStatus], list[str]] = {
    (AgentStatus.CREATED, AgentStatus.READY): ["initialized"],
    (AgentStatus.READY, AgentStatus.RUNNING): ["task_assigned"],
    (AgentStatus.RUNNING, AgentStatus.WAITING_FOR_DEPENDENCY): ["dependency_blocked"],
    (AgentStatus.RUNNING, AgentStatus.WAITING_FOR_APPROVAL): ["approval_required"],
    (AgentStatus.RUNNING, AgentStatus.RETRYING): ["transient_error"],
    (AgentStatus.RUNNING, AgentStatus.COMPLETED): ["task_complete"],
    (AgentStatus.RUNNING, AgentStatus.FAILED): ["fatal_error", "budget_exhausted"],
    (AgentStatus.RUNNING, AgentStatus.CANCELLED): ["cancelled"],
    (AgentStatus.WAITING_FOR_DEPENDENCY, AgentStatus.RUNNING): ["dependency_resolved"],
    (AgentStatus.WAITING_FOR_DEPENDENCY, AgentStatus.CANCELLED): ["cancelled"],
    (AgentStatus.WAITING_FOR_APPROVAL, AgentStatus.RUNNING): ["approved"],
    (AgentStatus.WAITING_FOR_APPROVAL, AgentStatus.CANCELLED): ["rejected", "cancelled"],
    (AgentStatus.RETRYING, AgentStatus.RUNNING): ["retry_ready"],
    (AgentStatus.RETRYING, AgentStatus.FAILED): ["max_retries_exceeded"],
}

#: Allowed (from_state, to_state) → [trigger_names] for ToolInvocationFSM.
TOOL_INVOCATION_TRANSITIONS: dict[tuple[ToolInvocationStatus, ToolInvocationStatus], list[str]] = {
    (ToolInvocationStatus.REQUESTED, ToolInvocationStatus.RISK_CHECKING): ["risk_check_started"],
    (ToolInvocationStatus.RISK_CHECKING, ToolInvocationStatus.APPROVED): ["risk_approved"],
    (ToolInvocationStatus.RISK_CHECKING, ToolInvocationStatus.BLOCKED): [
        "risk_blocked",
        "risk_needs_human",
    ],
    (ToolInvocationStatus.APPROVED, ToolInvocationStatus.RUNNING): ["execution_started"],
    (ToolInvocationStatus.RUNNING, ToolInvocationStatus.SUCCEEDED): ["execution_complete"],
    (ToolInvocationStatus.RUNNING, ToolInvocationStatus.FAILED): ["execution_error"],
    (ToolInvocationStatus.RUNNING, ToolInvocationStatus.TIMED_OUT): ["timeout"],
    (ToolInvocationStatus.TIMED_OUT, ToolInvocationStatus.RUNNING): ["retry_after_timeout"],
    (ToolInvocationStatus.FAILED, ToolInvocationStatus.RUNNING): ["retry"],
}

# ---------------------------------------------------------------------------
# Terminal state sets
# ---------------------------------------------------------------------------

#: States from which no further ExecutionFSM transitions are possible.
EXECUTION_TERMINAL_STATES: frozenset[ExecutionStatus] = frozenset(
    {
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.TIMED_OUT,
    }
)

#: States from which no further AgentFSM transitions are possible.
AGENT_TERMINAL_STATES: frozenset[AgentStatus] = frozenset(
    {
        AgentStatus.COMPLETED,
        AgentStatus.FAILED,
        AgentStatus.CANCELLED,
    }
)

#: States from which no further ToolInvocationFSM transitions are possible.
TOOL_TERMINAL_STATES: frozenset[ToolInvocationStatus] = frozenset(
    {
        ToolInvocationStatus.SUCCEEDED,
        ToolInvocationStatus.BLOCKED,
    }
)

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class StateTransitionError(Exception):
    """Raised when an invalid or disallowed state transition is attempted.

    Attributes
    ----------
    entity_type:
        Human-readable name of the FSM entity, e.g. ``"execution"``.
    entity_id:
        Unique identifier for the entity instance.
    from_state:
        The current (source) state at the time of the failed attempt.
    to_state:
        The target state that was requested.
    trigger:
        The trigger string that was supplied.
    message:
        Detailed human-readable error description.
    """

    def __init__(
        self,
        *,
        entity_type: str,
        entity_id: str,
        from_state: Any,
        to_state: Any,
        trigger: str,
        message: str,
    ) -> None:
        """Initialise the exception with structured context fields."""
        super().__init__(message)
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.from_state = from_state
        self.to_state = to_state
        self.trigger = trigger
        self.message = message

    def __repr__(self) -> str:  # noqa: D401
        """Return a developer-friendly representation."""
        return (
            f"StateTransitionError("
            f"entity_type={self.entity_type!r}, "
            f"entity_id={self.entity_id!r}, "
            f"from_state={self.from_state!r}, "
            f"to_state={self.to_state!r}, "
            f"trigger={self.trigger!r}, "
            f"message={self.message!r})"
        )


# ---------------------------------------------------------------------------
# Transition event dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransitionEvent:
    """Immutable record of a state transition that occurred.

    Passed to the ``on_transition`` callback so consumers (audit log, metrics,
    persistence layer) can react without coupling to the FSM internals.

    Attributes
    ----------
    entity_type:
        The FSM family, e.g. ``"execution"``, ``"agent"``, ``"tool_invocation"``.
    entity_id:
        Unique identifier for the entity instance.
    from_state:
        State *before* the transition.
    to_state:
        State *after* the transition.
    trigger:
        The trigger string that caused the transition.
    timestamp:
        UTC datetime at which the transition was executed.
    metadata:
        Arbitrary caller-supplied key/value pairs (e.g. error details, retry
        count).  Defaults to an empty dict.
    """

    entity_type: str
    entity_id: str
    from_state: Any
    to_state: Any
    trigger: str
    timestamp: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Generic state machine
# ---------------------------------------------------------------------------

S = TypeVar("S")


class StateMachine(Generic[S]):
    """Generic finite state machine with validated, trigger-named transitions.

    Type parameter ``S`` must be a ``str``-based ``Enum`` (or any hashable
    type used as dict keys in the transition table).

    Parameters
    ----------
    entity_type:
        Descriptive name used in log messages and ``TransitionEvent`` records.
    entity_id:
        Unique identifier for the entity being managed.
    initial_state:
        The starting state.  Must be a value that can appear as a *from_state*
        or *to_state* in ``transitions``.
    transitions:
        Mapping of ``(from_state, to_state)`` → list of allowed trigger names.
    on_transition:
        Optional async callback invoked *after* each successful transition.
        Signature: ``async def cb(event: TransitionEvent) -> None``.
    """

    def __init__(
        self,
        entity_type: str,
        entity_id: str,
        initial_state: S,
        transitions: dict[tuple[S, S], list[str]],
        on_transition: Callable[[TransitionEvent], Awaitable[None]] | None = None,
    ) -> None:
        """Initialise the state machine and pre-compute helper indexes."""
        self._entity_type = entity_type
        self._entity_id = entity_id
        self._state: S = initial_state
        self._transitions = transitions
        self._on_transition = on_transition
        self._lock = asyncio.Lock()
        self._log = logger.bind(
            entity_type=entity_type,
            entity_id=entity_id,
        )

        # Pre-compute: from_state → list[to_state] (for allowed_transitions)
        self._outgoing: dict[S, list[S]] = {}
        for from_s, to_s in self._transitions:
            self._outgoing.setdefault(from_s, []).append(to_s)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current_state(self) -> S:
        """Return the current state of the machine (read-only snapshot)."""
        return self._state

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def can_transition(self, to_state: S, trigger: str | None = None) -> bool:
        """Return ``True`` if moving to *to_state* is currently valid.

        When *trigger* is supplied it must appear in the allowed triggers for
        the ``(current_state, to_state)`` pair.  When omitted, only the state
        pair is checked.

        Parameters
        ----------
        to_state:
            The candidate target state.
        trigger:
            Optional trigger name to validate in addition to the state pair.
        """
        pair = (self._state, to_state)
        allowed_triggers = self._transitions.get(pair)
        if allowed_triggers is None:
            return False
        if trigger is not None:
            return trigger in allowed_triggers
        return True

    def allowed_transitions(self) -> list[S]:
        """Return all states reachable from the current state via any trigger."""
        return list(self._outgoing.get(self._state, []))

    def is_terminal(self) -> bool:
        """Return ``True`` if the current state has no outgoing transitions."""
        return not bool(self._outgoing.get(self._state))

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    async def transition(
        self,
        to_state: S,
        trigger: str,
        metadata: dict[str, Any] | None = None,
    ) -> TransitionEvent:
        """Execute a state transition atomically.

        The internal ``asyncio.Lock`` ensures that concurrent callers block
        rather than corrupt state.  The first caller to acquire the lock will
        complete its transition (or raise); subsequent callers will then
        re-validate against the *updated* state.

        Parameters
        ----------
        to_state:
            Desired target state.
        trigger:
            The named trigger causing this transition.  Must be in the
            allowed-triggers list for the ``(current_state, to_state)`` pair.
        metadata:
            Optional dict of extra context included in the ``TransitionEvent``.

        Returns
        -------
        TransitionEvent
            Immutable record describing what happened.

        Raises
        ------
        StateTransitionError
            If the transition is not in the allow-list, or the trigger is not
            valid for the pair.
        """
        async with self._lock:
            from_state = self._state
            pair = (from_state, to_state)
            allowed_triggers = self._transitions.get(pair)

            if allowed_triggers is None:
                msg = (
                    f"{self._entity_type} '{self._entity_id}': "
                    f"transition {from_state!r} → {to_state!r} is not allowed. "
                    f"Trigger attempted: '{trigger}'."
                )
                self._log.warning(
                    "state_transition_rejected",
                    from_state=str(from_state),
                    to_state=str(to_state),
                    trigger=trigger,
                    reason="unknown_transition",
                )
                raise StateTransitionError(
                    entity_type=self._entity_type,
                    entity_id=self._entity_id,
                    from_state=from_state,
                    to_state=to_state,
                    trigger=trigger,
                    message=msg,
                )

            if trigger not in allowed_triggers:
                msg = (
                    f"{self._entity_type} '{self._entity_id}': "
                    f"trigger '{trigger}' is not valid for transition "
                    f"{from_state!r} → {to_state!r}. "
                    f"Allowed triggers: {allowed_triggers!r}."
                )
                self._log.warning(
                    "state_transition_rejected",
                    from_state=str(from_state),
                    to_state=str(to_state),
                    trigger=trigger,
                    reason="invalid_trigger",
                )
                raise StateTransitionError(
                    entity_type=self._entity_type,
                    entity_id=self._entity_id,
                    from_state=from_state,
                    to_state=to_state,
                    trigger=trigger,
                    message=msg,
                )

            # Commit the transition
            self._state = to_state
            event = TransitionEvent(
                entity_type=self._entity_type,
                entity_id=self._entity_id,
                from_state=from_state,
                to_state=to_state,
                trigger=trigger,
                timestamp=datetime.now(tz=UTC),
                metadata=metadata or {},
            )

            self._log.info(
                "state_transition_ok",
                from_state=str(from_state),
                to_state=str(to_state),
                trigger=trigger,
            )

        # Call the callback *outside* the lock to avoid deadlock if the
        # callback itself triggers another transition on the same machine.
        if self._on_transition is not None:
            await self._on_transition(event)

        return event


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def execution_state_machine(
    execution_id: str = "",
    initial: ExecutionStatus = ExecutionStatus.CREATED,
    callback: Callable[[TransitionEvent], Awaitable[None]] | None = None,
) -> StateMachine[ExecutionStatus]:
    """Create a ``StateMachine`` pre-configured for execution lifecycle.

    Parameters
    ----------
    execution_id:
        Unique identifier for the execution.
    initial:
        Starting ``ExecutionStatus`` (typically ``CREATED``).
    callback:
        Optional async function called after every successful transition.

    Returns
    -------
    StateMachine[ExecutionStatus]
    """
    return StateMachine(
        entity_type="execution",
        entity_id=execution_id,
        initial_state=initial,
        transitions=EXECUTION_TRANSITIONS,
        on_transition=callback,
    )


create_execution_state_machine = execution_state_machine


def agent_state_machine(
    agent_id: str,
    initial: AgentStatus,
    callback: Callable[[TransitionEvent], Awaitable[None]] | None = None,
) -> StateMachine[AgentStatus]:
    """Create a ``StateMachine`` pre-configured for agent lifecycle.

    Parameters
    ----------
    agent_id:
        Unique identifier for the agent.
    initial:
        Starting ``AgentStatus`` (typically ``CREATED``).
    callback:
        Optional async function called after every successful transition.

    Returns
    -------
    StateMachine[AgentStatus]
    """
    return StateMachine(
        entity_type="agent",
        entity_id=agent_id,
        initial_state=initial,
        transitions=AGENT_TRANSITIONS,
        on_transition=callback,
    )


def tool_invocation_state_machine(
    invocation_id: str,
    initial: ToolInvocationStatus,
    callback: Callable[[TransitionEvent], Awaitable[None]] | None = None,
) -> StateMachine[ToolInvocationStatus]:
    """Create a ``StateMachine`` pre-configured for tool-invocation lifecycle.

    Parameters
    ----------
    invocation_id:
        Unique identifier for the tool invocation.
    initial:
        Starting ``ToolInvocationStatus`` (typically ``REQUESTED``).
    callback:
        Optional async function called after every successful transition.

    Returns
    -------
    StateMachine[ToolInvocationStatus]
    """
    return StateMachine(
        entity_type="tool_invocation",
        entity_id=invocation_id,
        initial_state=initial,
        transitions=TOOL_INVOCATION_TRANSITIONS,
        on_transition=callback,
    )
