"""Project provider and Tool retries into one UI state; never decide execution."""

from llm.core.models import StepKind
from llm.core.policies import ToolRetryPolicy

from contextlib import aclosing
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import ClassVar
import math
from time import monotonic

from llm.engines.loop import LoopEngine
from llm.engines.base import EngineEventType
from llm.engines.events import EngineEvent
from llm.engines.completion_state import CompletionInvocationOptions
from llm.providers.options import ProviderOptions
from llm.providers.diagnostic_values import (
    ProviderAttempt,
    ProviderRetryObservation,
    ProviderRetryDetails,
)
from llm.core.model_protocol import ModelOperation
from llm.core.step_tool_metadata import ToolRetryProgress
from llm.services.runtime.tools import ToolExecutionPhase
from ..tools.retry_values import ToolRepairPhase
from .retry_values import RetryIdentity
from llm.core.contracts import Diagnostic
from llm.policies.retry import RetryPolicy
from llm.providers.runtime import diagnostic_scope
from llm.services.infrastructure.observability import (
    ObservationKind,
    ObservationStatus,
    ObservationRecord,
)
from ...model import RetrySummary
from .provider_recovery import CompletionRecoveryMixin


@dataclass(slots=True)
class CompletionWatch:
    INITIAL_ATTEMPT: ClassVar[int] = 0
    NO_COMPLETION: ClassVar[str] = ""
    NO_CALL: ClassVar[str] = ""

    run_id: str
    policy: dict
    attempt: int = INITIAL_ATTEMPT
    completion_id: str = NO_COMPLETION
    call_id: str = NO_CALL

    @property
    def maximum(self):
        return RetryPolicy(ProviderOptions(self.policy).max_attempts).attempts


@dataclass(slots=True)
class ToolWatch:
    name: str
    maximum: int
    delay: float


@dataclass(frozen=True, slots=True)
class RetryObservation:
    run_id: str
    summary: RetrySummary


class RetryMonitor:
    RUN_CONTEXT = "hub_retry_run"
    CALL_CONTEXT = "hub_retry_completion"
    NO_RUN = ""
    NO_WATCH = None
    NO_STATE = None
    NO_DELAY = 0
    NO_ATTEMPT = 0
    UNSCHEDULED = math.inf
    TOOL_PROGRESS_PHASES = (ToolRepairPhase.REPAIRING, ToolExecutionPhase.RETRYING)
    TERMINAL_TOOL_EVENTS = (
        EngineEventType.STEP_COMPLETED,
        EngineEventType.STEP_FAILED,
        EngineEventType.STEP_INTERRUPTED,
        EngineEventType.STEP_CANCELLED,
    )
    RECOVERY_EVENTS = (
        ProviderRetryObservation.RECOVERY,
        ProviderRetryObservation.RECOVERY_REJECTED,
    )

    def __init__(self) -> None:
        self.run: ContextVar[str] = ContextVar(self.RUN_CONTEXT, default=self.NO_RUN)
        self.call: ContextVar[CompletionWatch | None] = ContextVar(
            self.CALL_CONTEXT, default=self.NO_WATCH
        )
        self.states: dict[int | tuple[str, str], RetryObservation] = {}
        self.tools: dict[tuple[str, str], ToolWatch] = {}

    def set(self, key, run_id, summary):
        self.states[key] = RetryObservation(run_id, summary)

    def observe_tool(self, context, event):
        """Called after the owning service consumes each Engine event."""
        key = RetryIdentity.create(context.run.id, event.step_id)
        if event.type == EngineEventType.STEP_STARTED and event.kind == StepKind.TOOL:
            policy = ToolRetryPolicy.from_run(context.run)
            if policy.max_retries:
                delay = policy.delay_seconds
                self.tools[key] = ToolWatch(
                    event.name,
                    policy.max_retries + ProviderAttempt.ORDINAL_INCREMENT,
                    RetryPolicy.NO_DELAY if delay is RetryPolicy.UNSPECIFIED else delay,
                )
        watch = self.tools.get(key)
        if watch is self.NO_WATCH:
            return
        if event.type in self.TERMINAL_TOOL_EVENTS:
            self.states.pop(key, self.NO_STATE)
            self.tools.pop(key, self.NO_WATCH)
        elif event.type == EngineEventType.STEP_UPDATED:
            progress = ToolRetryProgress(event.metadata)
            phase = progress.phase
            attempt = progress.attempt
            if (
                phase in self.TOOL_PROGRESS_PHASES
                and type(attempt) is int
                and attempt > self.NO_ATTEMPT
            ):
                self.set(
                    key,
                    context.run.id,
                    RetrySummary(
                        attempt,
                        watch.maximum,
                        (
                            monotonic() + watch.delay
                            if phase == ToolExecutionPhase.RETRYING
                            else RetrySummary.NO_RETRY_AT
                        ),
                        RetrySummary.TOOL,
                        watch.name,
                        (
                            RetrySummary.REPAIRING
                            if phase == ToolRepairPhase.REPAIRING
                            else RetrySummary.WAITING
                        ),
                    ),
                )

    def clear_tools(self, run_id):
        for key in [key for key in self.tools if RetryIdentity.run_id(key) == run_id]:
            self.tools.pop(key)
            self.states.pop(key, self.NO_STATE)

    def __call__(self, event: dict) -> None:
        # Executions is emitted immediately before the actual Tool call, including
        # unchanged-argument retries that have no new STEP_UPDATED event.
        observation = ObservationRecord(event)
        if (
            observation.kind == ObservationKind.TOOLS.value
            and observation.status == ObservationStatus.EXECUTIONS.value
        ):
            key = RetryIdentity.create(observation.run_id, observation.step_id)
            previous = self.states.get(key)
            if (
                previous is not self.NO_STATE
                and previous.summary.phase != RetrySummary.ACTIVE
            ):
                self.set(
                    key,
                    previous.run_id,
                    replace(
                        previous.summary,
                        attempt=RetrySummary.next_attempt(previous.summary),
                        retry_at=RetrySummary.NO_RETRY_AT,
                        phase=RetrySummary.ACTIVE,
                    ),
                )
            return

    def observe_provider(self, event: Diagnostic) -> None:
        """The provider owns Retry-After/jitter/deadlines; project its chosen delay."""
        watch = self.call.get()
        if watch is self.NO_WATCH:
            return
        if event.code in self.RECOVERY_EVENTS:
            state = self.states.get(
                RetryIdentity.create(watch.run_id, RetrySummary.RECOVERY)
            )
            if state is not self.NO_STATE:
                self.set(
                    RetryIdentity.create(watch.run_id, RetrySummary.RECOVERY),
                    watch.run_id,
                    replace(
                        state.summary,
                        phase=(
                            RetrySummary.RETRYING
                            if event.code == ProviderRetryObservation.RECOVERY
                            else RetrySummary.REJECTED
                        ),
                        retry_at=RetrySummary.NO_RETRY_AT,
                    ),
                )
            return
        if event.code != ProviderRetryObservation.RETRY:
            return
        details = ProviderRetryDetails(event.details)
        if (
            details.operation != ModelOperation.COMPLETION.value
            or details.run_id(watch.run_id) != watch.run_id
        ):
            return
        delay, attempt, call_id = (
            details.delay_seconds,
            details.attempt,
            details.call_id,
        )
        if (
            type(delay) not in (int, float)
            or not math.isfinite(delay)
            or delay < self.NO_DELAY
            or type(attempt) is not int
            or attempt != watch.attempt + ProviderAttempt.ORDINAL_INCREMENT
            or not isinstance(call_id, str)
            or not call_id
            or watch.call_id not in (CompletionWatch.NO_CALL, call_id)
        ):
            return
        watch.call_id = call_id
        self.states.pop(
            RetryIdentity.create(watch.run_id, RetrySummary.RECOVERY), self.NO_STATE
        )
        self.set(
            id(watch),
            watch.run_id,
            RetrySummary(
                attempt - ProviderAttempt.ORDINAL_INCREMENT,
                watch.maximum,
                monotonic() + delay,
            ),
        )

    def view(self, run_id: str) -> RetrySummary | None:
        states = [
            item.summary for item in self.states.values() if item.run_id == run_id
        ]
        recovery = next(
            (state for state in states if state.kind == RetrySummary.RECOVERY),
            self.NO_STATE,
        )
        if recovery is not self.NO_STATE:
            return recovery
        # Parallel Graph agents keep separate calls. Finishing one must not
        # erase another's backoff; show the next scheduled attempt first.
        return min(
            states,
            key=lambda state: (
                state.retry_at
                if state.retry_at is not RetrySummary.NO_RETRY_AT
                else self.UNSCHEDULED
            ),
            default=self.NO_STATE,
        )


