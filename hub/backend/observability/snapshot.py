"""Read-only projection through llm services; no scheduling or localized strings."""

from hub.model import ReasoningMessage

from llm.core.steering import RunInstruction, is_instruction
from llm.core.models import MessageRole, SessionStatus
from llm.core.agents import AgentDefinition, AgentBinding
from ..project.participants import AgentProfile
from ..project.participant_groups import ParticipantGroups
from .workflow import WorkflowObservation
from ..execution.engine_selection import HubEngineIdentity

from llm.core.models import StepKind

from llm.core.models import RunStatus, MessageStatus
from llm.core.results import CompletionRecords
from llm.services.runtime.requests import RequestAdmission
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, NamedTuple
from time import monotonic

from ...model import (
    QuestionPresentation,
    AgentSummary,
    ChatMessage,
    HubSnapshot,
    SessionSummary,
    RunSummary,
    TraceItem,
    ResponseActivity,
)
from ..execution.trace import merge_trace, tool_trace
from ..execution.followup import split_followup
from ..execution.steps import display_steps
from ..execution.engine_selection import agent_model, selected_engines
from ..interactions.questions import waiting_status
from ..execution.stopping import display_run, StopIntent


@dataclass(slots=True)
class SessionObservation:
    session_id: str
    record: Any
    runtime: Any
    run: Any
    handle: Any
    questions: tuple = ()


class CachedSessionObservation(NamedTuple):
    observed_at: float
    value: SessionObservation


