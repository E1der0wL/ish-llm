"""Session-bound Hub operations through LargeLanguageModel's public facade."""

import asyncio
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..model import ChatMessage, HubSnapshot, SessionNotification, SubmissionResult
from ..locales import Language
from ..config.profile import UserProfile
from .engine_selection import requires_model, selected_engines
from ..config.general import GeneralSettings
from ..config.view_state import ViewStateStore


@dataclass(frozen=True, slots=True)
class HubConfig:
    workspace: str | Path = field(default_factory=lambda: Path.home() / ".ish/hub-workspace")
    engine: str = "loop"
    model: str | None = None
    api_base: str | None = None
    project_id: str | None = None
    session_id: str | None = None
    language: str = "ko"
    engine_factories: dict[str, Callable] | None = None
    component_factories: tuple[Callable, ...] | None = None
    tool_classifier: Callable | None = None
    project_config: dict = field(default_factory=dict)
    file_root: str | Path | None = None
    auto_title: bool = True
    user_profile: UserProfile = field(default_factory=UserProfile)
    language_packs: dict[str, dict[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Language(self.language, self.language_packs)
        if not self.engine.strip():
            raise ValueError("An explicit engine is required")
        if self.session_id and not self.project_id:
            raise ValueError("session_id requires project_id")
        if self.engine_factories is not None:
            if self.engine not in ("loop", "graph") and self.engine not in self.engine_factories:
                raise ValueError("Initial engine must be registered in engine_factories")
            if any(not name.strip() or name.startswith("_hub_") or not callable(factory)
                   for name, factory in self.engine_factories.items()):
                raise ValueError("Use nonempty engine names and callable factories; _hub_ is reserved")


def create_backend(config: HubConfig):
    # Heavy imports and construction happen on the backend thread, not in .ishrc.
    from llm.llm import LargeLanguageModel, BackendServices
    from llm.services.runtime.tools import ToolRuntime
    from llm.engines.loop import LoopEngine
    from llm.engines.graph import GraphEngine
    from llm.engines.graph.agent import AgentNode
    from llm.engines.graph.tool import ToolNode
    from llm.components.tools import ToolComponent
    from llm.components.agents import AgentComponent
    from llm.components.workflows import WorkflowComponent
    from llm.components.memory import MemoryComponent
    from llm.components.prompts import PromptComponent
    from llm.components.skills import SkillComponent
    from llm.components.mcp import MCPComponent
    from llm.components.rag import RAGComponent
    from .naming import TitleEngine, TITLE_ENGINE
    from .builtin_tools import HubBuiltinTools
    from .context import HubContextBuilder
    factories = {"loop": LoopEngine, **(config.engine_factories or {})}
    if config.engine not in factories and config.engine != "graph":
        raise ValueError("Register the selected engine in HubConfig.engine_factories")
    engines = {name: factory() for name, factory in factories.items()}
    engines.setdefault("graph", GraphEngine(handlers={
        "agent": AgentNode(engines={"loop": engines["loop"]}), "tool": ToolNode()}))
    if config.auto_title:
        engines[TITLE_ENGINE] = TitleEngine()
    components = {component.name: component for component in
                  (ToolComponent(), SkillComponent(), MCPComponent(), RAGComponent(),
                   AgentComponent(engines=engines), WorkflowComponent(), MemoryComponent(), PromptComponent(),
                   HubBuiltinTools(config.file_root))}
    for factory in config.component_factories or ():
        component = factory()
        components[component.name] = component
    return LargeLanguageModel(config.workspace, components=list(components.values()), engines=engines,
                              services=BackendServices(context_builder=HubContextBuilder(),
                                  tool_runtime=ToolRuntime(classify=config.tool_classifier)))


class HubRuntime:
    """All handles, subscriptions and locks belong to one backend event loop."""

    def __init__(self, config: HubConfig, backend_factory: Callable = create_backend) -> None:
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
        from .snapshot import SnapshotReader
        self.snapshot_reader = SnapshotReader()
        self._opened_sessions = set()
        self._notification_sessions = {}
        self._notifications = deque(maxlen=64)
        self._notification_keys = deque(maxlen=128)
        self._notification_sequence = 0
        self._observed_runs = {}
        self.builtin_tools = None
        from .settings_service import SettingsService
        self.settings_service = SettingsService(self)
        preferences = self.settings_service.preferences.load()
        self.general = GeneralSettings(**preferences.get("general", {}))
        from .naming import SessionNamer
        self.namer = SessionNamer(self)

    async def _engine_event(self, run, event) -> None:
        self.snapshot_reader.thinking.observe(run, event)
        self.dirty.set()

    async def _run_event(self, event) -> None:
        self.snapshot_reader.invalidate(event.run.session_id)
        session = self._notification_sessions.get(event.run.session_id)
        if session is not None and not event.run.engine.startswith("_hub_"):
            self._record_notification(session, event.run)
        self.dirty.set()

    def _record_notification(self, session, run) -> None:
        project_id, title = session
        state = (run.id, str(run.status))
        self._observed_runs[run.session_id] = state
        if state in self._notification_keys:
            return
        self._notification_keys.append(state)
        self._notification_sequence += 1
        self._notifications.append(SessionNotification(self._notification_sequence,
            project_id, run.session_id, title, str(run.status)))

    def _saved_selection(self, project_id=None):
        try:
            store = ViewStateStore(self.config.workspace)
            return store.load(project_id)[0] if project_id else store.last_project()
        except (OSError, ValueError, TypeError):
            return ""  # Corrupt UI state must not prevent opening the backend.

    async def start(self) -> None:
        from .bootstrap import select_or_create
        self.backend = self._factory(self.config)
        from .builtin_tools import HubBuiltinTools
        if "builtin_tools" in self.backend.project_manager.components.names():
            component = self.backend.project_manager.components.get("builtin_tools")
            if isinstance(component, HubBuiltinTools):
                self.builtin_tools = component
                component.questions.changed = self.dirty.set
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
        if self.builtin_tools:
            await self.builtin_tools.ensure_selected(project)
        data = await project.aget_data()
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
            self._notification_sessions[session.id] = (project.id, (await session.aget_data()).title)
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
                raise ValueError(self.t("session_required"))
            source = self.sessions[source_id or self.selected_id]
            status = await source.run.astatus()
            if status.active_run_id or status.queued_count or source.id in self.namer.tasks:
                raise ValueError(self.t("clone_busy"))
            await source.run.shutdown()
            try:
                session = await source.aclone(title=title or self.t("new_session"), through_message_id=through_message_id)
            finally:
                await source.run.start()
        elif mode == "new":
            session = await self.project.sessions.acreate(title or self.t("new_session"))
        else:
            raise ValueError(f"Unknown session operation: {mode}")
        data = await session.aget_data()
        metadata = {**data.metadata, "hub_auto_title": not bool(title) and self.config.auto_title}
        metadata.pop("hub_title_attempt", None)
        if mode == "clone":
            from llm.services.query import Query
            source_runs = await source.run.alist(query=Query(descending=True, limit=1))
            if source_runs:
                metadata["hub_title_engine"] = (await source_runs[0].aget_data()).engine
        await session.asave(metadata=metadata)
        self.sessions[session.id] = session
        self._notification_sessions[session.id] = (self.project.id, (await session.aget_data()).title)
        self.selected_id = session.id
        await session.run.start()
        self._opened_sessions.add(session.id)
        self.dirty.set()
        if mode == "clone":
            self.namer.schedule(session, "clone")
        return session.id

    async def turns(self, session_id):
        if not session_id:
            return []
        session = self.sessions[session_id]
        rows = []
        for group in await session.aturns():
            request = group[0]
            assistants = [m for m in group if m.role == "assistant"]
            run = await (await session.run.aload(request.run_id)).aget_data() if request.run_id else None
            elapsed = (await self.snapshot_reader.run_display(session, run.id))[1] if run else None
            rows.append({"id": request.id, "text": request.content, "time": request.created_at,
                         "status": str(run.status if run else assistants[-1].status if assistants else request.status),
                         "engine": run.engine if run else "", "elapsed": elapsed,
                         "response": "\n".join(m.content for m in assistants),
                         "run_id": request.run_id or "", "error": str(run.error or "") if run else ""})
        return rows

    async def delete_turn_plan(self, session_id, request_id):
        return (await self.sessions[session_id].adelete_turn_plan(request_id)).to_dict()

    async def delete_turn(self, session_id, request_id, abandon_runs=(), expected_revision=None):
        session = self.sessions[session_id]
        status = await session.run.astatus()
        if status.active_run_id or status.queued_count or session_id in self.namer.tasks:
            raise ValueError(self.t("history_busy"))
        await session.run.shutdown()
        try:
            try:
                await session.adelete_turn(request_id, abandon_runs=abandon_runs, expected_revision=expected_revision)
            except ValueError as error:
                if str(error) == "Turn deletion plan changed; reload and confirm again":
                    raise ValueError(self.t("history_delete_changed")) from error
                if str(error) == "Conversation turn is required by an unfinished resumable Run":
                    raise ValueError(self.t("history_checkpoint_protected")) from error
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
            raise ValueError(self.t("history_cancel_unavailable"))
        return True

    async def project_activity(self):
        """실행 이력은 기존 파생 인덱스로 조회한다. 운영 로그/도메인 파일을 순회하지 않는다."""
        return [event.to_dict() for event in await self.project.aactivity(limit=300, newest_first=False)]

    async def execution_catalog(self, session_id: str) -> dict:
        from llm.engines.graph import GraphEngine
        project = self.sessions[session_id].project if session_id else self.project
        record = await project.aget_data()
        engines = {name: {"kind": "graph" if isinstance(self.backend.engines.get(name), GraphEngine)
                          else "plain"}
                   for name in selected_engines(record.config, self.backend.engines.names())}
        if not engines:
            raise ValueError(self.t("settings_engine_required"))
        workflows = {}
        if any(item["kind"] == "graph" for item in engines.values()):
            if "workflows" in (await project.aget_data()).components:
                workflows = await (await project.components.aget("workflows")).alist()
        return {"engines": engines, "workflows": workflows}

    async def rename_session(self, session_id: str, title: str) -> str:
        if not title.strip():
            raise ValueError(self.t("session_name_required"))
        session = self.sessions[session_id]
        # Stop an in-flight automatic title before saving the explicit title.
        task = self.namer.tasks.get(session_id)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        data = await session.aget_data()
        await session.asave(title=title.strip(), metadata={**data.metadata, "hub_auto_title": False})
        self.namer.errors.pop(session_id, None)
        self._notification_sessions[session_id] = (self.project.id, title.strip())
        self.snapshot_reader.invalidate(session_id)
        self.dirty.set()
        return session_id

    async def delete_session(self, session_id: str) -> str:
        session = self.sessions[session_id]
        status = await session.run.astatus()
        if status.active_run_id or status.queued_count or session_id in self.namer.tasks:
            raise ValueError(self.t("session_busy"))
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
        self._notification_sessions.pop(session_id, None)
        self._observed_runs.pop(session_id, None)
        self.namer.errors.pop(session_id, None)
        if self.selected_id == session_id:
            if self.sessions:
                await self.select(next(iter(self.sessions)))
            else:
                self.selected_id = ""
        self.dirty.set()
        return self.selected_id

    async def component_command(self, project_id: str, command: str, argument: str):
        from .component_commands import component_commands, execute_data, parse_arguments
        project = await self.backend.projects.aload(project_id)
        commands = component_commands(tuple((await project.aget_data()).components))
        if command not in commands:
            raise ValueError(self.t("component_unavailable", name=command))
        name = commands[command]
        action, identifier, payload = parse_arguments(argument, self.t)
        if action == "settings":
            return {"catalog": await self.settings_service.catalog(),
                    "project": await self.settings_service.load_project(project_id)}
        if action == "help":
            return self.t("component_help", command=command)
        data = await project.components.aget(name)
        result = await execute_data(data, action, identifier, payload)
        self.dirty.set()
        return result

    async def manage_tools(self, project_id, action, argument="", expected_version=None):
        from .tool_management import manage_tools
        project = await self.backend.projects.aload(project_id)
        return await manage_tools(project, action, argument, expected_version, root=self.config.file_root,
                                  builtin=self.builtin_tools)

    async def submit(self, session_id: str, text: str, engine: str | None = None,
                     engine_options: dict | None = None) -> str:
        if not text.strip():
            raise ValueError(self.t("empty_message"))
        if session_id not in self.sessions:
            raise ValueError(self.t("session_required"))
        session = self.sessions[session_id]
        selected = engine if engine is not None else self.config.engine
        project = await session.project.aget_data()
        if requires_model(self.backend.engines, project.config, selected, (await session.aget_data()).config):
            raise ValueError(self.t("model_required"))
        if selected not in selected_engines(project.config, self.backend.engines.names()):
            raise ValueError(self.t("settings_engine_unavailable", name=selected))
        if selected.startswith("_hub_"):
            raise ValueError("Internal engine cannot receive conversation requests")
        request = await session.run.submit(text, engine=selected, engine_options=engine_options or {})
        self.snapshot_reader.invalidate(session_id)
        self.dirty.set()
        return request.id

    async def submit_input(self, session_id: str, text: str, engine: str,
                           engine_options: dict, check_running: bool = True) -> SubmissionResult:
        """Check and admit in one UI command; acknowledge only persisted input."""
        from datetime import datetime, timezone
        if check_running:
            state = await self.submission_state(session_id)
            if state["run_id"]:
                return SubmissionResult(run_id=state["run_id"])
        timestamp = datetime.now(timezone.utc).isoformat()
        identifier = await self.submit(session_id, text, engine, engine_options)
        # No extra reads after admission: a later read failure must not make a
        # durable request look rejected. The next snapshot supplies exact state.
        return SubmissionResult(message=ChatMessage("user", text, timestamp,
            status="queued", id=identifier, author=self.config.user_profile.display_name))

    async def submission_state(self, session_id: str) -> dict:
        status = await self.sessions[session_id].run.astatus(queued_limit=0)
        return {"run_id": status.active_run_id}

    async def instruction_targets(self, session_id: str) -> tuple[str, tuple[tuple[str, str], ...]]:
        session = self.sessions[session_id]
        status = await session.run.astatus()
        if not status.active_run_id:
            raise ValueError(self.t("no_active_run"))
        run = await session.run.aload(status.active_run_id)
        targets = tuple((target.id, target.node_path or target.node_id or target.engine)
                        for target in await run.ainstruction_targets()
                        if target.accepting and str(target.mode) == "consume")
        if not targets:
            raise ValueError(self.t("no_targets"))
        return status.active_run_id, targets

    async def steer(self, session_id: str, run_id: str, text: str, targets: list[str]) -> str:
        result = await self.sessions[session_id].run.steer(run_id, text, targets=targets)
        self.dirty.set()
        return result.id

    async def interrupt(self, session_id: str) -> bool:
        result = await self.sessions[session_id].run.interrupt()
        self.dirty.set()
        return result

    async def answer_question(self, project_id, session_id, run_id, request_data, option_id, value):
        from llm.core.interactions import InteractionRequest
        if project_id != self.project.id:
            raise ValueError("Project changed; reload the question")
        request = InteractionRequest.from_dict(request_data)
        if self.builtin_tools and request.id in self.builtin_tools.questions.pending:
            self.builtin_tools.questions.answer(project_id, session_id, run_id, request, option_id, value)
        else:
            session = self.sessions[session_id]
            run = await session.run.aload(run_id)
            await run.arespond(request.respond(option_id, value=value))
            if not await run.ainteractions(pending_only=True):
                engine = (await run.aget_data()).engine
                plan = await session.run.resume_plan(run_id, engine=engine)
                if not plan.can_resume or plan.retry_nodes:
                    raise ValueError("Response saved; this Run requires explicit recovery before resuming")
                await session.run.resume(run_id, engine=engine)
        self.dirty.set()
        return True

    def _reconcile_snapshot(self, project_id, observations):
        for item in observations:
            identifier, record, runtime, run = item.session_id, item.record, item.runtime, item.run
            self._notification_sessions[identifier] = (project_id, record.title)
            if run is not None:
                state = (run.id, str(run.status))
                if identifier in self._observed_runs and self._observed_runs[identifier] != state:
                    self._record_notification((project_id, record.title), run)
                else:
                    self._observed_runs[identifier] = state
            elif identifier not in self._observed_runs:
                self._observed_runs[identifier] = None
            if record.metadata.get("hub_auto_title") and not runtime.active_run_id:
                if run is not None and str(run.status) == "completed":
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
        return replace(snapshot, notifications=tuple(self._notifications), questions=questions + snapshot.questions)

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