class RetryObservationMixin:
    """Use with BaseEngine implementations; callbacks never replace their retry loop."""

    NO_MONITOR = None
    retry_monitor: RetryMonitor | None = NO_MONITOR

    async def execute(self, context):
        monitor = self.retry_monitor
        if monitor is self.NO_MONITOR:
            async with aclosing(super().execute(context)) as events:
                async for event in events:
                    yield event
            return
        async with aclosing(super().execute(context)) as events:
            while True:
                token = monitor.run.set(context.run.id)
                try:
                    event = await anext(events)
                except StopAsyncIteration:
                    break
                finally:
                    monitor.run.reset(token)
                yield event

    async def stream_completion(self, request, **kwargs):
        monitor = self.retry_monitor
        if (
            monitor is self.NO_MONITOR
            or not monitor.run.get()
            or CompletionInvocationOptions(kwargs).events_excluded
        ):
            async with aclosing(super().stream_completion(request, **kwargs)) as events:
                async for event in events:
                    yield event
            return
        watch = CompletionWatch(
            monitor.run.get(), CompletionInvocationOptions(kwargs).resolved_provider()
        )
        try:
            async with aclosing(super().stream_completion(request, **kwargs)) as events:
                while True:
                    token = monitor.call.set(watch)
                    try:
                        # Scope the observer to iteration, including callbacks after
                        # the Session worker has started. Never leak across yields.
                        with diagnostic_scope(monitor.observe_provider):
                            event = await anext(events)
                    except StopAsyncIteration:
                        break
                    finally:
                        monitor.call.reset(token)
                    completion = EngineEvent.observed_completion(event)
                    if completion and completion.id != watch.completion_id:
                        watch.completion_id = completion.id
                        watch.attempt += ProviderAttempt.ORDINAL_INCREMENT
                        if watch.attempt > ProviderAttempt.ORDINAL_INCREMENT:
                            monitor.states.pop(
                                RetryIdentity.create(
                                    watch.run_id, RetrySummary.RECOVERY
                                ),
                                RetryMonitor.NO_STATE,
                            )
                            monitor.set(
                                id(watch),
                                watch.run_id,
                                RetrySummary(watch.attempt, watch.maximum),
                            )
                    yield event
        finally:
            monitor.states.pop(id(watch), RetryMonitor.NO_STATE)


class HubLoopEngine(CompletionRecoveryMixin, RetryObservationMixin, LoopEngine):
    """The standard Loop with optional Hub-only retry observations."""
