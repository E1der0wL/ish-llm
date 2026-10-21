"""Project provider and Tool retries into one UI state; never decide execution."""

from contextlib import aclosing
from contextvars import ContextVar
from dataclasses import dataclass, replace
import math
from time import monotonic

from llm.engines.loop import LoopEngine
from llm.engines.base import EngineEventType
from llm.core.contracts import Diagnostic
from llm.policies.retry import RetryPolicy
from llm.providers.requests import resolve_provider_options
from llm.providers.runtime import diagnostic_scope
from ...model import RetrySummary
from .provider_recovery import CompletionRecoveryMixin


@dataclass(slots=True)
class CompletionWatch:
    run_id: str
    policy: dict
    attempt: int = 0
    completion_id: str = ""
    call_id: str = ""


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
    def __init__(self) -> None:
        self.run: ContextVar[str] = ContextVar("hub_retry_run", default="")
        self.call: ContextVar[CompletionWatch | None] = ContextVar("hub_retry_completion", default=None)
        self.states: dict[int | tuple[str, str], RetryObservation] = {}
        self.tools: dict[tuple[str, str], ToolWatch] = {}

    def set(self, key, run_id, summary):
        self.states[key] = RetryObservation(run_id, summary)

    def observe_tool(self, context, event):
        """Called after the owning service consumes each Engine event."""
        key = (context.run.id, event.step_id)
        if event.type == EngineEventType.STEP_STARTED and event.kind == "tool":
            policy = context.run.metadata.get("policies", {}).get("tool_retry", {})
            if policy.get("max_retries"):
                delay = policy.get("delay_seconds")
                self.tools[key] = ToolWatch(event.name, policy["max_retries"] + 1,
                                            0 if delay is None else delay)
        watch = self.tools.get(key)
        if watch is None:
            return
        if event.type in (EngineEventType.STEP_COMPLETED, EngineEventType.STEP_FAILED,
                          EngineEventType.STEP_INTERRUPTED, EngineEventType.STEP_CANCELLED):
            self.states.pop(key, None)
            self.tools.pop(key, None)
        elif event.type == EngineEventType.STEP_UPDATED:
            phase = event.metadata.get("phase")
            attempt = event.metadata.get("retry_attempt")
            if phase in ("repairing", "retrying") and type(attempt) is int and attempt > 0:
                self.set(key, context.run.id, RetrySummary(attempt, watch.maximum,
                    monotonic() + watch.delay if phase == "retrying" else None,
                    "tool", watch.name, "repairing" if phase == "repairing" else "waiting"))

    def clear_tools(self, run_id):
        for key in [key for key in self.tools if key[0] == run_id]:
            self.tools.pop(key)
            self.states.pop(key, None)

    def __call__(self, event: dict) -> None:
        # Executions is emitted immediately before the actual Tool call, including
        # unchanged-argument retries that have no new STEP_UPDATED event.
        if event.get("kind") == "tools" and event.get("status") == "executions":
            key = (event.get("run_id"), event.get("step_id"))
            previous = self.states.get(key)
            if previous is not None and previous.summary.phase != "active":
                self.set(key, previous.run_id, replace(previous.summary,
                    attempt=previous.summary.attempt + 1, retry_at=None, phase="active"))
            return

    def observe_provider(self, event: Diagnostic) -> None:
        """The provider owns Retry-After/jitter/deadlines; project its chosen delay."""
        watch = self.call.get()
        if watch is None:
            return
        if event.code in ("completion_recovery", "completion_recovery_rejected"):
            state = self.states.get((watch.run_id, "recovery"))
            if state is not None:
                self.set((watch.run_id, "recovery"), watch.run_id,
                    replace(state.summary, phase="retrying" if event.code == "completion_recovery" else "rejected", retry_at=None))
            return
        if event.code != "provider_retry":
            return
        details = event.details
        if details.get("operation") != "completion" or details.get("run_id", watch.run_id) != watch.run_id:
            return
        delay, attempt, call_id = details.get("delay_seconds"), details.get("attempt"), details.get("call_id")
        if (type(delay) not in (int, float) or not math.isfinite(delay) or delay < 0
                or type(attempt) is not int or attempt != watch.attempt + 1
                or not isinstance(call_id, str) or not call_id or watch.call_id not in ("", call_id)):
            return
        watch.call_id = call_id
        self.states.pop((watch.run_id, "recovery"), None)
        self.set(id(watch), watch.run_id, RetrySummary(attempt - 1,
            RetryPolicy(watch.policy.get("max_attempts")).attempts, monotonic() + delay))

    def view(self, run_id: str) -> RetrySummary | None:
        states = [item.summary for item in self.states.values() if item.run_id == run_id]
        recovery = next((state for state in states if state.kind == "recovery"), None)
        if recovery is not None:
            return recovery
        # Parallel Graph agents keep separate calls. Finishing one must not
        # erase another's backoff; show the next scheduled attempt first.
        return min(states, key=lambda state: state.retry_at if state.retry_at is not None else float("inf"),
                   default=None)


class RetryObservationMixin:
    """Use with BaseEngine implementations; callbacks never replace their retry loop."""
    retry_monitor: RetryMonitor | None = None

    async def execute(self, context):
        monitor = self.retry_monitor
        if monitor is None:
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
        if monitor is None or not monitor.run.get() or kwargs.get("include_events", True) is False:
            async with aclosing(super().stream_completion(request, **kwargs)) as events:
                async for event in events:
                    yield event
            return
        provider = kwargs.get("provider")
        watch = CompletionWatch(monitor.run.get(),
                                resolve_provider_options({} if provider is None else provider))
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
                    completion = getattr(event, "completion", None)
                    if completion and completion.id != watch.completion_id:
                        watch.completion_id = completion.id
                        watch.attempt += 1
                        if watch.attempt > 1:
                            monitor.states.pop((watch.run_id, "recovery"), None)
                            monitor.set(id(watch), watch.run_id, RetrySummary(
                                watch.attempt, RetryPolicy(watch.policy.get("max_attempts")).attempts))
                    yield event
        finally:
            monitor.states.pop(id(watch), None)


class HubLoopEngine(CompletionRecoveryMixin, RetryObservationMixin, LoopEngine):
    """The standard Loop with optional Hub-only retry observations."""
