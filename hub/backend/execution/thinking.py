"""Transient provider-exposed reasoning phases; never a second transcript."""

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import ClassVar

from llm.core.continuations import ResumeRecord
from llm.core.models import Run, RunStatus
from llm.core.results import CompletionRecords, CompletionResult, OutputVisibility
from llm.engines.events import EngineEvent
from ...model import TraceItem, ResponseActivity
from .trace import merge_trace


@dataclass(slots=True)
class ThinkingState:
    EMPTY_TEXT: ClassVar[str] = ""
    NO_PHASE: ClassVar[None] = None
    NO_COMPLETION: ClassVar[None] = None

    seen: dict = field(default_factory=dict)
    text: str = EMPTY_TEXT
    phase: str | None = NO_PHASE
    completion: str | None = NO_COMPLETION
    activity: str = ResponseActivity.EMPTY
    trace: dict = field(default_factory=dict)


class ThinkingPhases:
    MAXIMUM_RUNS = 128
    REMOVE_NEWEST = False
    ABSENT = None
    NO_TIME = ""
    THOUGHT_PREFIX = "thought:"
    PARAGRAPH = "\n\n"
    ACTIVE_STATES = Run.ACTIVE_STATES
    UNREPORTED_STATUS = RunStatus.RUNNING.value

    def __init__(self):
        self.runs = OrderedDict()

    def observe(self, run, event):
        if str(run.status) not in self.ACTIVE_STATES:
            self.runs.pop(run.id, self.ABSENT)
            return
        state = self.runs.setdefault(run.id, ThinkingState())
        self.runs.move_to_end(run.id)
        while len(self.runs) > self.MAXIMUM_RUNS:
            self.runs.popitem(last=self.REMOVE_NEWEST)
        completion = EngineEvent.observed_completion(event)
        if completion is not self.ABSENT:
            if completion.id != state.completion:
                state.completion = completion.id
                state.activity = ResponseActivity.THINKING
            text = completion.reasoning_content
            if text:
                state.trace[completion.id] = TraceItem(
                    self.THOUGHT_PREFIX + completion.id,
                    TraceItem.REASONING,
                    text,
                    started_at=getattr(
                        completion, CompletionResult.started_at.__name__, self.ABSENT
                    )
                    or getattr(run, Run.started_at.__name__, self.NO_TIME)
                    or self.NO_TIME,
                )
            previous = state.seen.get(completion.id, ThinkingState.EMPTY_TEXT)
            state.seen[completion.id] = text
            if text != previous and text:
                added = text[len(previous) :] if text.startswith(previous) else text
                state.text += (
                    self.PARAGRAPH
                    if state.text and not previous
                    else ThinkingState.EMPTY_TEXT
                ) + added
                state.phase = completion.id
                state.activity = ResponseActivity.THINKING
            if (
                str(
                    getattr(
                        completion,
                        CompletionResult.status.__name__,
                        self.UNREPORTED_STATUS,
                    )
                )
                not in self.ACTIVE_STATES
            ):
                state.activity = ResponseActivity.RUNNING
        delta = EngineEvent.observed_delta(event)
        output = EngineEvent.observed_output(event)
        if any(
            value is not self.ABSENT
            and value.visibility == OutputVisibility.USER
            and value.text
            for value in (delta, output)
        ):
            state.activity = ResponseActivity.STREAMING

    def activity(self, run) -> str:
        """Execution observation, independent of whether thought text is exposed."""
        if run is self.ABSENT or str(run.status) not in self.ACTIVE_STATES:
            return ResponseActivity.EMPTY
        state = self.runs.get(run.id)
        if state and state.activity:
            return state.activity
        completions = CompletionRecords.results(run.metadata)
        if completions:
            return (
                ResponseActivity.THINKING
                if completions[-1].status in self.ACTIVE_STATES
                else ResponseActivity.RUNNING
            )
        return (
            ResponseActivity.RESUMING
            if ResumeRecord.from_metadata(run.metadata)
            else ResponseActivity.PREPARING
        )

    def visible(self, run_id, fallback, *, active, content):
        if not active:
            self.runs.pop(run_id, self.ABSENT)
            return ThinkingState.EMPTY_TEXT
        state = self.runs.get(run_id)
        text = state.text if state is not self.ABSENT else ThinkingState.EMPTY_TEXT
        return fallback if len(fallback) >= len(text) else text

    def trace(self, run_id, fallback):
        state = self.runs.get(run_id)
        if not state:
            return fallback
        observed = state.trace.values()
        stored = {item.id: item for item in fallback}
        return merge_trace(
            fallback,
            (
                item
                for item in observed
                if item.id not in stored or len(item.text) > len(stored[item.id].text)
            ),
        )
