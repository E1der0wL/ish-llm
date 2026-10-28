"""Read-only preview of root Loop answer output, separate from conversation history."""

from collections import OrderedDict
from dataclasses import dataclass, field

from llm.core.model_protocol import StopReason
from llm.core.models import Run, RunStatus, StepKind
from llm.core.results import CompletionRecords, EngineDelta, EngineOutput, OutputVisibility
from llm.engines.events import EngineEvent, EngineEventType as Event
from llm.engines.loop import LoopEngine
from llm.engines.loop.records import LoopStepMetadata

from ...model import AnswerDraft


@dataclass(slots=True)
class DraftState:
    step_id: str | None = None
    iteration: int = 0
    cursor: int = 0
    output: EngineOutput | None = None
    termination: StopReason | None = None
    needs_sync: bool = False
    published: bool = False
    pending: dict[int, EngineDelta | EngineOutput] = field(default_factory=dict)


class AnswerDrafts:
    """Only public output snapshots/events are read; no reviewer or execution authority."""

    MAX_RUNS = 128
    MAX_PENDING = 128
    FAILED_STEPS = (Event.STEP_FAILED, Event.STEP_CANCELLED, Event.STEP_INTERRUPTED)

    def __init__(self):
        self.runs: OrderedDict[str, DraftState] = OrderedDict()

    def clear(self) -> None:
        self.runs.clear()

    def forget(self, run_id: str) -> None:
        self.runs.pop(run_id, None)

    def invalidate(self) -> None:
        for state in self.runs.values():
            state.needs_sync, state.output = True, None

    def state(self, run_id: str, *, needs_sync=False) -> DraftState:
        value = self.runs.setdefault(run_id, DraftState(needs_sync=needs_sync))
        self.runs.move_to_end(run_id)
        while len(self.runs) > self.MAX_RUNS:
            self.runs.popitem(last=False)
        return value

    @staticmethod
    def select(state: DraftState, step_id, kind, name, metadata) -> None:
        record = LoopStepMetadata(metadata)
        if (kind == StepKind.LLM and name == LoopEngine.COMPLETION_STEP_NAME
                and record.checkpoint_name == LoopEngine.checkpoint_name
                and record.checkpoint_scope is None
                and type(record.iteration) is int and record.iteration > state.iteration):
            state.step_id, state.iteration = step_id, record.iteration
            state.output, state.termination = None, None

    @classmethod
    def apply(cls, state: DraftState, value: EngineDelta | EngineOutput) -> None:
        if value.sequence <= state.cursor:
            return
        if state.needs_sync or value.sequence != state.cursor + 1:
            state.needs_sync, state.output = True, None
            state.pending[value.sequence] = value
            if len(state.pending) > cls.MAX_PENDING:
                state.pending.pop(next(iter(state.pending)))
            return
        state.cursor = value.sequence
        if value.visibility == OutputVisibility.USER and value.step_id is None:
            # Publication, not Step.final, is the backend's accepted-answer boundary.
            state.published, state.output = True, None
        elif (not state.published and value.step_id == state.step_id
              and value.visibility == OutputVisibility.INTERNAL):
            if isinstance(value, EngineDelta):
                prior = state.output or EngineOutput(
                    output_id=value.output_id, step_id=value.step_id,
                    visibility=value.visibility, final=False)
                if prior.final or prior.output_id != value.output_id:
                    state.needs_sync, state.output = True, None
                    state.pending[value.sequence] = value
                    return
                state.output = prior.apply(value)
            else:
                state.output = value

    def observe(self, run: Run, event: EngineEvent) -> None:
        if run.status != RunStatus.RUNNING:
            self.forget(run.id)
            return
        state = self.state(run.id)
        kind = EngineEvent.observed_type(event)
        if kind == Event.STEP_STARTED:
            self.select(state, event.step_id, event.kind, event.name, event.metadata)
        completion = EngineEvent.observed_completion(event)
        if completion is not None and completion.step_id == state.step_id:
            state.termination = completion.termination
            if completion.termination not in (None, StopReason.COMPLETE):
                state.output = None
        value = EngineEvent.observed_delta(event) or EngineEvent.observed_output(event)
        if value is not None:
            self.apply(state, value)
        if event.step_id == state.step_id and kind in self.FAILED_STEPS:
            state.output, state.termination = None, StopReason.BLOCKED

    async def read(self, handle, run: Run, steps) -> AnswerDraft | None:
        if run.status != RunStatus.RUNNING:
            self.forget(run.id)
            return None
        state = self.state(run.id, needs_sync=True)
        for step in steps:
            self.select(state, step.id, step.kind, step.name, step.metadata)
        if state.needs_sync:
            # One atomic public snapshot on attach or loss, not a storage scan per delta.
            view = await handle.aview(limit=1)
            if self.runs.get(run.id) is not state:
                return None  # Closed/replaced while the public snapshot was being read.
            if view.run.status != RunStatus.RUNNING:
                self.forget(run.id)
                return None
            state.published = any(value.visibility == OutputVisibility.USER
                                  and value.step_id is None for value in view.outputs)
            state.output = next((value for value in view.outputs
                                 if value.step_id == state.step_id
                                 and value.visibility == OutputVisibility.INTERNAL), None)
            for completion in CompletionRecords.results(view.run.metadata):
                if (completion.step_id == state.step_id
                        and state.termination in (None, StopReason.COMPLETE)):
                    state.termination = completion.termination
            state.cursor, state.needs_sync = view.cursor, False
            pending, state.pending = state.pending, {}
            for _, value in sorted(pending.items()):
                self.apply(state, value)
        output = state.output
        if (state.needs_sync or state.published or output is None or not output.text
                or state.termination not in (None, StopReason.COMPLETE)):
            return None
        return AnswerDraft(output.text, AnswerDraft.REVIEWING if output.final else AnswerDraft.WRITING)
