"""Session-bound Hub operations through LargeLanguageModel's public facade."""
from hub.backend.execution.engine_selection import HubEngineIdentity

from hub.locales.codes import LanguageCode

from hub.locales.keys import MessageKey

from llm.core.continuations import ResumeRecord

from llm.core.models import MessageStatus, RunStatus
from llm.core.configuration import UNSET
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..model import ChatMessage, HubSnapshot, SubmissionResult, SessionSummary
from ..locales import Language
from ..config.profile import UserProfile
from .execution.engine_selection import requires_model, selected_engines
from ..config.general import GeneralSettings
from ..config.view_state import ViewStateStore
from ..config.workspace import WorkspaceSelection
from .observability.notifications import NotificationBuffer
from .bootstrap import create_backend
from .project.agents import DEFAULT_AGENT


@dataclass(frozen=True, slots=True)
class HubConfig:
    """Host choices; explicit factory collections extend or replace core registrations.

    Only None means no supplied collection. Empty collections add no overrides;
    nonempty collections retain their entries regardless of custom truthiness.
    Factories are validated here and constructed later on the backend thread.
    """
    workspace: str | Path = field(default_factory=lambda: Path.home() / ".ish/hub-workspace")
    agent_id: str = DEFAULT_AGENT
    agent_definition: dict | None = None
    model: str | None = None
    api_base: str | None = None
    project_id: str | None = WorkspaceSelection.NO_PROJECT
    session_id: str | None = WorkspaceSelection.NO_SESSION
    language: str = LanguageCode.DEFAULT
    engine_factories: dict[str, Callable] | None = None
    component_factories: tuple[Callable, ...] | list[Callable] | None = None
    tool_classifier: Callable | None = None
    project_config: dict = field(default_factory=dict)
    file_root: str | Path | None = None
    auto_title: bool = True
    user_profile: UserProfile = field(default_factory=UserProfile)
    language_packs: dict[str, dict[str, str]] = field(default_factory=dict)
    _workspace_anchor: str | Path | None = field(default=WorkspaceSelection.UNBOUND_ANCHOR, repr=False)
    tool_retry_chooser: Callable | None = None
    tool_retry_evidence: dict[str, Callable] = field(default_factory=dict)
    log_sink: Callable | None = None

    def __post_init__(self) -> None:
        Language(self.language, self.language_packs)
        if self.file_root is not None:
            # Validate path input without changing its value or resolution time.
            value = str(self.file_root) if isinstance(self.file_root, str) else self.file_root
            Path(value)
            object.__setattr__(self, "file_root", value)
        if self.log_sink is not None and not callable(self.log_sink):
            raise TypeError("log_sink must be callable")
        if self.tool_retry_chooser is not None and not callable(self.tool_retry_chooser):
            raise TypeError("tool_retry_chooser must be callable")
        if not isinstance(self.tool_retry_evidence, dict) or any(not isinstance(name, str) or not name or not callable(provider)
               for name, provider in self.tool_retry_evidence.items()):
            raise TypeError("tool_retry_evidence maps Tool names to callable evidence providers")
        from llm.core.agents import AgentDefinition
        AgentDefinition.identifier(self.agent_id)
        if self.agent_id.startswith("_hub_"):
            raise ValueError("Internal Hub Agent IDs are reserved")
        if self.agent_definition is not None:
            from copy import deepcopy
            AgentDefinition.validate(self.agent_id, self.agent_definition)
            object.__setattr__(self, "agent_definition", deepcopy(self.agent_definition))
        if self.session_id and not self.project_id:
            raise ValueError("session_id requires project_id")
        if self.engine_factories is not None:
            if not isinstance(self.engine_factories, dict):
                raise TypeError("engine_factories must be a dictionary or None")
            if self.initial_engine not in (HubEngineIdentity.LOOP, "graph") and self.initial_engine not in self.engine_factories:
                raise ValueError("Initial engine must be registered in engine_factories")
            if any(not isinstance(name, str) or not name.strip() or name.startswith("_hub_") or not callable(factory)
                   for name, factory in self.engine_factories.items()):
                raise ValueError("Use nonempty engine names and callable factories; _hub_ is reserved")
        if self.component_factories is not None:
            if not isinstance(self.component_factories, (tuple, list)):
                raise TypeError("component_factories must be a tuple, list or None")
            if any(not callable(factory) for factory in self.component_factories):
                raise ValueError("Component factories must be callable")

    @property
    def launch_workspace(self):
        return self.workspace if self._workspace_anchor is WorkspaceSelection.UNBOUND_ANCHOR else self._workspace_anchor

    @property
    def initial_engine(self):
        """Creation preset only; requests always select a saved Project Agent."""
        return HubEngineIdentity.LOOP if self.agent_definition is None else self.agent_definition["engine"]


