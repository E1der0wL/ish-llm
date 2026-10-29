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
from dataclasses import dataclass, replace
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from llm.engines.events import EngineEvent, EngineEventType
from llm.core.results import OutputVisibility
from .reads import ObservationReads, ReadKind
from .projection import ConversationProjection, MessageProjection, SessionProjection

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
from ..interactions.questions import pending_requests, waiting_status
from ..execution.stopping import display_run, StopIntent


@dataclass(slots=True)
class SessionObservation:
    session_id: str
    record: Any
    runtime: Any
    run: Any
    handle: Any
    questions: tuple = ()


class SnapshotReader:
    ABSENT = None
    EMPTY_TEXT = ""
    PARAGRAPH = "\n\n"
    CACHE_LIMIT = 1024
    VISITED_SESSIONS = 4
    DETAIL_NEIGHBORS = 24
    ZERO_ELAPSED = 0.0
    QUEUED_IDS_LIMIT = 0
    LATEST_RUN_LIMIT = 1
    NEWEST_FIRST = True
    NO_QUEUED_REQUESTS = 0
    NO_MODEL_REQUIRED = False
    MUTABLE_RUN_STATES = frozenset((RunStatus.PENDING, RunStatus.RUNNING, RunStatus.PAUSED))
    LIVE_MESSAGE_STATES = frozenset((MessageStatus.QUEUED, MessageStatus.COMMITTED, MessageStatus.STREAMING))

    def record_submission(self, identifier, timestamp):
        self.submissions[identifier] = timestamp
        if len(self.submissions) > SnapshotReader.CACHE_LIMIT:
            self.submissions.pop(next(iter(self.submissions)))

    def invalidate_dropped_events(self):
        for projection in set((self.projection, *self.projections.values())):
            focus = projection.focus_id
            projection.clear()
            projection.focus_id = focus
        self.session_summaries.clear()
        self.thinking.runs.clear()
        self.reads.clear()
        self.step_observations.clear()
        self.drafts.invalidate()

    def __init__(self):
        self.cache = {}
        self.reads = ObservationReads()
        self.run_handles = OrderedDict()
        self.projection = ConversationProjection()
        self.projections = OrderedDict()
        self.session_summaries = {}
        self.selected_conversation = None
        self.agent_definitions = {}
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
        from .drafts import AnswerDrafts
        self.drafts = AnswerDrafts()

    def clear(self):
        self.projections.clear()
        self.projection.clear()
        self.session_summaries.clear()
        self.drafts.clear()
        self.cache.clear()
        self.reads.clear()
        self.run_handles.clear()
        self.selected_conversation = None
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
        for projection in self.projections.values():
            identifiers.update(item.run_id for item in (projection.input_records or ()) if item.run_id)
        self.cache = {key: value for key, value in self.cache.items() if key[0] in identifiers}
        for cache in (self.tool_history, self.trace_history, self.ended_times,
                      self.workflows, self.authors, self.run_engines, self.step_observations):
            for identifier in cache.keys() - identifiers:
                del cache[identifier]

    async def read_steps(self, handle, run):
        if run.status in self.MUTABLE_RUN_STATES:
            self.step_observations.pop(run.id, self.ABSENT)
            return await self.reads.read((ReadKind.STEPS, run.id), handle.steps.alist)
        if run.id not in self.step_observations:
            self.step_observations[run.id] = tuple(await handle.steps.alist())
        return self.step_observations[run.id]

    def invalidate(self, session_id):
        self.session_summaries.pop(session_id, None)
        projection = self.projections.get(session_id)
        if projection is not None:
            projection.changed()
        elif session_id == self.selected_conversation:
            self.projection.changed()
        self.reads.discard(ReadKind.SESSION, session_id)
        self.reads.discard(ReadKind.CONVERSATION, session_id)
        self.reads.discard(ReadKind.RUN, session_id)

    def invalidate_project(self):
        self.reads.discard(ReadKind.PROJECT)
        self.reads.discard(ReadKind.AGENTS)

    def _visited_projection(self, session_id):
        projection = self.projections.get(session_id)
        if projection is None:
            projection = self.projections[session_id] = ConversationProjection()
        self.projections.move_to_end(session_id)
        while len(self.projections) > self.VISITED_SESSIONS:
            expired, _ = self.projections.popitem(last=False)
            self.reads.discard(ReadKind.CONVERSATION, expired)
        return projection

    def focus_history(self, session_id, identifier):
        projection = self._visited_projection(session_id)
        if projection.focus_id == identifier:
            return False
        projection.focus_id = identifier
        projection.changed()
        return True

    def detail_window(self, messages):
        target = ReasoningMessage.assistant_id(self.projection.focus_id or '')
        index = next((i for i, message in enumerate(messages) if message.id == target), len(messages) - 1)
        return {message.id for message in messages[max(0, index - self.DETAIL_NEIGHBORS):
                                                   index + self.DETAIL_NEIGHBORS + 1]}

    def observe_run(self, run):
        projection = self.projections.get(run.session_id)
        if projection is not None:
            projection.changed(run.id)
        self.invalidate(run.session_id)
        self._remember_run(run)
        self.reads.discard(ReadKind.STEPS, run.id)
        self.step_observations.pop(run.id, None)

    def observe_engine(self, run, event):
        """Invalidate only records changed by persisted Engine observations."""
        if self._stale_run(run):
            return
        kind = EngineEvent.observed_type(event)
        projection = self.projections.get(run.session_id)
        if projection is not None:
            projection.changed(run.id)
        if kind != EngineEventType.TEXT_DELTA:
            self._remember_run(run)
        if kind in EngineEvent.PHASES or kind == EngineEventType.OUTPUT:
            self.reads.discard(ReadKind.STEPS, run.id)
            self.step_observations.pop(run.id, None)
        value = EngineEvent.observed_delta(event) or EngineEvent.observed_output(event)
        if value is not None and value.visibility == OutputVisibility.USER:
            self.reads.discard(ReadKind.CONVERSATION, run.session_id)
        if kind in (EngineEventType.PAUSED, EngineEventType.INTERACTION_CHANGED,
                    EngineEventType.STEERING_CHANGED):
            self.invalidate(run.session_id)
            self._remember_run(run)

    def _stale_run(self, run):
        previous = self.reads.entries.get((ReadKind.RUN, run.session_id, run.id))
        return (previous is not None and previous.value is not None
                and previous.value.status not in (RunStatus.PENDING, RunStatus.RUNNING)
                and run.status in (RunStatus.PENDING, RunStatus.RUNNING))

    def _remember_run(self, run):
        if self._stale_run(run):
            return  # Separate Engine/lifecycle queues may deliver an older phase late.
        self.reads.remember((ReadKind.RUN, run.session_id, run.id), deepcopy(run))

    async def read_run(self, session, session_id, run_id, *, handle=None):
        current = handle if handle is not None else self.run_handles.get(run_id)
        if current is None:
            current = await session.run.aload(run_id)
        self.run_handles[run_id] = current
        self.run_handles.move_to_end(run_id)
        while len(self.run_handles) > self.CACHE_LIMIT:
            self.run_handles.popitem(last=False)
        return current, await self.reads.read(
            (ReadKind.RUN, session_id, run_id), current.aget_data)

    async def read_session(self, identifier, session):
        from llm.services.query import Query

        record = await session.aget_data()
        latest = await session.run.alist(
            query=Query(descending=self.NEWEST_FIRST, limit=self.LATEST_RUN_LIMIT))
        handle = latest[0] if latest else self.ABSENT
        run = (display_run(record, (await self.read_run(
            session, identifier, handle.id, handle=handle))[1]) if handle else self.ABSENT)
        question_handle = handle if run and str(run.status) == RunStatus.PAUSED else self.ABSENT
        paused = {handle.id: handle for handle in await session.run.alist(
            query=Query(status=RunStatus.PAUSED))}
        # History queries can wait behind durable execution writes. Read the
        # live queue afterwards so an already dispatched request is not still
        # labelled queued in the frame assembled from those queries.
        runtime = await session.run.astatus(queued_limit=self.QUEUED_IDS_LIMIT)
        target = StopIntent(record.metadata).review_target
        if target and not runtime.active_runs and not runtime.queued_count:
            try:
                candidate, candidate_run = await self.read_run(
                    session, identifier, target, handle=handle if run and run.id == target else None)
                if str(candidate_run.status) in (RunStatus.PAUSED, RunStatus.INTERRUPTED, RunStatus.FAILED):
                    question_handle = candidate
            except FileNotFoundError:
                pass
        if question_handle is not self.ABSENT:
            paused[question_handle.id] = question_handle
        pending = []
        for paused_handle in paused.values():
            pending.extend(QuestionPresentation.stored_request(paused_handle.id, request)
                           for request in await pending_requests(paused_handle))
        return SessionObservation(identifier, record, runtime, run, handle, tuple(pending))

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
        active = str(run.status) in (RunStatus.PENDING, RunStatus.RUNNING)
        elapsed = self.elapsed(run, requested_at)
        result = reasoning, elapsed
        if not active:
            self.ended_times[run_id] = run.ended_at or self.EMPTY_TEXT
            self.cache[key] = result
        return result

    @classmethod
    def elapsed(cls, run, requested_at=None):
        """Clock-only refresh must not deserialize all completion evidence."""
        active = str(run.status) in (RunStatus.PENDING, RunStatus.RUNNING)
        started_at = requested_at or run.started_at
        if started_at and (run.ended_at or active):
            end = (
                datetime.fromisoformat(run.ended_at)
                if run.ended_at
                else datetime.now(timezone.utc)
            )
            return max(
                cls.ZERO_ELAPSED,
                (end - datetime.fromisoformat(started_at)).total_seconds(),
            )
        return cls.ABSENT

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
        drafts=None,
        detail_ids=None,
    ):
        revision = self.projection.revision
        base_parts = self.projection.parts
        run_records, run_steps = run_records or {}, run_steps or {}
        input_context = (author, tuple(stopped_runs), tuple(graph_engines),
                         frozenset(detail_ids) if detail_ids is not None else None)
        incremental = self.projection.incremental(messages, input_context)
        if not incremental:
            self.retain_messages(messages)
            self.projection.retain({message.id for message in messages})
        visible = []
        requests = self.projection.request_times if incremental else {
            run_id: self.submissions.get(message.id, message.created_at)
            for message in messages
            if message.role == MessageRole.USER and not is_instruction(message)
            for run_id in RequestAdmission.run_ids(message)
        }
        authors = dict(self.projection.request_authors) if incremental else {}
        for message in (() if incremental else messages):
            if RequestAdmission.present(message):
                admission = RequestAdmission.from_message(message)
                if admission.dispatched is not self.ABSENT:
                    for agent, identifier in admission.dispatched.run_ids.items():
                        if identifier not in self.authors:
                            self.authors[identifier] = AgentProfile.title(
                                agent, admission.binding(agent).definition)
                        authors[identifier] = self.authors[identifier]
        if not incremental:
            self.projection.index(messages, input_context, requests, dict(authors))
        changing = set(run_records) | ({latest.id} if latest is not None else set())
        selected_messages = (tuple(message for run_id in changing
                                   for message in self.projection.by_run.get(run_id, ()))
                             if incremental else messages)
        parts = {}
        for message in selected_messages:
            role = str(message.role)
            if role not in (MessageRole.USER, MessageRole.ASSISTANT):
                continue
            # Settled historical rows have no live Step/clock dependencies.
            # Active/paused/latest Runs always go through the normal projection.
            settled = (message.status not in (*self.LIVE_MESSAGE_STATES, MessageStatus.PAUSED)
                       and message.run_id not in run_records
                       and (latest is None or message.run_id != latest.id))
            detailed = (detail_ids is None or message.id in detail_ids or not settled
                        or message.id in self.projection.detailed)
            if message.run_id not in authors and message.run_id in self.authors:
                authors[message.run_id] = self.authors[message.run_id]
            key = (role, message.content, message.created_at, str(message.status),
                   message.run_id, author, requests.get(message.run_id),
                   authors.get(message.run_id),
                   str(RunInstruction.from_message(message).status) if is_instruction(message) else None,
                   detailed)
            cached = self.projection.settled.get(message.id) if settled else None
            if cached is not None and cached.key == key:
                visible.extend(cached.items)
                parts[message.id] = cached.items
                continue
            start = len(visible)
            elapsed = self.ABSENT
            current_run = run_records.get(message.run_id)
            if current_run is None and latest is not None and latest.id == message.run_id:
                current_run = latest
            steps_for_run = run_steps.get(message.run_id, latest_steps if current_run is latest else ())
            workflow, workflow_states = self.EMPTY_TEXT, ()
            if role == MessageRole.ASSISTANT and message.run_id and detailed:
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
                    draft=(drafts or {}).get(message.run_id)
                    if role == MessageRole.ASSISTANT and not message.content and status in (
                        MessageStatus.STREAMING, MessageStatus.COMMITTED) else None,
                )
            )
            parts[message.id] = tuple(visible[start:])
            if settled and revision == self.projection.revision:
                if detailed:
                    self.projection.detailed.add(message.id)
                self.projection.settled[message.id] = MessageProjection(
                    key, message.run_id, parts[message.id])
        if revision == self.projection.revision:
            self.projection.parts.update(parts)
            result = self.projection.display(parts)
            self.projection.full_refresh = False
            return result
        # An event arrived while awaiting a historical Run. Do not retain an
        # incomplete index; publish this observation through the normal owner.
        self.projection.full_refresh = True
        from ..execution.turns import TurnIndex
        assembled = {**base_parts, **parts}
        return TurnIndex(messages).display([
            row for message in messages for row in assembled.get(message.id, ())])

    async def read(
        self, project, sessions, selected_id, *, config, engines, title_errors,
        reconcile_terminal=True,
    ):
        data = await self.reads.read((ReadKind.PROJECT,), project.aget_data)
        observations, summaries = [], []
        for identifier in self.projections.keys() - sessions.keys():
            del self.projections[identifier]
            self.reads.discard(ReadKind.CONVERSATION, identifier)
        for identifier in self.session_summaries.keys() - sessions.keys():
            del self.session_summaries[identifier]
        for identifier, session in tuple(sessions.items()):
            item = await self.reads.read((ReadKind.SESSION, identifier),
                lambda: self.read_session(identifier, session),
                max_age=(ObservationReads.RECONCILE_SECONDS if identifier == selected_id
                         else ObservationReads.background_age(identifier)))
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
            cached = self.session_summaries.get(identifier)
            if cached is None or cached.observation is not item or cached.status != status:
                summary = SessionSummary(
                    identifier, item.record.title, status, item.runtime.queued_count,
                    tuple(item.record.agent_ids),
                    ParticipantGroups.read(item.record.metadata, item.record.agent_ids),
                )
                cached = SessionProjection(item, status, summary)
                self.session_summaries[identifier] = cached
            summaries.append(cached.summary)
        selected = next(
            (item for item in observations if item.session_id == selected_id),
            self.ABSENT,
        )
        messages, summary, questions = (), self.ABSENT, ()
        if self.selected_conversation != selected_id:
            # Cache only visited conversations, never prefetch another Session.
            self.projection = self._visited_projection(selected_id)
            self.selected_conversation = selected_id
        if selected:
            session = sessions[selected_id]
            if selected.run:
                _, latest = await self.read_run(session, selected_id, selected.run.id, handle=selected.handle)
                selected = replace(selected, run=display_run(selected.record, latest))
            steps = await self.read_steps(selected.handle, selected.run) if selected.run else ()
            run_records = {selected.run.id: selected.run} if selected.run else {}
            run_steps = {selected.run.id: steps} if selected.run else {}
            run_handles = {selected.run.id: selected.handle} if selected.run else {}
            for active in selected.runtime.active_runs:
                if active.run_id not in run_records:
                    handle, record = await self.read_run(session, selected_id, active.run_id)
                    run_handles[active.run_id] = handle
                    run_records[active.run_id] = display_run(selected.record, record)
                    run_steps[active.run_id] = await self.read_steps(handle, run_records[active.run_id])
            from llm.core.agents import AgentMode
            from llm.engines.base import agent_mode
            from ..execution.environment import underlying_engine
            from llm.engines.loop import LoopEngine

            drafts = {}
            for identifier, record in run_records.items():
                if record.status != RunStatus.RUNNING:
                    self.drafts.forget(identifier)
                    continue
                if (not HubEngineIdentity.internal(record.engine)
                        and isinstance(underlying_engine(engines.get(record.engine)), LoopEngine)):
                    handle = run_handles[identifier]
                    drafts[identifier] = await self.drafts.read(handle, record, run_steps[identifier])

            conversation = await self.reads.read(
                (ReadKind.CONVERSATION, selected_id), session.aconversation)
            active_ids = {item.run_id for item in selected.runtime.active_runs}
            if reconcile_terminal and any(
                message.role == MessageRole.ASSISTANT and message.run_id in active_ids
                and message.status not in self.LIVE_MESSAGE_STATES
                for message in conversation
            ):
                # Conversation and Run lifecycle notifications use separate
                # queues. Never publish a finished answer with cached active
                # recipients: the next Enter would incorrectly open steering.
                self.invalidate(selected_id)
                return await self.read(project, sessions, selected_id, config=config,
                    engines=engines, title_errors=title_errors, reconcile_terminal=False)
            now = datetime.now(timezone.utc)
            clocks = tuple((identifier, int((now - datetime.fromisoformat(run.started_at)).total_seconds()))
                           for identifier, run in run_records.items()
                           if run.status in (RunStatus.PENDING, RunStatus.RUNNING) and run.started_at)
            context = (self.projection.revision, config.user_profile.display_name,
                       tuple(StopIntent(selected.record.metadata).stopped_runs),
                       tuple((key, id(value)) for key, value in run_records.items()),
                       tuple((key, id(value)) for key, value in run_steps.items()),
                       tuple(drafts.items()), clocks)
            if self.projection.reusable(conversation, context):
                messages = self.projection.messages
            else:
                messages = await self.messages(
                    session,
                    conversation,
                    config.user_profile.display_name,
                    latest=selected.run,
                    latest_steps=steps,
                    stopped_runs=StopIntent(selected.record.metadata).stopped_runs,
                    run_records=run_records, run_steps=run_steps,
                    drafts=drafts,
                    graph_engines=tuple(name for name in engines.names()
                        if agent_mode(underlying_engine(engines.get(name))) == AgentMode.WORKFLOW),
                    detail_ids=self.detail_window(conversation),
                )
                self.projection.remember(conversation, context, messages,
                    (*run_records.values(), *run_steps.values()))
            if selected.run:
                run = selected.run
                questions = selected.questions
                elapsed = self.elapsed(run)
                summary = RunSummary(
                    run.id,
                    str(run.status),
                    run.engine,
                    int(elapsed) if elapsed is not None else None,
                    any(
                        active.run_id == run.id
                        for active in selected.runtime.active_runs
                    ),
                    display_steps(steps),
                    str(run.error) if run.error else self.EMPTY_TEXT,
                )
        self.agent_definitions = await self.reads.read((ReadKind.AGENTS,), project.agents.alist)
        definitions = {
            identifier: definition
            for identifier, definition in self.agent_definitions.items()
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
