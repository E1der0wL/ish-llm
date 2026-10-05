"""Session-bound Hub operations through LargeLanguageModel's public facade."""

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..ui.chat.conversation import ChatMessage
from ..locales import Language
from ..config.profile import UserProfile
from .engine_selection import selected_engines
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


@dataclass(frozen=True, slots=True)
class SessionSummary:
    id: str
    title: str
    status: str


@dataclass(frozen=True, slots=True)
class SessionNotification:
    sequence: int
    project_id: str
    session_id: str
    title: str
    status: str


@dataclass(frozen=True, slots=True)
class HubSnapshot:
    project_id: str
    project_title: str
    model: str
    storage: str
    sessions: tuple[SessionSummary, ...]
    selected_id: str
    messages: tuple[ChatMessage, ...]
    detail: str
    notice: str
    engines: tuple[str, ...] = ()
    activity: str = ""
    file_root: str = ""
    notifications: tuple[SessionNotification, ...] = ()
    components: tuple[str, ...] = ()


def create_backend(config: HubConfig):
    # Heavy imports and construction happen on the backend thread, not in .ishrc.
    from llm.llm import LargeLanguageModel
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
                   AgentComponent(), WorkflowComponent(), MemoryComponent(), PromptComponent())}
    for factory in config.component_factories or ():
        component = factory()
        components[component.name] = component
    return LargeLanguageModel(config.workspace, components=list(components.values()), engines=engines)


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
        self._run_display_cache: dict[str, tuple[str, float | None]] = {}
        self._opened_sessions = set()
        self._notification_sessions = {}
        self._notifications = deque(maxlen=64)
        self._notification_keys = deque(maxlen=128)
        self._notification_sequence = 0
        self._observed_runs = {}
        from .settings_service import SettingsService
        self.settings_service = SettingsService(self)
        self.general = GeneralSettings(**self.settings_service.preferences.load().get("general", {}))
        from .naming import SessionNamer
        self.namer = SessionNamer(self)

    async def _engine_event(self, run, event) -> None:
        self.dirty.set()

    async def _run_event(self, event) -> None:
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

    async def _run_display(self, session, run_id: str) -> tuple[str, float | None]:
        cached = self._run_display_cache.get(run_id)
        if cached is not None:
            return cached
        try:
            run = await (await session.run.aload(run_id)).aget_data()
        except FileNotFoundError:
            return "", None  # Retention can remove old execution history.
        reasoning = "\n\n".join(item.get("reasoning_content", "")
            for item in run.metadata.get("completions", []) if item.get("reasoning_content"))
        elapsed = None
        active = str(run.status) in ("pending", "running")
        if run.started_at and (run.ended_at or str(run.status) == "running"):
            end = datetime.fromisoformat(run.ended_at) if run.ended_at else datetime.now(timezone.utc)
            elapsed = max(0.0, (end - datetime.fromisoformat(run.started_at)).total_seconds())
        result = reasoning, elapsed
        if not active:
            self._run_display_cache[run_id] = result
        return result

    async def _messages(self, session, messages):
        visible = []
        for message in messages:
            role = str(message.role)
            if role not in ("user", "assistant"):
                continue
            elapsed = None
            if role == "assistant" and message.run_id:
                reasoning, elapsed = await self._run_display(session, message.run_id)
                if reasoning:
                    visible.append(ChatMessage("reasoning", reasoning, id=message.id + ":reasoning"))
            status = str(message.metadata.get("steering", {}).get("status", message.status))
            timestamp = (datetime.fromisoformat(message.created_at).astimezone().strftime("%Y-%m-%d %H:%M:%S")
                         if role == "user" else "")
            visible.append(ChatMessage(role, message.content or self.t("waiting"), timestamp,
                                       status=status, id=message.id,
                                       author=self.config.user_profile.display_name if role == "user" else "",
                                       elapsed_seconds=elapsed))
        return tuple(visible)

    def _model(self, config, engine, session_config=None):
        if engine not in self.backend.engines.names():
            return ""
        implementation = self.backend.engines.resolve(engine)
        describe = getattr(implementation, "configuration", None)
        if describe:
            return describe(config, engine, session_config=session_config).get("values", {}).get("config", {}).get("completion", {}).get("model", "")
        return ""

    def _requires_model(self, config, engine, session_config=None):
        from llm.engines.loop import LoopEngine
        if engine not in self.backend.engines.names():
            return False
        implementation = self.backend.engines.resolve(engine)
        # Graph/사용자 엔진에는 LLM 모델이 필요하다고 추정하지 않는다.
        if not isinstance(implementation, LoopEngine):
            return False
        view = implementation.configuration(config, engine, session_config=session_config)
        # JSON으로 표현하지 않은 host client/factory는 조회만으로 모델 누락을 판정하지 않는다.
        if "completion" in view.get("runtime", ()):
            return False
        return not view["values"].get("config", {}).get("completion", {}).get("model")

    def _saved_selection(self, project_id=None):
        try:
            store = ViewStateStore(self.config.workspace)
            return store.load(project_id)[0] if project_id else store.last_project()
        except (OSError, ValueError, TypeError):
            return ""  # Corrupt UI state must not prevent opening the backend.

    async def start(self) -> None:
        from .bootstrap import select_or_create
        self.backend = self._factory(self.config)
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
            elapsed = (await self._run_display(session, run.id))[1] if run else None
            rows.append({"id": request.id, "text": request.content, "time": request.created_at,
                         "status": str(run.status if run else assistants[-1].status if assistants else request.status),
                         "engine": run.engine if run else "", "elapsed": elapsed,
                         "response": "\n".join(m.content for m in assistants),
                         "run_id": request.run_id or "", "error": str(run.error or "") if run else ""})
        return rows

    async def delete_turn(self, session_id, request_id):
        session = self.sessions[session_id]
        status = await session.run.astatus()
        if status.active_run_id or status.queued_count or session_id in self.namer.tasks:
            raise ValueError(self.t("history_busy"))
        await session.run.shutdown()
        try:
            await session.adelete_turn(request_id)
        finally:
            await session.run.start()
        self.dirty.set()
        return True

    async def project_activity(self):
        """실행 이력은 기존 파생 인덱스로 조회한다. 운영 로그/도메인 파일을 순회하지 않는다."""
        return [event.to_dict() for event in await self.project.aactivity(limit=300, newest_first=False)]

    async def execution_catalog(self, session_id: str) -> dict:
        from llm.engines.graph import GraphEngine
        project = self.sessions[session_id].project if session_id else self.project
        record = await project.aget_data()
        engines = {name: {"kind": "graph" if isinstance(self.backend.engines.resolve(name), GraphEngine)
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

    async def submit(self, session_id: str, text: str, engine: str | None = None,
                     engine_options: dict | None = None) -> str:
        if not text.strip():
            raise ValueError(self.t("empty_message"))
        if session_id not in self.sessions:
            raise ValueError(self.t("session_required"))
        session = self.sessions[session_id]
        selected = engine if engine is not None else self.config.engine
        project = await session.project.aget_data()
        if self._requires_model(project.config, selected, (await session.aget_data()).config):
            raise ValueError(self.t("model_required"))
        if selected not in selected_engines(project.config, self.backend.engines.names()):
            raise ValueError(self.t("settings_engine_unavailable", name=selected))
        if selected.startswith("_hub_"):
            raise ValueError("Internal engine cannot receive conversation requests")
        request = await session.run.submit(text, engine=selected, engine_options=engine_options or {})
        self.dirty.set()
        return request.id

    async def submission_state(self, session_id: str) -> dict:
        status = await self.sessions[session_id].run.astatus()
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

    async def snapshot(self) -> HubSnapshot:
        from llm.services.query import Query
        project = await self.project.aget_data()
        model = self._model(project.config, self.config.engine)
        if not self.sessions:
            return HubSnapshot(project.id, project.title, model,
                project.conversation_storage, (), "", (), "", self.t("session_required"),
                selected_engines(project.config, self.backend.engines.names()),
                file_root=str(self.config.file_root or self.project.paths.root),
                notifications=tuple(self._notifications), components=tuple(project.components))
        summaries = []
        for identifier, session in self.sessions.items():
            record = await session.aget_data()
            self._notification_sessions[identifier] = (project.id, record.title)
            runtime = await session.run.astatus(queued_limit=0)
            # Reconcile dropped observation events from authoritative Run records.
            latest = await session.run.alist(query=Query(descending=True, limit=1))
            if latest:
                run = await latest[0].aget_data()
                state = (run.id, str(run.status))
                previous = self._observed_runs.get(identifier)
                if identifier in self._observed_runs and previous != state:
                    self._record_notification((project.id, record.title), run)
                else:
                    self._observed_runs[identifier] = state
            elif identifier not in self._observed_runs:
                self._observed_runs[identifier] = None
            summaries.append(SessionSummary(identifier, record.title,
                                            self.t("queued", status=self.t.status(runtime.status), count=runtime.queued_count)))
            if record.metadata.get("hub_auto_title") and not runtime.active_run_id:
                if latest and str((await latest[0].aget_data()).status) == "completed":
                    self.namer.schedule(session, latest[0].id)
                elif not latest and record.metadata.get("hub_title_engine"):
                    self.namer.schedule(session, "clone")
        session = self.sessions[self.selected_id]
        session_config = (await session.aget_data()).config
        model = self._model(project.config, self.config.engine, session_config)
        messages = await session.aconversation()
        visible = await self._messages(session, messages)
        runtime = await session.run.astatus(queued_limit=0)
        runs = await session.run.alist(query=Query(descending=True, limit=1))
        detail = f"  {self.t('workspace')}\n  {project.id}\n\n  {self.t('session')}\n  {session.id}\n"
        notice = self.t("queued", status=self.t.status(runtime.status), count=runtime.queued_count)
        activity = ""
        if runs:
            handle = runs[0]
            run = await handle.aget_data()
            steps = await handle.steps.alist()
            if runtime.active_run_id:
                seconds = int((datetime.now(timezone.utc) - datetime.fromisoformat(run.started_at)).total_seconds()) if run.started_at else 0
                activity = self.t("run_progress", status=self.t.status(run.status), engine=run.engine,
                                  seconds=seconds, count=runtime.queued_count)
                if steps:
                    step = steps[-1]
                    activity += "\n " + self.t("phase", name=step.name, status=self.t.status(step.status))
            reasoning = "\n\n".join(item.get("reasoning_content", "") for item in run.metadata.get("completions", [])
                                     if item.get("reasoning_content"))
            if not reasoning and runtime.active_run_id:
                detail += "\n" + self.t("no_reasoning")
            detail += f"\n\n  {self.t('run')} / {self.t.status(run.status)}\n  {run.id}\n\n  {self.t('steps')}\n"
            detail += "\n".join(f"  {self.t.status(step.status)} · {step.name}" for step in steps)
            completions = run.metadata.get("completions", [])
            if completions:
                last = completions[-1]
                tokens = last.get("usage", {}).get("total_tokens")
                detail += "\n\n" + self.t("completion_detail", model=last.get("model") or self.config.model or "—",
                                           count=len(completions), tokens=tokens if tokens is not None else "—")
            if run.error:
                notice = f"{self.t.status(run.status)}: {run.error}"
                detail += f"\n\n  {run.error}"
            elif str(run.status) == "paused":
                notice = self.t("paused")
        if project.conversation_storage == "memory":
            notice += " · " + self.t("memory")
        if session.id in self.namer.errors:
            notice = self.t("title_failed", error=self.namer.errors[session.id])
        if self._requires_model(project.config, self.config.engine, session_config):
            notice = self.t("model_required")
        return HubSnapshot(project.id, project.title, model,
                           project.conversation_storage, tuple(summaries), self.selected_id,
                           visible, detail, notice,
                           selected_engines(project.config, self.backend.engines.names()),
                           activity, str(self.config.file_root or self.project.paths.root), tuple(self._notifications),
                           tuple(project.components))

    async def close(self) -> None:
        await self.namer.close()
        for subscription in self._subscriptions:
            await subscription.aclose()
        self._subscriptions.clear()
        if self.backend is not None:
            await self.backend.shutdown()