class SnapshotReader:
    ABSENT = None
    EMPTY_TEXT = ""
    PARAGRAPH = "\n\n"
    CACHE_LIMIT = 1024
    OBSERVATION_SECONDS = 2
    ZERO_ELAPSED = 0.0
    QUEUED_IDS_LIMIT = 0
    LATEST_RUN_LIMIT = 1
    NEWEST_FIRST = True
    PENDING_ONLY = True
    NO_QUEUED_REQUESTS = 0
    NO_MODEL_REQUIRED = False
    MUTABLE_RUN_STATES = frozenset((RunStatus.PENDING, RunStatus.RUNNING, RunStatus.PAUSED))

    def record_submission(self, identifier, timestamp):
        self.submissions[identifier] = timestamp
        if len(self.submissions) > SnapshotReader.CACHE_LIMIT:
            self.submissions.pop(next(iter(self.submissions)))

    def invalidate_dropped_events(self):
        self.thinking.runs.clear()
        self.observations.clear()

    def __init__(self):
        self.cache = {}
        self.observations = {}
        self.submissions = {}
        self.tool_history = {}
        self.trace_history = {}
        self.ended_times = {}
        self.workflows = {}
        self.authors = {}
        self.run_engines = {}
        self.step_observations = {}
        from ..execution.thinking import ThinkingPhases

        self.thinking = ThinkingPhases()

    def clear(self):
        self.cache.clear()
        self.observations.clear()
        self.tool_history.clear()
        self.trace_history.clear()
        self.ended_times.clear()
        self.workflows.clear()
        self.authors.clear()
        self.run_engines.clear()
        self.step_observations.clear()

    def retain_messages(self, messages):
        """Bound projection caches to the displayed conversation, avoiding
        eviction/reload loops when history exceeds a fixed-size cache.
        """
        identifiers = {item.run_id for item in messages if item.run_id}
        self.cache = {key: value for key, value in self.cache.items() if key[0] in identifiers}
        for cache in (self.tool_history, self.trace_history, self.ended_times,
                      self.workflows, self.authors, self.run_engines, self.step_observations):
            for identifier in cache.keys() - identifiers:
                del cache[identifier]

    async def read_steps(self, handle, run):
        if run.status in self.MUTABLE_RUN_STATES:
            self.step_observations.pop(run.id, self.ABSENT)
            return await handle.steps.alist()
        if run.id not in self.step_observations:
            self.step_observations[run.id] = tuple(await handle.steps.alist())
        return self.step_observations[run.id]

    def invalidate(self, session_id):
        self.observations.pop(session_id, self.ABSENT)

    async def run_display(
        self, session, run_id: str, *, run=ABSENT, requested_at=ABSENT, steps=()
    ) -> tuple[str, float | None]:
        key = (run_id, requested_at)
        cached = self.cache.get(key)
        if cached is not self.ABSENT:
            return cached
        try:
            if run is self.ABSENT:
                run = await (await session.run.aload(run_id)).aget_data()
        except FileNotFoundError:
            return (
                self.EMPTY_TEXT,
                self.ABSENT,
            )  # Retention can remove old execution history.
        hidden_steps = self.thinking.hidden_steps(run_id, steps)
        completions = [item for item in CompletionRecords.results(run.metadata)
                       if item.step_id not in hidden_steps]
        self.run_engines[run_id] = run.engine
        if AgentBinding.METADATA_FIELD in run.metadata:
            binding = AgentBinding.from_metadata(run.metadata)
            self.authors[run_id] = AgentProfile.title(binding.agent_id, binding.definition)
        reasoning = self.PARAGRAPH.join(
            text for item in completions if (text := item.reasoning_content)
        )
        self.trace_history[run_id] = tuple(
            TraceItem.thought(item.id, text, started_at=item.started_at)
            for item in completions
            if (text := item.reasoning_content)
        )
        elapsed = self.ABSENT
        active = str(run.status) in (RunStatus.PENDING, RunStatus.RUNNING)
        started_at = requested_at or run.started_at
        if started_at and (run.ended_at or active):
            end = (
                datetime.fromisoformat(run.ended_at)
                if run.ended_at
                else datetime.now(timezone.utc)
            )
            elapsed = max(
                self.ZERO_ELAPSED,
                (end - datetime.fromisoformat(started_at)).total_seconds(),
            )
        result = reasoning, elapsed
        if not active:
            self.ended_times[run_id] = run.ended_at or self.EMPTY_TEXT
            self.cache[key] = result
        return result

    async def messages(
        self,
        session,
        messages,
        author=EMPTY_TEXT,
        *,
        latest=ABSENT,
        latest_steps=(),
        stopped_runs=(),
        run_records=None,
        run_steps=None,
        graph_engines=(),
    ):
        self.retain_messages(messages)
        run_records, run_steps = run_records or {}, run_steps or {}
        visible = []
        requests = {
            run_id: self.submissions.get(message.id, message.created_at)
            for message in messages
            if message.role == MessageRole.USER and not is_instruction(message)
            for run_id in RequestAdmission.run_ids(message)
        }
        authors = {}
        for message in messages:
            if RequestAdmission.present(message):
                admission = RequestAdmission.from_message(message)
                if admission.dispatched is not self.ABSENT:
                    authors.update(
                        (identifier, agent)
                        for agent, identifier in admission.dispatched.run_ids.items()
                    )
        for message in messages:
            role = str(message.role)
            if role not in (MessageRole.USER, MessageRole.ASSISTANT):
                continue
            elapsed = self.ABSENT
            current_run = run_records.get(message.run_id)
            if current_run is None and latest is not None and latest.id == message.run_id:
                current_run = latest
            steps_for_run = run_steps.get(message.run_id, latest_steps if current_run is latest else ())
            workflow, workflow_states = self.EMPTY_TEXT, ()
            if role == MessageRole.ASSISTANT and message.run_id:
                active = str(message.status) in (
                    MessageStatus.STREAMING,
                    MessageStatus.COMMITTED,
                    MessageStatus.PAUSED,
                )
                reasoning, elapsed = await self.run_display(
                    session,
                    message.run_id,
                    run=current_run,
                    requested_at=requests.get(message.run_id),
                    steps=steps_for_run,
                )
                displayed_reasoning = self.thinking.visible(
                    message.run_id, reasoning, active=active, content=message.content
                )
                # Keep the provider's visible reasoning when no final answer was
                # returned. Do not promote it to an answer or expose helper Steps.
                if not (message.status in (MessageStatus.COMPLETED, MessageStatus.FAILED)
                        and not split_followup(message.content).content.strip()):
                    reasoning = displayed_reasoning
                tools = ()
                if active:
                    if current_run is not self.ABSENT:
                        tools = tuple(
                            step
                            for step in display_steps(steps_for_run)
                            if step.kind == StepKind.TOOL
                        )
                        if str(current_run.status) == RunStatus.PAUSED:
                            self.tool_history[message.run_id] = tools
                    elif message.run_id in self.tool_history:
                        tools = self.tool_history[message.run_id]
                    elif session is not self.ABSENT:
                        try:
                            steps = await (
                                await session.run.aload(message.run_id)
                            ).steps.alist()
                            steps_for_run = steps
                            tools = tuple(
                                step
                                for step in display_steps(steps)
                                if step.kind == StepKind.TOOL
                            )
                            if str(message.status) == MessageStatus.PAUSED:
                                self.tool_history[message.run_id] = tools
                        except FileNotFoundError:
                            pass
                if not active and message.run_id in self.workflows:
                    observation = self.workflows[message.run_id]
                elif steps_for_run or (current_run is not None and str(current_run.status) in self.MUTABLE_RUN_STATES):
                    observation = WorkflowObservation.read(steps_for_run)
                elif message.run_id in self.workflows:
                    observation = self.workflows[message.run_id]
                else:
                    observation = WorkflowObservation()
                if (not active and message.run_id not in self.workflows
                        and self.run_engines.get(message.run_id) in graph_engines
                        and session is not None):
                    try:
                        history_steps = steps_for_run or await (await session.run.aload(message.run_id)).steps.alist()
                        observation = WorkflowObservation.read(history_steps)
                    except FileNotFoundError:
                        pass
                if not active or message.status == MessageStatus.PAUSED:
                    self.workflows[message.run_id] = observation
                workflow, workflow_states = observation.text, observation.states
                if message.run_id in self.authors:
                    authors[message.run_id] = self.authors[message.run_id]
                summarizing = active and self.thinking.activity(current_run, steps_for_run) == ResponseActivity.SUMMARIZING
                summaries = tuple(
                    TraceItem(step.id, TraceItem.SUMMARY, self.EMPTY_TEXT, step.status, step.started_at)
                    for step in display_steps(
                        step for step in steps_for_run if self.thinking.summary_step(step)
                    )
                ) if active else ()
                if reasoning or tools or workflow or summarizing or summaries:
                    trace = merge_trace(
                        self.thinking.trace(
                            message.run_id, self.trace_history.get(message.run_id, ())
                        ),
                        tool_trace(tools),
                        summaries,
                    )
                    visible.append(
                        ReasoningMessage.create(
                            message.id, reasoning, tools=tools, trace=trace,
                            author=authors.get(message.run_id, self.EMPTY_TEXT), workflow=workflow,
                            workflow_states=workflow_states,
                            phase=ResponseActivity.SUMMARIZING if summarizing else self.EMPTY_TEXT,
                        )
                    )
            status = str(
                RunInstruction.from_message(message).status
                if is_instruction(message)
                else message.status
            )
            if status == MessageStatus.PAUSED and message.run_id in stopped_runs:
                status = MessageStatus.INTERRUPTED.value
            phase = (
                self.thinking.activity(current_run, steps_for_run)
                if role == MessageRole.ASSISTANT
                and current_run is not self.ABSENT
                and status in (MessageStatus.STREAMING, MessageStatus.COMMITTED)
                else self.EMPTY_TEXT
            )
            timestamp = (
                message.created_at
                if role == MessageRole.USER
                else (
                    (self.ended_times.get(message.run_id) or message.created_at)
                    if status == MessageStatus.FAILED
                    else self.EMPTY_TEXT
                )
            )
            visible.append(
                ChatMessage(
                    role,
                    message.content or self.EMPTY_TEXT,
                    timestamp,
                    status=status,
                    id=message.id,
                    author=(
                        author
                        if role == MessageRole.USER
                        else authors.get(message.run_id, self.EMPTY_TEXT)
                    ),
                    elapsed_seconds=elapsed,
                    phase=phase,
                    run_id=message.run_id or self.EMPTY_TEXT,
                )
            )
        from ..execution.turns import TurnIndex

        return TurnIndex(messages).display(visible)

    async def read(
        self, project, sessions, selected_id, *, config, engines, title_errors
    ):
        from llm.services.query import Query

        data = await project.aget_data()
        observations, summaries = [], []
        for identifier, session in tuple(sessions.items()):
            cached = self.observations.get(identifier)
            if (
                identifier != selected_id
                and cached
                and monotonic() - cached.observed_at < self.OBSERVATION_SECONDS
            ):
                item = cached.value
            else:
                record = await session.aget_data()
                runtime = await session.run.astatus(queued_limit=self.QUEUED_IDS_LIMIT)
                latest = await session.run.alist(
                    query=Query(
                        descending=self.NEWEST_FIRST, limit=self.LATEST_RUN_LIMIT
                    )
                )
                handle = latest[0] if latest else self.ABSENT
                run = (
                    display_run(record, await handle.aget_data())
                    if handle
                    else self.ABSENT
                )
                question_handle = (
                    handle
                    if run and str(run.status) == RunStatus.PAUSED
                    else self.ABSENT
                )
                target = StopIntent(record.metadata).review_target
                if target and not runtime.active_runs and not runtime.queued_count:
                    try:
                        candidate = (
                            handle
                            if run and run.id == target
                            else await session.run.aload(target)
                        )
                        if str((await candidate.aget_data()).status) in (
                            RunStatus.PAUSED,
                            RunStatus.INTERRUPTED,
                            RunStatus.FAILED,
                        ):
                            question_handle = candidate
                    except FileNotFoundError:
                        pass
                paused = {
                    handle.id: handle
                    for handle in await session.run.alist(
                        query=Query(status=RunStatus.PAUSED)
                    )
                }
                if question_handle is not self.ABSENT:
                    paused[question_handle.id] = question_handle
                pending = []
                for paused_handle in paused.values():
                    pending.extend(
                        QuestionPresentation.stored_request(paused_handle.id, request)
                        for request in await paused_handle.ainteractions(
                            pending_only=self.PENDING_ONLY
                        )
                    )
                pending = tuple(pending)
                item = SessionObservation(
                    identifier, record, runtime, run, handle, pending
                )
                self.observations[identifier] = CachedSessionObservation(
                    monotonic(), item
                )
            observations.append(item)
            status = (
                waiting_status(item.questions)
                if item.questions
                else str(item.runtime.status)
            )
            if (
                not item.runtime.active_runs
                and item.run
                and item.run.id in StopIntent(item.record.metadata).stopped_runs
            ):
                status = str(item.run.status)
            summaries.append(
                SessionSummary(
                    identifier, item.record.title, status, item.runtime.queued_count,
                    tuple(item.record.agent_ids),
                    ParticipantGroups.read(item.record.metadata, item.record.agent_ids),
                )
            )
        selected = next(
            (item for item in observations if item.session_id == selected_id),
            self.ABSENT,
        )
        messages, summary, questions = (), self.ABSENT, ()
        if selected:
            session = sessions[selected_id]
            steps = await self.read_steps(selected.handle, selected.run) if selected.run else ()
            run_records = {selected.run.id: selected.run} if selected.run else {}
            run_steps = {selected.run.id: steps} if selected.run else {}
            for active in selected.runtime.active_runs:
                if active.run_id not in run_records:
                    handle = await session.run.aload(active.run_id)
                    run_records[active.run_id] = display_run(selected.record, await handle.aget_data())
                    run_steps[active.run_id] = await self.read_steps(handle, run_records[active.run_id])
            from llm.core.agents import AgentMode
            from llm.engines.base import agent_mode
            from ..execution.environment import underlying_engine

            messages = await self.messages(
                session,
                await session.aconversation(),
                config.user_profile.display_name,
                latest=selected.run,
                latest_steps=steps,
                stopped_runs=StopIntent(selected.record.metadata).stopped_runs,
                run_records=run_records, run_steps=run_steps,
                graph_engines=tuple(name for name in engines.names()
                    if agent_mode(underlying_engine(engines.get(name))) == AgentMode.WORKFLOW),
            )
            if selected.run:
                run = selected.run
                questions = selected.questions
                _, elapsed = await self.run_display(session, run.id, run=run)
                summary = RunSummary(
                    run.id,
                    str(run.status),
                    run.engine,
                    elapsed,
                    any(
                        active.run_id == run.id
                        for active in selected.runtime.active_runs
                    ),
                    display_steps(steps),
                    str(run.error) if run.error else self.EMPTY_TEXT,
                )
        definitions = {
            identifier: definition
            for identifier, definition in (await project.agents.alist()).items()
            if not identifier.startswith(HubEngineIdentity.INTERNAL_PREFIX)
        }
        definition = definitions.get(
            config.agent_id, next(iter(definitions.values()), self.ABSENT)
        )
        model, model_required = (
            agent_model(engines, definition)
            if definition
            else (self.EMPTY_TEXT, self.NO_MODEL_REQUIRED)
        )
        snapshot = HubSnapshot(
            project_id=data.id,
            project_title=data.title,
            model=model,
            storage=data.conversation_storage,
            sessions=tuple(summaries),
            selected_id=selected_id,
            messages=messages,
            run=summary,
            status=next(
                (s.status for s in summaries if s.id == selected_id),
                SessionStatus.IDLE.value,
            ),
            queued_count=(
                selected.runtime.queued_count if selected else self.NO_QUEUED_REQUESTS
            ),
            model_required=model_required if selected else self.NO_MODEL_REQUIRED,
            title_error=title_errors.get(selected_id),
            engines=selected_engines(data.config, engines.names()),
            file_root=str(
                project.paths.root
                if config.file_root is self.ABSENT
                else config.file_root
            ),
            components=tuple(data.components),
            questions=questions,
            agents=tuple(
                AgentSummary(
                    identifier,
                    AgentDefinition.engine(definition),
                    AgentDefinition.purpose(definition),
                    AgentProfile.title(identifier, definition),
                )
                for identifier, definition in definitions.items()
            ),
            participant_ids=tuple(selected.record.agent_ids) if selected else (),
            active_run_ids=tuple(item.run_id for item in selected.runtime.active_runs) if selected else (),
        )
        return snapshot, tuple(observations)
