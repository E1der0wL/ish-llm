"""Session-bound Hub operations through LargeLanguageModel's public facade."""

from hub.backend.session_contracts import SessionMode
from llm.llm import LargeLanguageModel
from llm.core.agents import AgentDefinition
from llm.core.models import MessageRole
from llm.core.steering import SteeringMode
from llm.components.rag.identity import RAGIdentity
from llm.services.composition import BackendServices
from llm.services.lifecycle.sessions import SessionManager
from llm.services.runtime.subscription_state import (
    EventChannel,
    EventDelivery,
    EventOverflow,
    SubscriptionStats,
)
from ..config.preference_records import PreferenceSnapshot
from .execution.engine_selection import EngineSelection
from .runtime_views import RuntimeAgentCatalog

from hub.backend.project.title_values import TitleSessionMetadata, TitlePresentation

from hub.backend.execution.engine_selection import HubEngineIdentity

from hub.locales.codes import LanguageCode

from hub.locales.keys import MessageKey

from llm.core.continuations import ResumeRecord

from llm.core.models import MessageStatus, RunStatus
from llm.core.configuration import UNSET
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..model import ChatMessage, HubSnapshot, SubmissionResult, SessionSummary
from ..locales import Language
from ..config.profile import UserProfile
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

    UNSPECIFIED = None
    INVALID_LOG_SINK = "log_sink must be callable"
    INVALID_RETRY_CHOOSER = "tool_retry_chooser must be callable"
    INVALID_RETRY_EVIDENCE = (
        "tool_retry_evidence maps Tool names to callable evidence providers"
    )
    RESERVED_AGENT = "Internal Hub Agent IDs are reserved"
    SESSION_REQUIRES_PROJECT = "session_id requires project_id"
    INVALID_ENGINE_FACTORIES = "engine_factories must be a dictionary or None"
    UNREGISTERED_ENGINE = "Initial engine must be registered in engine_factories"
    INVALID_ENGINE_FACTORY = (
        "Use nonempty engine names and callable factories; _hub_ is reserved"
    )
    INVALID_COMPONENT_FACTORIES = "component_factories must be a tuple, list or None"
    INVALID_COMPONENT_FACTORY = "Component factories must be callable"

    workspace: str | Path = field(default_factory=WorkspaceSelection.default_workspace)
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
    postprocess: bool | None = None
    user_profile: UserProfile = field(default_factory=UserProfile)
    language_packs: dict[str, dict[str, str]] = field(default_factory=dict)
    _workspace_anchor: str | Path | None = field(
        default=WorkspaceSelection.UNBOUND_ANCHOR, repr=False
    )
    tool_retry_chooser: Callable | None = None
    tool_retry_evidence: dict[str, Callable] = field(default_factory=dict)
    log_sink: Callable | None = None

    def __post_init__(self) -> None:
        if self.postprocess is not None and type(self.postprocess) is not bool:
            raise TypeError('postprocess must be a boolean or None')
        Language(self.language, self.language_packs)
        if self.file_root is not HubConfig.UNSPECIFIED:
            # Validate path input without changing its value or resolution time.
            value = (
                str(self.file_root)
                if isinstance(self.file_root, str)
                else self.file_root
            )
            Path(value)
            object.__setattr__(self, HubConfig.file_root.__name__, value)
        if self.log_sink is not HubConfig.UNSPECIFIED and not callable(self.log_sink):
            raise TypeError(HubConfig.INVALID_LOG_SINK)
        if self.tool_retry_chooser is not HubConfig.UNSPECIFIED and not callable(
            self.tool_retry_chooser
        ):
            raise TypeError(HubConfig.INVALID_RETRY_CHOOSER)
        if not isinstance(self.tool_retry_evidence, dict) or any(
            not isinstance(name, str) or not name or not callable(provider)
            for name, provider in self.tool_retry_evidence.items()
        ):
            raise TypeError(HubConfig.INVALID_RETRY_EVIDENCE)
        from llm.core.agents import AgentDefinition

        AgentDefinition.identifier(self.agent_id)
        if HubEngineIdentity.internal(self.agent_id):
            raise ValueError(HubConfig.RESERVED_AGENT)
        if self.agent_definition is not HubConfig.UNSPECIFIED:
            from copy import deepcopy

            AgentDefinition.validate(self.agent_id, self.agent_definition)
            object.__setattr__(
                self,
                HubConfig.agent_definition.__name__,
                deepcopy(self.agent_definition),
            )
        if self.session_id and not self.project_id:
            raise ValueError(HubConfig.SESSION_REQUIRES_PROJECT)
        if self.engine_factories is not HubConfig.UNSPECIFIED:
            if not isinstance(self.engine_factories, dict):
                raise TypeError(HubConfig.INVALID_ENGINE_FACTORIES)
            if (
                self.initial_engine
                not in (HubEngineIdentity.LOOP, HubEngineIdentity.GRAPH)
                and self.initial_engine not in self.engine_factories
            ):
                raise ValueError(HubConfig.UNREGISTERED_ENGINE)
            if any(
                not isinstance(name, str)
                or not name.strip()
                or HubEngineIdentity.internal(name)
                or not callable(factory)
                for name, factory in self.engine_factories.items()
            ):
                raise ValueError(HubConfig.INVALID_ENGINE_FACTORY)
        if self.component_factories is not HubConfig.UNSPECIFIED:
            if not isinstance(self.component_factories, (tuple, list)):
                raise TypeError(HubConfig.INVALID_COMPONENT_FACTORIES)
            if any(not callable(factory) for factory in self.component_factories):
                raise ValueError(HubConfig.INVALID_COMPONENT_FACTORY)

    @property
    def launch_workspace(self):
        return (
            self.workspace
            if self._workspace_anchor is WorkspaceSelection.UNBOUND_ANCHOR
            else self._workspace_anchor
        )

    @property
    def initial_engine(self):
        """Creation preset only; requests always select a saved Project Agent."""
        return (
            HubEngineIdentity.LOOP
            if self.agent_definition is HubConfig.UNSPECIFIED
            else AgentDefinition.engine(self.agent_definition)
        )


