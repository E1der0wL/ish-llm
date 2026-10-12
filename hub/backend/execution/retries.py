"""Observe confirmed provider retries without owning retry/execution policy."""

from contextlib import aclosing
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic

from llm.engines.loop import LoopEngine
from ...model import RetrySummary


@dataclass(slots=True)
class CompletionWatch:
    run_id: str
    policy: dict
    attempt: int = 0
    completion_id: str = ""


class RetryMonitor:
    def __init__(self) -> None:
        self.run: ContextVar[str] = ContextVar("hub_retry_run", default="")
        self.call: ContextVar[CompletionWatch | None] = ContextVar("hub_retry_completion", default=None)
        self.states: dict[int, tuple[CompletionWatch, RetrySummary]] = {}

    def __call__(self, event: dict) -> None:
        # This fact is emitted only AFTER the backend checks partial output,
        # error eligibility, attempts, SDK delegation and the wall deadline.
        watch = self.call.get()
        if watch is None or event.get("kind") != "providers" or event.get("status") != "retries" or event.get("name") != "completion":
            return
        delay = watch.policy.get("delay_seconds", 0) * 2 ** max(0, watch.attempt - 1)
        delay = min(delay, watch.policy.get("max_delay_seconds", delay))
        self.states[id(watch)] = (watch, RetrySummary(watch.attempt,
            watch.policy.get("max_attempts", 1), monotonic() + delay))

    def view(self, run_id: str) -> RetrySummary | None:
        states = [state for watch, state in self.states.values() if watch.run_id == run_id]
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
        watch = CompletionWatch(monitor.run.get(), dict(kwargs.get("provider") or {}))
        try:
            async with aclosing(super().stream_completion(request, **kwargs)) as events:
                while True:
                    token = monitor.call.set(watch)
                    try:
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
                            monitor.states[id(watch)] = (watch, RetrySummary(
                                watch.attempt, watch.policy.get("max_attempts", 1)))
                    yield event
        finally:
            monitor.states.pop(id(watch), None)


class HubLoopEngine(RetryObservationMixin, LoopEngine):
    """The standard Loop with optional Hub-only retry observations."""