class HubRuntime:
    """All handles, subscriptions and locks belong to one backend event loop."""

    def __init__(self, config: HubConfig, backend_factory: Callable = create_backend) -> None:
        from ..config.preferences import configured_workspace
        config = configured_workspace(config)
        self.config = config
        self.t = Language(config.language, config.language_packs)
        self._factory = backend_factory
        self.backend = None
        self.project = None
        self.sessions = {}
        self.selected_id = ""
        self.dirty = asyncio.Event()
        self.lock = asyncio.Lock()
        self._subscriptions = []
        self._dropped_observations = 0
        from .observability.snapshot import SnapshotReader
        self.snapshot_reader = SnapshotReader()
        from .observability.failures import FailureDiagnostics
        self.failure_diagnostics = FailureDiagnostics(self)
        from .history import TurnHistory
        self.history = TurnHistory(self)
        self._opened_sessions = set()
        self._rag_jobs = {}
        self.notifications = NotificationBuffer()
        self.builtin_tools = None
        from .project.settings import SettingsService
        self.settings_service = SettingsService(self)
        preferences = self.settings_service.preferences.load()
        self.general = GeneralSettings(**preferences.get("general", {}))
        from .project.naming import SessionNamer
        self.namer = SessionNamer(self)

    def session_is_open(self, identifier):
        return identifier in self._opened_sessions

    def forget_open_sessions(self, identifiers):
        self._opened_sessions.difference_update(identifiers)

    async def _engine_event(self, run, event) -> None:
        self.snapshot_reader.thinking.observe(run, event)
        self.dirty.set()

    async def _run_event(self, event) -> None:
        self.snapshot_reader.invalidate(event.run.session_id)
        self.notifications.record_run(event.run)
        self.dirty.set()

    def _saved_selection(self, project_id=None):
        try:
            store = ViewStateStore(self.config.workspace)
            return store.load(project_id)[0] if project_id else store.last_project()
        except (OSError, ValueError, TypeError):
            return ""  # Corrupt UI state must not prevent opening the backend.

    def _questions_changed(self):
        self.notifications.observe_questions(self.builtin_tools.questions.pending)
        self.dirty.set()

    async def start(self) -> None:
        from .project.bootstrap import select_or_create
        self.backend = self._factory(self.config)
        from .tools.builtin import HubBuiltinTools
        if "builtin_tools" in self.backend.project_manager.components.names():
            component = self.backend.project_manager.components.get("builtin_tools")
            if isinstance(component, HubBuiltinTools):
                self.builtin_tools = component
                component.questions.changed = self._questions_changed
        await self.backend.__aenter__()
        for channel, callback in (("engine", self._engine_event), ("run", self._run_event)):
            self._subscriptions.append(self.backend.events.subscribe(
                callback, channel=channel, delivery="queued", buffer_size=128, overflow="drop_oldest"))
        if self.config.project_id:
            self.project = await self.backend.projects.aload(self.config.project_id)
        elif self.general.restore_last_project:
            previous = self._saved_selection()
            if previous:
                self.project = next((project for project in await self.backend.projects.alist() if project.id == previous), None)
        if self.project is None:
            self.project = await select_or_create(self.backend, self.config)
        await self.activate_project(self.project, session_id=self.config.session_id)

    async def settings(self, operation, *args):
        return await self.settings_service.execute(operation, *args)

    async def activate_project(self, project, *, session_id=None):
        data = await project.aget_data()
        if self.builtin_tools is not None and self.builtin_tools.name in data.components:
            # Prepare only the saved selection. Opening a Project never edits
            # its components or supplies a new enabled-Tool preset.
            await asyncio.to_thread(self.builtin_tools.resolve, data, "tools")
        sessions = []
        for session in await project.sessions.alist():
            record = await session.aget_data()
            if not record.metadata.get("hub_internal"):
                sessions.append(session)
        candidates = {session.id: session for session in sessions}
        previous = self._saved_selection(project.id) if self.general.restore_last_session else ""
        selected = session_id or (previous if previous in candidates else sessions[-1].id if sessions else "")
        if selected and selected not in candidates:
            raise ValueError("Session is not an active member of the selected Project")
        # Session start owns stale-state recovery and durable queued-request recovery.
        # Opening history alone does not start every Session in the Project.
        if selected:
            await candidates[selected].run.start()
        self.project, self.sessions, self.selected_id = project, candidates, selected
        self.snapshot_reader.clear()
        for session in sessions:
            self.notifications.register_session(project.id, session.id, (await session.aget_data()).title)
        if selected:
            self._opened_sessions.add(selected)
        self.dirty.set()

    async def select(self, session_id: str) -> None:
        if not session_id and not self.sessions:
            return
        session = self.sessions[session_id]
        await session.run.start()
        self._opened_sessions.add(session_id)
        self.selected_id = session_id
        self.dirty.set()

    async def new_session(self, mode: str = "new", title: str = "", source_id: str | None = None,
                          through_message_id: str | None = None) -> str:
        if mode == "clone":
            if not self.sessions:
                raise ValueError(self.t(MessageKey.SESSION_REQUIRED))
            source = self.sessions[source_id or self.selected_id]
            status = await source.run.astatus()
            if status.active_runs or status.queued_count or source.id in self.namer.tasks:
                raise ValueError(self.t(MessageKey.CLONE_BUSY))
            await source.run.shutdown()
            try:
                from .execution.turns import TurnIndex, edit_source
                from llm.services.history.turns import ConversationTurns
                records = await source.aconversation()
                revised = any(edit_source(m) or ResumeRecord.from_metadata(m.metadata) for m in records)
                index = TurnIndex(records)
                boundary = (next(i for i, turn in enumerate(index.turns) if turn.id == through_message_id)
                            if revised and through_message_id else None)
                session = await source.aclone(title=title or self.t(MessageKey.NEW_SESSION),
                    through_message_id=None if revised else through_message_id)
                if boundary is not None:
                    # The Hub context builder clones logical turns. Trim the
                    # freshly created inactive clone through public deletion.
                    groups = await session.aturns()
                    for group in reversed(groups[boundary + 1:]):
                        await session.adelete_turn(ConversationTurns.request(group).id)
            finally:
                await source.run.start()
        elif mode == "new":
            participants = sorted(identifier for identifier in await self.project.agents.alist()
                                  if not identifier.startswith("_hub_"))
            session = await self.project.sessions.acreate(title or self.t(MessageKey.NEW_SESSION), agent_ids=participants)
        else:
            raise ValueError(f"Unknown session operation: {mode}")
        data = await session.aget_data()
        metadata = {**data.metadata, "hub_auto_title": not bool(title) and self.config.auto_title}
        metadata.pop("hub_title_attempt", None)
        if mode == "clone":
            from llm.services.query import Query
            source_runs = await source.run.alist(query=Query(descending=True, limit=1))
            if source_runs:
                metadata["hub_title_agent"] = (await source_runs[0].aget_data()).agent_id
        await session.asave(metadata=metadata)
        self.sessions[session.id] = session
        self.notifications.register_session(self.project.id, session.id, (await session.aget_data()).title)
        self.selected_id = session.id
        await session.run.start()
        self._opened_sessions.add(session.id)
        self.dirty.set()
        if mode == "clone":
            self.namer.schedule(session, "clone")
        return session.id

    async def turns(self, session_id):
        return await self.history.rows(session_id)

    async def retry_turn(self, session_id, request_id, expected_run_id):
        return await self.history.retry(session_id, request_id, expected_run_id)

    async def edit_turn(self, session_id, request_id, text, expected_request_id):
        return await self.history.edit(session_id, request_id, text, expected_request_id)

    async def delete_turns_plan(self, session_id, identifiers):
        return await self.history.deletion_plan(session_id, identifiers)

    async def delete_turns(self, session_id, identifiers, expected_revision, *, progress=None):
        return await self.history.delete(session_id, identifiers, expected_revision, progress=progress)

    async def delete_turn_plan(self, session_id, request_id):
        return (await self.sessions[session_id].adelete_turn_plan(request_id)).to_dict()

    async def delete_turn(self, session_id, request_id, abandon_runs=(), expected_revision=None):
        session = self.sessions[session_id]
        status = await session.run.astatus()
        if status.active_runs or status.queued_count or session_id in self.namer.tasks:
            raise ValueError(self.t(MessageKey.HISTORY_BUSY))
        await session.run.shutdown()
        try:
            try:
                await session.adelete_turn(request_id, abandon_runs=abandon_runs, expected_revision=expected_revision)
            except ValueError as error:
                if str(error) == "Turn deletion plan changed; reload and confirm again":
                    raise ValueError(self.t(MessageKey.HISTORY_DELETE_CHANGED)) from error
                if str(error) == "Conversation turn is required by an unfinished resumable Run":
                    raise ValueError(self.t(MessageKey.HISTORY_CHECKPOINT_PROTECTED)) from error
                raise
            self.snapshot_reader.clear()
        finally:
            await session.run.start()
        self.dirty.set()
        return True

    async def cancel_request(self, session_id, request_id):
        session = self.sessions[session_id]
        request = await session.run.arequest(request_id)
        cancelled = await request.cancel()
        self.snapshot_reader.invalidate(session_id)
        self.dirty.set()
        if not cancelled:
            raise ValueError(self.t(MessageKey.HISTORY_CANCEL_UNAVAILABLE))
        return True

    async def project_activity(self):
        """Keep public activity records and add bounded provider diagnostics."""
        from .observability.provider_logs import read_provider_logs, associate_provider_logs
        project = self.project
        rows = [event.to_dict() for event in await project.aactivity(limit=300, newest_first=False)]
        workspace = getattr(self.backend, "workspace", self.config.workspace)
        provider = await asyncio.to_thread(read_provider_logs, workspace, project.id)
        return associate_provider_logs(rows, provider)

    async def diagnose_failures(self, project_id, session_id, run_ids):
        return await self.failure_diagnostics.read(project_id, session_id, run_ids)

    async def execution_catalog(self, session_id: str) -> dict:
        project = self.sessions[session_id].project if session_id else self.project
        definitions = await project.agents.alist()
        return {"agents": {identifier: {"engine": definition["engine"], "purpose": definition.get("purpose", "")}
                           for identifier, definition in definitions.items() if not identifier.startswith("_hub_")}}

    async def rename_session(self, session_id: str, title: str) -> str:
        if not title.strip():
            raise ValueError(self.t(MessageKey.SESSION_NAME_REQUIRED))
        session = self.sessions[session_id]
        # Stop an in-flight automatic title before saving the explicit title.
        task = self.namer.tasks.get(session_id)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        data = await session.aget_data()
        await session.asave(title=title.strip(), metadata={**data.metadata, "hub_auto_title": False})
        self.namer.errors.pop(session_id, None)
        self.notifications.register_session(self.project.id, session_id, title.strip())
        self.snapshot_reader.invalidate(session_id)
        self.dirty.set()
        return session_id

    async def delete_session(self, session_id: str) -> str:
        session = self.sessions[session_id]
        status = await session.run.astatus()
        if status.active_runs or status.queued_count or session_id in self.namer.tasks:
            raise ValueError(self.t(MessageKey.SESSION_BUSY))
        was_open = session_id in self._opened_sessions
        await session.run.shutdown()
        try:
            await session.adelete()
        except Exception:
            if was_open:
                await session.run.start()
            raise
        self.sessions.pop(session_id)
        self._opened_sessions.discard(session_id)
        self.notifications.forget_session(session_id)
        self.namer.errors.pop(session_id, None)
        if self.selected_id == session_id:
            if self.sessions:
                await self.select(next(iter(self.sessions)))
            else:
                self.selected_id = ""
        self.dirty.set()
        return self.selected_id

    async def manage_tools(self, project_id, action, argument="", expected_version=None):
        from .tools.management import ToolManagement
        project = await self.backend.projects.aload(project_id)
        tools = await ToolManagement.for_project(project, root=self.config.file_root, builtin=self.builtin_tools)
        return await tools.execute(action, argument, expected_version)

    async def manage_records(self, project_id, name, action, argument="", expected_version=None, *, progress=None):
        from .project.record_management import RecordManagement
        project = await self.backend.projects.aload(project_id)
        manager = await RecordManagement.for_project(project, name, root=self.config.file_root, rag_jobs=self._rag_jobs)
        if name == "rag":
            return await manager.execute(action, argument, expected_version, progress=progress)
        return await manager.execute(action, argument, expected_version)

    async def submit(self, session_id: str, text: str, agent_id: str | None = None,
                     request_context=UNSET) -> str:
        selected = self.config.agent_id if agent_id is None else agent_id
        return await self._submit_recipients(session_id, text, [selected], request_context)

    async def _submit_recipients(self, session_id, text, recipients, request_context):
        """Application-selected recipients share one durable Backend admission."""
        if not text.strip():
            raise ValueError(self.t(MessageKey.EMPTY_MESSAGE))
        if session_id not in self.sessions:
            raise ValueError(self.t(MessageKey.SESSION_REQUIRED))
        session = self.sessions[session_id]
        project = await session.project.aget_data()
        config = (await session.aget_data()).config
        from .execution.engine_selection import agent_model
        for selected in recipients:
            if selected.startswith("_hub_"):
                raise ValueError("Internal Agent cannot receive conversation requests")
            definition = await session.project.agents.aload(selected)
            if agent_model(self.backend.engines, project.config, definition, config)[1]:
                raise ValueError(self.t(MessageKey.MODEL_REQUIRED))
        request = await session.run.submit(text, recipients=recipients, request_context=request_context)
        self.snapshot_reader.invalidate(session_id)
        self.dirty.set()
        return request.id

    async def submit_input(self, session_id: str, text: str, agent_id: str,
                           request_context=UNSET, check_running: bool = True) -> SubmissionResult:
        """Check and admit in one UI command; acknowledge only persisted input."""
        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).isoformat()
        if check_running:
            state = await self.submission_state(session_id, agent_id)
            if state["run_id"]:
                return SubmissionResult(run_id=state["run_id"])
        identifier = await self.submit(session_id, text, agent_id, request_context)
        self.snapshot_reader.submissions[identifier] = timestamp
        if len(self.snapshot_reader.submissions) > 1024:
            self.snapshot_reader.submissions.pop(next(iter(self.snapshot_reader.submissions)))
        # No extra reads after admission: a later read failure must not make a
        # durable request look rejected. The next snapshot supplies exact state.
        return SubmissionResult(message=ChatMessage("user", text, timestamp,
            status=MessageStatus.QUEUED.value, id=identifier, author=self.config.user_profile.display_name))

    async def submission_state(self, session_id: str, agent_id: str | None = None) -> dict:
        status = await self.sessions[session_id].run.astatus(queued_limit=0)
        selected = self.config.agent_id if agent_id is None else agent_id
        return {"run_id": next((run.run_id for run in status.active_runs if run.agent_id == selected), None)}

    async def instruction_targets(self, session_id: str, run_id: str | None = None) -> tuple[str, tuple[tuple[str, str], ...]]:
        session = self.sessions[session_id]
        status = await session.run.astatus()
        selected = next((run for run in status.active_runs if (
            run.run_id == run_id if run_id is not None else run.agent_id == self.config.agent_id)), None)
        if selected is None:
            raise ValueError(self.t(MessageKey.NO_ACTIVE_RUN))
        run = await session.run.aload(selected.run_id)
        targets = tuple((target.id, target.node_path or target.node_id or target.engine)
                        for target in await run.ainstruction_targets()
                        if target.accepting and str(target.mode) == "consume")
        if not targets:
            raise ValueError(self.t(MessageKey.NO_TARGETS))
        return selected.run_id, targets

    async def steer(self, session_id: str, run_id: str, text: str, targets: list[str]) -> str:
        result = await self.sessions[session_id].run.steer(run_id, text, targets=targets)
        self.dirty.set()
        return result.id

    async def interrupt(self, session_id: str) -> bool:
        from .execution.stopping import stop_paused
        session = self.sessions[session_id]
        result = await session.run.interrupt()
        if not result:
            result = await stop_paused(session)
        self.snapshot_reader.invalidate(session_id)
        self.dirty.set()
        return result

    async def resume_latest(self, session_id: str):
        from .execution.stopping import display_run
        await self.history.require_idle(session_id)
        index = await self.history.index(session_id)
        if not index.turns:
            return None
        turn = index.turns[-1]
        session = self.sessions[session_id]
        record = await session.aget_data()
        candidates = [run for _, run in (await self.history.attempts(session, turn)).values()
                      if run is not None and str(display_run(record, run).status) == RunStatus.INTERRUPTED]
        if len(candidates) != 1:
            return None  # History offers explicit Run selection for ambiguous retries.
        return await self.history.retry(session_id, turn.id, candidates[0].id)

    async def approval_details(self, request_data):
        from .interactions.approval import review_text
        return await asyncio.to_thread(review_text, request_data)

    async def answer_question(self, project_id, session_id, run_id, request_data, option_id, value, *, acknowledged=None):
        from llm.core.interactions import InteractionOption, InteractionRequest
        if project_id != self.project.id:
            raise ValueError("Project changed; reload the question")
        request = InteractionRequest.from_dict(request_data)
        if self.builtin_tools and request.id in self.builtin_tools.questions.pending:
            self.builtin_tools.questions.answer(project_id, session_id, run_id, request, option_id, value)
            if acknowledged:
                acknowledged()
        else:
            session = self.sessions[session_id]
            run = await session.run.aload(run_id)
            await run.arespond(request.respond(option_id, value=value))
            # The decision is durable now. Resume admission may perform much
            # more I/O; its completion must not gate answer acknowledgement.
            self.snapshot_reader.invalidate(session_id)
            self.dirty.set()
            if acknowledged:
                acknowledged()
            denied = next(option for option in request.options if option.id == option_id).effect == InteractionOption.EFFECT_DENY
            if not await run.ainteractions(pending_only=True):
                if not denied:
                    agent_id = (await run.aget_data()).agent_id
                    # The backend validates persisted user decisions, including
                    # uncertain-effect approval. Never invent retry_nodes here.
                    await session.run.resume(run_id, agent_id=agent_id, partial_output=True)
                from .execution.stopping import finish_review
                await finish_review(session, run_id)
        self.dirty.set()
        return True

    def _reconcile_snapshot(self, project_id, observations):
        for item in observations:
            identifier, record, runtime, run = item.session_id, item.record, item.runtime, item.run
            self.notifications.observe_session(project_id, identifier, record.title, run, item.questions)
            if record.metadata.get("hub_auto_title") and not runtime.active_runs:
                if run is not None and str(run.status) == RunStatus.COMPLETED:
                    self.namer.schedule(self.sessions[identifier], run.id)
                elif run is None and record.metadata.get("hub_title_engine"):
                    self.namer.schedule(self.sessions[identifier], "clone")

    async def snapshot(self) -> HubSnapshot:
        dropped = sum(subscription.stats["dropped"] for subscription in self._subscriptions)
        if dropped != self._dropped_observations:
            # A missing text delta makes a cached thinking phase unreliable.
            # Rebuild the display from durable messages/completions below.
            self.snapshot_reader.thinking.runs.clear()
            self.snapshot_reader.observations.clear()
            self._dropped_observations = dropped
        from dataclasses import replace
        snapshot, observations = await self.snapshot_reader.read(
            self.project, self.sessions, self.selected_id, config=self.config,
            engines=self.backend.engines, title_errors=self.namer.errors)
        self._reconcile_snapshot(snapshot.project_id, observations)
        questions = self.builtin_tools.questions.views(self.project.id, self.selected_id) if self.builtin_tools else ()
        questions = questions + snapshot.questions
        from .execution.retries import RetryMonitor
        monitor = getattr(self.backend.services, "observability_sink", None)
        if snapshot.run and snapshot.run.active and isinstance(monitor, RetryMonitor):
            snapshot = replace(snapshot, run=replace(snapshot.run, retry=monitor.view(snapshot.run.id)))
        from .interactions.questions import waiting_status
        waiting = {item.session_id for item in self.builtin_tools.questions.pending.values()
                   if item.project_id == snapshot.project_id} if self.builtin_tools else set()
        sessions = tuple(replace(item, status=SessionSummary.WAITING_INPUT) if item.id in waiting else item for item in snapshot.sessions)
        return replace(snapshot, notifications=self.notifications.items(), questions=questions, sessions=sessions,
                       status=waiting_status(questions) if questions else snapshot.status)

    async def close(self) -> None:
        if self.builtin_tools:
            self.builtin_tools.questions.close()
        await self.namer.close()
        for subscription in self._subscriptions:
            await subscription.aclose()
        self._subscriptions.clear()
        if self.backend is not None:
            await self.backend.shutdown()
        if self.builtin_tools:
            await self.builtin_tools.close()