class HubRuntime:
    """All handles, subscriptions and locks belong to one backend event loop."""

    ABSENT = None
    NO_SELECTION = ""
    UNTITLED = ""
    NO_ARGUMENT = ""
    INITIAL_DROPPED = 0
    EVENT_BUFFER_SIZE = 128
    LAST_ENTRY = -1
    NEXT_TURN = 1
    LATEST_RUN_LIMIT = 1
    LATEST_RUN_POSITION = 0
    NEWEST_FIRST = True
    CAPTURE_ERRORS = True
    SUCCEEDED = True
    ACTIVITY_LIMIT = 300
    ACTIVITY_NEWEST_FIRST = False
    CHECK_RUNNING = True
    NO_QUEUED_IDS = 0
    UNIQUE_CANDIDATE_COUNT = 1
    INITIAL_PARTICIPANT_COUNT = 1
    SOLE_CANDIDATE_POSITION = 0
    PARTIAL_OUTPUT = True
    UNKNOWN_SESSION_OPERATION = "Unknown session operation: {mode}"
    FOREIGN_SESSION = "Session is not an active member of the selected Project"
    INTERNAL_RECIPIENT = "Internal Agent cannot receive conversation requests"
    FOREIGN_QUESTION = "Project changed; reload the question"

    def __init__(
        self, config: HubConfig, backend_factory: Callable = create_backend
    ) -> None:
        from ..config.preferences import configured_workspace

        config = configured_workspace(config)
        self.config = config
        self.t = Language(config.language, config.language_packs)
        self._factory = backend_factory
        self.backend = HubRuntime.ABSENT
        self.project = HubRuntime.ABSENT
        self.sessions = {}
        self.selected_id = HubRuntime.NO_SELECTION
        self.dirty = asyncio.Event()
        self.urgent_observation = False
        self.lock = asyncio.Lock()
        self._subscriptions = []
        self._dropped_observations = HubRuntime.INITIAL_DROPPED
        from .observability.snapshot import SnapshotReader

        self.snapshot_reader = SnapshotReader()
        from .observability.failures import FailureDiagnostics

        self.failure_diagnostics = FailureDiagnostics(self)
        from .history import TurnHistory

        self.history = TurnHistory(self)
        self._opened_sessions = set()
        self._rag_jobs = {}
        self.notifications = NotificationBuffer()
        self.builtin_tools = HubRuntime.ABSENT
        from .project.settings import SettingsService

        self.settings_service = SettingsService(self)
        preferences = self.settings_service.preferences.load()
        self.general = PreferenceSnapshot(preferences).general()
        from .project.naming import SessionNamer

        self.namer = SessionNamer(self)
        from .project.postprocessing import BackgroundRecords
        self.background_records = BackgroundRecords(self)
        from .interactions.automatic import AutomaticApprovals
        self.automatic_approvals = AutomaticApprovals(self)

    def session_is_open(self, identifier):
        return identifier in self._opened_sessions

    def forget_open_sessions(self, identifiers):
        self._opened_sessions.difference_update(identifiers)

    async def _engine_event(self, run, event) -> None:
        from .project.postprocessing_values import Postprocessing
        if run.engine == Postprocessing.ENGINE:
            return
        self.snapshot_reader.observe_engine(run, event)
        from llm.engines.events import EngineEvent, EngineEventType
        if EngineEvent.observed_type(event) in (
            EngineEventType.INTERACTION_CHANGED, EngineEventType.PAUSED,
        ):
            self.urgent_observation = True
        self.snapshot_reader.thinking.observe(run, event)
        from llm.engines.loop import LoopEngine
        from .execution.environment import underlying_engine
        if (not HubEngineIdentity.internal(run.engine)
                and isinstance(underlying_engine(self.backend.engines.get(run.engine)), LoopEngine)):
            self.snapshot_reader.drafts.observe(run, event)
        self.dirty.set()

    async def _run_event(self, event) -> None:
        from .project.postprocessing_values import Postprocessing
        if event.run.engine == Postprocessing.ENGINE:
            return
        if event.run.status != RunStatus.RUNNING:
            self.snapshot_reader.drafts.forget(event.run.id)
        self.background_records.observe(event.run)
        self.automatic_approvals.observe(event.run)
        self.snapshot_reader.observe_run(event.run)
        self.notifications.record_run(event.run)
        self.urgent_observation = True
        self.dirty.set()

    def _saved_selection(self, project_id=ABSENT):
        try:
            store = ViewStateStore(self.config.workspace)
            return (
                ViewStateStore.selected_from(store.load(project_id))
                if project_id
                else store.last_project()
            )
        except (OSError, ValueError, TypeError):
            return (
                HubRuntime.NO_SELECTION
            )  # Corrupt UI state must not prevent opening the backend.

    def _questions_changed(self):
        self.notifications.observe_questions(self.builtin_tools.questions.pending)
        self.urgent_observation = True
        self.dirty.set()

    async def start(self) -> None:
        from .project.bootstrap import select_or_create
        from .progress import OperationProgress

        OperationProgress.report(MessageKey.PROGRESS_BACKEND_PREPARE)
        self.backend = self._factory(self.config)
        from .tools.builtin import HubBuiltinTools

        if HubBuiltinTools.NAME in self.backend.project_manager.components.names():
            component = self.backend.project_manager.components.get(
                HubBuiltinTools.NAME
            )
            if isinstance(component, HubBuiltinTools):
                self.builtin_tools = component
                component.application.runtime = self
                component.questions.changed = self._questions_changed
        OperationProgress.report(MessageKey.PROGRESS_BACKEND_STORAGE)
        await self.backend.__aenter__()
        for channel, callback in (
            (EventChannel.ENGINE.value, self._engine_event),
            (EventChannel.RUN.value, self._run_event),
        ):
            self._subscriptions.append(
                self.backend.events.subscribe(
                    callback,
                    channel=channel,
                    delivery=EventDelivery.QUEUED.value,
                    buffer_size=HubRuntime.EVENT_BUFFER_SIZE,
                    overflow=EventOverflow.DROP_OLDEST.value,
                )
            )
        OperationProgress.report(MessageKey.PROGRESS_BACKEND_PROJECT)
        if self.config.project_id:
            self.project = await self.backend.projects.aload(self.config.project_id)
        elif self.general.restore_last_project:
            previous = self._saved_selection()
            if previous:
                self.project = next(
                    (
                        project
                        for project in await self.backend.projects.alist()
                        if project.id == previous
                    ),
                    HubRuntime.ABSENT,
                )
        if self.project is HubRuntime.ABSENT:
            self.project = await select_or_create(self.backend, self.config)
        await self.activate_project(self.project, session_id=self.config.session_id)

    async def settings(self, operation, *args):
        try:
            return await self.settings_service.execute(operation, *args)
        finally:
            from .progress import OperationProgress
            if operation in OperationProgress.SETTINGS_WRITES:
                self.snapshot_reader.invalidate_project()

    async def activate_project(self, project, *, session_id=ABSENT):
        from .progress import OperationProgress

        # Tool capabilities are resolved by the backend when execution needs them.
        # Merely opening history must not build an unused per-project toolkit.
        OperationProgress.report(MessageKey.PROGRESS_BACKEND_SESSIONS)
        sessions = []
        titles = {}
        available = await project.sessions.alist()
        for index, session in enumerate(available):
            OperationProgress.report(MessageKey.PROGRESS_BACKEND_SESSIONS, index, len(available))
            record = await session.aget_data()
            if not TitleSessionMetadata(record.metadata).internal:
                sessions.append(session)
                titles[session.id] = record.title
        candidates = {session.id: session for session in sessions}
        previous = (
            self._saved_selection(project.id)
            if self.general.restore_last_session
            else HubRuntime.NO_SELECTION
        )
        selected = session_id or (
            previous
            if previous in candidates
            else (
                sessions[HubRuntime.LAST_ENTRY].id
                if sessions
                else HubRuntime.NO_SELECTION
            )
        )
        if selected and selected not in candidates:
            raise ValueError(HubRuntime.FOREIGN_SESSION)
        # Session start owns stale-state recovery and durable queued-request recovery.
        # Opening history alone does not start every Session in the Project.
        if selected:
            OperationProgress.report(MessageKey.PROGRESS_BACKEND_RECOVER)
            await candidates[selected].run.start()
        self.project, self.sessions, self.selected_id = project, candidates, selected
        self.snapshot_reader.clear()
        for session in sessions:
            self.notifications.register_session(
                project.id, session.id, titles[session.id]
            )
        if selected:
            self._opened_sessions.add(selected)
        self.dirty.set()

    async def select(self, session_id: str) -> None:
        from .progress import OperationProgress
        if not session_id and not self.sessions:
            return
        session = self.sessions[session_id]
        if not self.session_is_open(session_id):
            OperationProgress.report(MessageKey.PROGRESS_BACKEND_RECOVER)
            await session.run.start()
            self._opened_sessions.add(session_id)
        self.selected_id = session_id
        self.dirty.set()

    async def new_session(
        self,
        mode: str = SessionMode.NEW.value,
        title: str = UNTITLED,
        source_id: str | None = ABSENT,
        through_message_id: str | None = ABSENT,
    ) -> str:
        if mode == SessionMode.CLONE:
            if not self.sessions:
                raise ValueError(self.t(MessageKey.SESSION_REQUIRED))
            source = self.sessions[source_id or self.selected_id]
            status = await source.run.astatus()
            if (
                status.active_runs
                or status.queued_count
                or source.id in self.namer.tasks
            ):
                raise ValueError(self.t(MessageKey.CLONE_BUSY))
            await source.run.shutdown()
            try:
                from .execution.turns import TurnIndex, edit_source
                from llm.services.history.turns import ConversationTurns

                records = await source.aconversation()
                revised = any(
                    edit_source(m) or ResumeRecord.from_metadata(m.metadata)
                    for m in records
                )
                index = TurnIndex(records)
                boundary = (
                    next(
                        i
                        for i, turn in enumerate(index.turns)
                        if turn.id == through_message_id
                    )
                    if revised and through_message_id
                    else HubRuntime.ABSENT
                )
                session = await source.aclone(
                    title=title or self.t(MessageKey.NEW_SESSION),
                    through_message_id=(
                        HubRuntime.ABSENT if revised else through_message_id
                    ),
                )
                if boundary is not HubRuntime.ABSENT:
                    # The Hub context builder clones logical turns. Trim the
                    # freshly created inactive clone through public deletion.
                    groups = await session.aturns()
                    for group in reversed(groups[boundary + HubRuntime.NEXT_TURN :]):
                        await session.adelete_turn(ConversationTurns.request(group).id)
            finally:
                await source.run.start()
        elif mode == SessionMode.NEW:
            participants = sorted(
                identifier
                for identifier in await self.project.agents.alist()
                if not HubEngineIdentity.internal(identifier)
            )
            participants = ([self.config.agent_id] if self.config.agent_id in participants
                            else participants[:self.INITIAL_PARTICIPANT_COUNT])
            session = await self.project.sessions.acreate(
                title or self.t(MessageKey.NEW_SESSION), agent_ids=participants
            )
        else:
            raise ValueError(HubRuntime.UNKNOWN_SESSION_OPERATION.format(mode=mode))
        data = await session.aget_data()
        metadata = TitleSessionMetadata(data.metadata).initialize(
            not bool(title) and self.config.auto_title
        )
        if mode == SessionMode.CLONE:
            from llm.services.query import Query

            source_runs = await source.run.alist(
                query=Query(
                    descending=HubRuntime.NEWEST_FIRST,
                    limit=HubRuntime.LATEST_RUN_LIMIT,
                )
            )
            if source_runs:
                TitleSessionMetadata(metadata).set_agent(
                    (
                        await source_runs[HubRuntime.LATEST_RUN_POSITION].aget_data()
                    ).agent_id
                )
        await session.asave(metadata=metadata)
        self.sessions[session.id] = session
        self.notifications.register_session(
            self.project.id, session.id, (await session.aget_data()).title
        )
        self.selected_id = session.id
        await session.run.start()
        self._opened_sessions.add(session.id)
        self.dirty.set()
        if mode == SessionMode.CLONE:
            self.namer.schedule(session, TitlePresentation.CLONE_TRIGGER)
        return session.id

    async def turns(self, session_id):
        return await self.history.rows(session_id)

    async def retry_turn(self, session_id, request_id, expected_run_id):
        return await self.history.retry(session_id, request_id, expected_run_id)

    async def edit_turn(self, session_id, request_id, text, expected_request_id):
        return await self.history.edit(
            session_id, request_id, text, expected_request_id
        )

    async def delete_turns_plan(self, session_id, identifiers):
        return await self.history.deletion_plan(session_id, identifiers)

    async def delete_turns(
        self, session_id, identifiers, expected_revision, *, progress=ABSENT
    ):
        return await self.history.delete(
            session_id, identifiers, expected_revision, progress=progress
        )

    async def delete_turn_plan(self, session_id, request_id):
        return (await self.sessions[session_id].adelete_turn_plan(request_id)).to_dict()

    async def delete_turn(
        self, session_id, request_id, abandon_runs=(), expected_revision=ABSENT
    ):
        session = self.sessions[session_id]
        status = await session.run.astatus()
        if status.active_runs or status.queued_count or session_id in self.namer.tasks:
            raise ValueError(self.t(MessageKey.HISTORY_BUSY))
        await session.run.shutdown()
        try:
            try:
                await session.adelete_turn(
                    request_id,
                    abandon_runs=abandon_runs,
                    expected_revision=expected_revision,
                )
            except ValueError as error:
                if str(error) == SessionManager.STALE_PLAN:
                    raise ValueError(
                        self.t(MessageKey.HISTORY_DELETE_CHANGED)
                    ) from error
                if str(error) == SessionManager.RESUMABLE_TURN:
                    raise ValueError(
                        self.t(MessageKey.HISTORY_CHECKPOINT_PROTECTED)
                    ) from error
                raise
            self.snapshot_reader.clear()
        finally:
            await session.run.start()
        self.dirty.set()
        return HubRuntime.SUCCEEDED

    async def cancel_request(self, session_id, request_id):
        session = self.sessions[session_id]
        request = await session.run.arequest(request_id)
        cancelled = await request.cancel()
        self.snapshot_reader.invalidate(session_id)
        self.dirty.set()
        if not cancelled:
            raise ValueError(self.t(MessageKey.HISTORY_CANCEL_UNAVAILABLE))
        return HubRuntime.SUCCEEDED

    async def project_activity(self):
        """Keep public activity records and add bounded provider diagnostics."""
        from .observability.provider_logs import (
            read_provider_logs,
            associate_provider_logs,
        )

        project = self.project
        rows = [
            event.to_dict()
            for event in await project.aactivity(
                limit=HubRuntime.ACTIVITY_LIMIT,
                newest_first=HubRuntime.ACTIVITY_NEWEST_FIRST,
            )
        ]
        workspace = LargeLanguageModel.workspace_or(self.backend, self.config.workspace)
        provider = await asyncio.to_thread(read_provider_logs, workspace, project.id)
        return associate_provider_logs(rows, provider)

    async def diagnose_failures(self, project_id, session_id, run_ids):
        return await self.failure_diagnostics.read(project_id, session_id, run_ids)

    async def execution_catalog(self, session_id: str) -> dict:
        project = self.sessions[session_id].project if session_id else self.project
        definitions = await project.agents.alist()
        return RuntimeAgentCatalog.from_definitions(definitions).to_dict()

    async def rename_session(self, session_id: str, title: str) -> str:
        if not title.strip():
            raise ValueError(self.t(MessageKey.SESSION_NAME_REQUIRED))
        session = self.sessions[session_id]
        # Stop an in-flight automatic title before saving the explicit title.
        task = self.namer.tasks.get(session_id)
        if task is not HubRuntime.ABSENT:
            task.cancel()
            await asyncio.gather(task, return_exceptions=HubRuntime.CAPTURE_ERRORS)
        data = await session.aget_data()
        await session.asave(
            title=title.strip(),
            metadata=TitleSessionMetadata(data.metadata).completed(),
        )
        self.namer.errors.pop(session_id, HubRuntime.ABSENT)
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
        self.namer.errors.pop(session_id, HubRuntime.ABSENT)
        if self.selected_id == session_id:
            if self.sessions:
                await self.select(next(iter(self.sessions)))
            else:
                self.selected_id = HubRuntime.NO_SELECTION
        self.dirty.set()
        return self.selected_id

    async def manage_tools(
        self, project_id, action, argument=NO_ARGUMENT, expected_version=ABSENT
    ):
        from .tools.management import ToolManagement

        project = await self.backend.projects.aload(project_id)
        tools = await ToolManagement.for_project(
            project, root=self.config.file_root, builtin=self.builtin_tools
        )
        return await tools.execute(action, argument, expected_version)

    async def manage_records(
        self,
        project_id,
        name,
        action,
        argument=NO_ARGUMENT,
        expected_version=ABSENT,
        *,
        progress=ABSENT
    ):
        from .project.record_management import RecordManagement
        from .progress import OperationProgress

        OperationProgress.report(OperationProgress.management(action))
        project = await self.backend.projects.aload(project_id)
        manager = await RecordManagement.for_project(
            project, name, root=self.config.file_root, rag_jobs=self._rag_jobs
        )
        if name == RAGIdentity.CAPABILITY:
            return await manager.execute(
                action, argument, expected_version, progress=progress
            )
        try:
            return await manager.execute(action, argument, expected_version)
        finally:
            self.snapshot_reader.invalidate_project()

    async def submit(
        self,
        session_id: str,
        text: str,
        agent_id: str | Sequence[str] | None = ABSENT,
        request_context=UNSET,
    ) -> str:
        selected = self.config.agent_id if agent_id is HubRuntime.ABSENT else agent_id
        recipients = [selected] if isinstance(selected, str) else list(selected)
        return await self._submit_recipients(session_id, text, recipients, request_context)

    async def invite_agent(self, project_id, session_id, agent_id):
        from .project.participants import invite
        return await invite(self, project_id, session_id, agent_id)

    async def change_participants(self, project_id, session_id, action, identifier, value):
        from .project.participants import change
        return await change(self, project_id, session_id, action, identifier, value)

    async def _submit_recipients(self, session_id, text, recipients, request_context):
        """Application-selected recipients share one durable Backend admission."""
        if not text.strip():
            raise ValueError(self.t(MessageKey.EMPTY_MESSAGE))
        if session_id not in self.sessions:
            raise ValueError(self.t(MessageKey.SESSION_REQUIRED))
        if not recipients:
            raise ValueError(self.t(MessageKey.PARTICIPANTS_REQUIRED))
        session = self.sessions[session_id]
        from .execution.engine_selection import agent_model

        engines = []
        for selected in recipients:
            if HubEngineIdentity.internal(selected):
                raise ValueError(HubRuntime.INTERNAL_RECIPIENT)
            definition = await session.project.agents.aload(selected)
            if EngineSelection.model_required(
                agent_model(self.backend.engines, definition)
            ):
                raise ValueError(self.t(MessageKey.MODEL_REQUIRED))
            engines.append(self.backend.engines.get(AgentDefinition.engine(definition)))
        from .execution.request_profile import RequestProfile
        request = await session.run.submit(
            text, recipients=recipients,
            request_context=RequestProfile.for_engines(text, request_context, engines)
        )
        self.snapshot_reader.invalidate(session_id)
        self.dirty.set()
        return request.id

    async def submit_input(
        self,
        session_id: str,
        text: str,
        agent_id: str | Sequence[str],
        request_context=UNSET,
        check_running: bool = CHECK_RUNNING,
    ) -> SubmissionResult:
        """Check and admit in one UI command; acknowledge only persisted input."""
        from datetime import datetime, timezone

        timestamp = datetime.now(timezone.utc).isoformat()
        if check_running:
            state = await self.submission_state(session_id, agent_id)
            if SubmissionResult.state_run_id(state):
                return SubmissionResult(run_id=SubmissionResult.state_run_id(state))
        identifier = await self.submit(session_id, text, agent_id, request_context)
        self.snapshot_reader.record_submission(identifier, timestamp)
        # No extra reads after admission: a later read failure must not make a
        # durable request look rejected. The next snapshot supplies exact state.
        return SubmissionResult(
            message=ChatMessage(
                MessageRole.USER.value,
                text,
                timestamp,
                status=MessageStatus.QUEUED.value,
                id=identifier,
                author=self.config.user_profile.display_name,
            )
        )

    async def submission_state(
        self, session_id: str, agent_id: str | Sequence[str] | None = ABSENT
    ) -> dict:
        status = await self.sessions[session_id].run.astatus(
            queued_limit=HubRuntime.NO_QUEUED_IDS
        )
        selected = self.config.agent_id if agent_id is HubRuntime.ABSENT else agent_id
        recipients = (selected,) if isinstance(selected, str) else selected
        return SubmissionResult.state_record(
            next(
                (run.run_id for run in status.active_runs if run.agent_id in recipients),
                HubRuntime.ABSENT,
            )
        )

    async def instruction_targets(
        self, session_id: str, run_id: str | None = ABSENT
    ) -> tuple[str, tuple[tuple[str, str], ...]]:
        session = self.sessions[session_id]
        status = await session.run.astatus()
        selected = next(
            (
                run
                for run in status.active_runs
                if (
                    run.run_id == run_id
                    if run_id is not HubRuntime.ABSENT
                    else run.agent_id == self.config.agent_id
                )
            ),
            HubRuntime.ABSENT,
        )
        if selected is HubRuntime.ABSENT:
            raise ValueError(self.t(MessageKey.NO_ACTIVE_RUN))
        run = await session.run.aload(selected.run_id)
        targets = tuple(
            (target.id, target.node_path or target.node_id or target.engine)
            for target in await run.ainstruction_targets()
            if target.accepting and str(target.mode) == SteeringMode.CONSUME.value
        )
        if not targets:
            raise ValueError(self.t(MessageKey.NO_TARGETS))
        return selected.run_id, targets

    async def running_recipients(
        self, session_id: str, recipients: Sequence[str]
    ) -> tuple[tuple[str, str], ...]:
        """Observe live recipient Runs before the user chooses a steering target."""
        status = await self.sessions[session_id].run.astatus(queued_limit=self.NO_QUEUED_IDS)
        return tuple((run.run_id, run.agent_id) for run in status.active_runs
                     if run.agent_id in recipients)

    async def steer(
        self, session_id: str, run_id: str, text: str, targets: list[str]
    ) -> str:
        result = await self.sessions[session_id].run.steer(
            run_id, text, targets=targets
        )
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
            return HubRuntime.ABSENT
        turn = index.turns[HubRuntime.LAST_ENTRY]
        session = self.sessions[session_id]
        record = await session.aget_data()
        candidates = [
            run
            for _, run in (await self.history.attempts(session, turn)).values()
            if run is not HubRuntime.ABSENT
            and str(display_run(record, run).status) == RunStatus.INTERRUPTED
        ]
        if len(candidates) != HubRuntime.UNIQUE_CANDIDATE_COUNT:
            return (
                HubRuntime.ABSENT
            )  # History offers explicit Run selection for ambiguous retries.
        return await self.history.retry(
            session_id, turn.id, candidates[HubRuntime.SOLE_CANDIDATE_POSITION].id
        )

    async def approval_details(self, request_data):
        from .interactions.approval import review_text

        return await asyncio.to_thread(review_text, request_data)

    async def answer_question(
        self,
        project_id,
        session_id,
        run_id,
        request_data,
        option_id,
        value,
        *,
        acknowledged=ABSENT
    ):
        from llm.core.interactions import InteractionOption, InteractionRequest
        from .interactions.questions import pending_requests

        if project_id != self.project.id:
            raise ValueError(HubRuntime.FOREIGN_QUESTION)
        request = InteractionRequest.from_dict(request_data)
        if self.builtin_tools and request.id in self.builtin_tools.questions.pending:
            self.builtin_tools.questions.answer(
                project_id, session_id, run_id, request, option_id, value
            )
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
            denied = (
                next(
                    option for option in request.options if option.id == option_id
                ).effect
                == InteractionOption.EFFECT_DENY
            )
            if not await pending_requests(run):
                if not denied:
                    agent_id = (await run.aget_data()).agent_id
                    # The backend validates persisted user decisions, including
                    # uncertain-effect approval. Never invent retry_nodes here.
                    await session.run.resume(
                        run_id,
                        agent_id=agent_id,
                        partial_output=HubRuntime.PARTIAL_OUTPUT,
                    )
                from .execution.stopping import finish_review

                await finish_review(session, run_id)
        self.dirty.set()
        return HubRuntime.SUCCEEDED

    def _reconcile_snapshot(self, project_id, observations):
        for item in observations:
            identifier, record, runtime, run = (
                item.session_id,
                item.record,
                item.runtime,
                item.run,
            )
            self.notifications.observe_session(
                project_id, identifier, record.title, run, item.questions
            )
            if (
                TitleSessionMetadata(record.metadata).automatic
                and not runtime.active_runs
            ):
                if (
                    run is not HubRuntime.ABSENT
                    and str(run.status) == RunStatus.COMPLETED
                ):
                    self.namer.schedule(self.sessions[identifier], run.id)
                elif (
                    run is HubRuntime.ABSENT
                    and TitleSessionMetadata(record.metadata).engine
                ):
                    self.namer.schedule(
                        self.sessions[identifier], TitlePresentation.CLONE_TRIGGER
                    )

    async def snapshot(self) -> HubSnapshot:
        from .progress import OperationProgress
        OperationProgress.report(MessageKey.PROGRESS_BACKEND_HISTORY)
        dropped = sum(
            SubscriptionStats.dropped_count(subscription.stats)
            for subscription in self._subscriptions
        )
        if dropped != self._dropped_observations:
            # A missing text delta makes a cached thinking phase unreliable.
            # Rebuild the display from durable messages/completions below.
            self.snapshot_reader.invalidate_dropped_events()
            self._dropped_observations = dropped
        from dataclasses import replace

        snapshot, observations = await self.snapshot_reader.read(
            self.project,
            self.sessions,
            self.selected_id,
            config=self.config,
            engines=self.backend.engines,
            title_errors=self.namer.errors,
        )
        self.namer.observe_configuration(snapshot.project_id, self.snapshot_reader.agent_definitions)
        self._reconcile_snapshot(snapshot.project_id, observations)
        questions = (
            self.builtin_tools.questions.views(self.project.id, self.selected_id)
            if self.builtin_tools
            else ()
        )
        questions = questions + snapshot.questions
        from .execution.retries import RetryMonitor

        monitor = BackendServices.observed_sink(self.backend.services)
        if snapshot.run and snapshot.run.active and isinstance(monitor, RetryMonitor):
            snapshot = replace(
                snapshot, run=replace(snapshot.run, retry=monitor.view(snapshot.run.id))
            )
        from .interactions.questions import waiting_status

        waiting = (
            {
                item.session_id
                for item in self.builtin_tools.questions.pending.values()
                if item.project_id == snapshot.project_id
            }
            if self.builtin_tools
            else set()
        )
        sessions = tuple(
            (
                replace(item, status=SessionSummary.WAITING_INPUT)
                if item.id in waiting
                else item
            )
            for item in snapshot.sessions
        )
        return replace(
            snapshot,
            notifications=self.notifications.items(),
            questions=questions,
            sessions=sessions,
            status=waiting_status(questions) if questions else snapshot.status,
        )

    async def close(self) -> None:
        await self.automatic_approvals.close()
        await self.background_records.close()
        if self.builtin_tools:
            self.builtin_tools.questions.close()
        await self.namer.close()
        for subscription in self._subscriptions:
            await subscription.aclose()
        self._subscriptions.clear()
        if self.backend is not HubRuntime.ABSENT:
            await self.backend.shutdown()
        if self.builtin_tools:
            await self.builtin_tools.close()
