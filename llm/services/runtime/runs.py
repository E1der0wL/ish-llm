"""Session별 직렬 실행을 담당한다. 입력을 먼저 영속 큐에 저장하고 Engine 이벤트를 대화/Step/결과 저장으로 연결한다. 중단된 Run은 자동 재실행하지 않는다."""

from llm.services.infrastructure.storage import read_domain_record, atomic_domain_json

from llm.services.query import FileCatalog, Query, select
from llm.services.infrastructure.journal import OutputJournal
from llm.providers.calls import ProviderCalls, ProviderCapacityError
from llm.services.runtime.output import OutputPolicy, OutputProjection, RunOutputState, consume_events
from typing import Optional, Union
import asyncio
import inspect
import json
from contextlib import closing
from collections.abc import Callable
from itertools import count
from copy import deepcopy
from dataclasses import asdict, replace

from llm.core.models import (
    Message, MessageRole, MessageStatus, Project, Run, RunStatus, StepStatus,
    Session, SessionStatus, new_id, now,
)
from llm.core.paths import RunPaths
from llm.core.interactions import InteractionRequest, InteractionResponse
from llm.engines.base import EngineContext, EngineEvent, EngineEventType, required_capabilities
from llm.engines.registry import EngineRegistry
from llm.components.tools import ToolRegistry
from llm.services.history.conversation import Conversation
from llm.services.history.context import ConversationContextBuilder
from llm.components.registry import ComponentRegistry
from llm.components.tools.resolver import CapabilityResolver, ComponentToolResolver
from llm.services.lifecycle.steps import StepEventRecorder, StepManager
from llm.services.infrastructure.storage import (
    StorageIO, child, drain_on_cancel, record,
)
from llm.services.lifecycle.sessions import SessionManager, SessionRuntime
from llm.services.infrastructure.logging import log_event
from llm.services.results import RunResultQuery
from llm.core.contracts import Diagnostic, ResourceRef
from llm.core.plans import ResumePlan
from llm.core.results import CompletionResult, EngineOutput, EngineDelta
from llm.compat import dataclass, StrEnum, timeout
from llm.services.runtime.policies import ProjectPolicyResolver, ExecutionLimitError
from llm.services.runtime.tools import ToolPolicy, ToolExecutionScope
from llm.services.runtime.operations import OperationRepository, ToolOperations
from llm.services.runtime.events import EventHandlers, EventContext, EventSubscriptions
from llm.services.runtime.checkpoints import CheckpointRepository, checkpoint_digest
from llm.services.runtime.interactions import InteractionRepository
from llm.services.runtime.pending import PendingWork
from llm.services.runtime.usage import UsageScope, check_admission, component_usage
from llm.providers.retry import retry_scope


# 실행 메타데이터를 저장한다. Facade와 실행 서비스가 같은 인스턴스를 공유한다.
class RunRepository:
    """Run metadata and paths beneath the owning Session's runs root."""

    def __init__(self, *, output_index_stride: int = 128, catalog_size: int = 4096):
        self.output_journal = OutputJournal(index_stride=output_index_stride)
        self.catalog = FileCatalog(catalog_size)
        self.usage_catalog = FileCatalog(catalog_size, projection=lambda r: (
            r["session_id"], [{key: entry[key] for key in (
                "started_at", "usage", "usage_complete", "reserved_tokens") if key in entry}
                for entry in r.get("metadata", {}).get("completions", [])]))

    def completion_history(self, session):
        """프로젝트 한도 검사에는 사용량 투영만 읽고 전체 Run 객체/조회 로그를 만들지 않는다."""
        if type(self).list is not RunRepository.list or type(self).load is not RunRepository.load:
            return [entry for run in self.list(session) for entry in run.metadata.get("completions", [])]
        entries = []
        for path in session.paths.runs.glob("*/run.json"):
            self.paths(session, path.parent.name)
            owner, values = self.usage_catalog.read(path)
            if owner != session.id:
                raise ValueError("Run ownership mismatch")
            entries.extend(deepcopy(values))
        return entries

    def delete_history(self, session, run_id):
        """보관 서비스가 승인한 완료 Run의 기록만 삭제한다. 재실행/상태 전이는 하지 않는다."""
        from llm.services.infrastructure.storage import remove_named_tree
        paths = self.paths(session, run_id)
        if not paths.root.exists():
            return
        run = self.load(session, run_id)
        if run.status != RunStatus.COMPLETED:
            raise ValueError("Only completed Run history can be pruned")
        remove_named_tree(session.paths.runs, paths.root, run_id)

    def recover_history_deletions(self, session, run_ids):
        from llm.services.infrastructure.storage import recover_deletions
        for identifier in run_ids:
            self.paths(session, identifier)
        return recover_deletions(session.paths.runs, allowed=set(run_ids))

    def read_backup(self, session):
        """자신이 소유하는 Run/출력/체크포인트만 읽어서 검증한다."""
        runs = []
        for path in session.paths.runs.glob("*/run.json"):
            data = read_domain_record(path)
            if child(session.paths.runs, data["id"]) != path.parent or data["session_id"] != session.id:
                raise ValueError("Backup Run ownership mismatch")
            run = Run(**{**data, "paths": RunPaths(path.parent), "status": RunStatus(data["status"])})
            if run.status in (RunStatus.PENDING, RunStatus.RUNNING):
                raise ValueError("Backup contains an unfinished Run")
            self.output_events(run)
            for name in run.metadata.get("checkpoints", ()):
                self.checkpoint(run, name)
            runs.append(run)
        return runs

    def paths(self, session: Session, run_id: str) -> RunPaths:
        return RunPaths(child(session.paths.runs, run_id))

    def claim_tool_operation(self, session, call):
        return OperationRepository().claim(session, call)

    def complete_tool_operation(self, session, call, result):
        return OperationRepository().complete(session, call, result)

    def fail_tool_operation(self, session, call, evidence):
        return OperationRepository().not_applied(session, call, evidence)

    def tool_operation(self, session, key):
        return OperationRepository().load(session, key)

    def reconcile_tool_operation(self, session, key, *, result, evidence):
        return OperationRepository().reconcile(session, key, result=result, evidence=evidence)

    def verify_tool_operation(self, session, key, observation, expected_version):
        return OperationRepository().verify(session, key, observation, expected_version)

    def save(self, run: Run) -> None:
        if run.status not in (RunStatus.PENDING, RunStatus.RUNNING) and "completions" in run.metadata:
            run.metadata["completions"] = [asdict(CompletionResult.for_run(data, run))
                                           for data in run.metadata["completions"]]
        atomic_domain_json(run.paths.root / "run.json", record(run))
        log_event(run.paths.logs, "run.saved", entity_id=run.id, status=run.status)

    def load(self, session: Session, run_id: str) -> Run:
        paths = self.paths(session, run_id)
        data = read_domain_record(paths.root / "run.json")
        if data["id"] != run_id or data["session_id"] != session.id:
            raise ValueError("Run ownership mismatch")
        log_event(paths.logs, "run.loaded", entity_id=run_id)
        return Run(**{**data, "status": RunStatus(data["status"]), "paths": paths})

    def record_output(self, run: Run, value: Union[EngineDelta, EngineOutput]) -> None:
        self.output_journal.append(run.paths.state / "outputs.jsonl", [value])

    def record_outputs(self, run: Run, values) -> None:
        """묶음 쓰기 확장점. 단일 쓰기를 재정의한 사용자 저장소의 계약도 유지한다."""
        if type(self).record_output is not RunRepository.record_output:
            for value in values:
                self.record_output(run, value)
        else:
            self.output_journal.append(run.paths.state / "outputs.jsonl", values)

    def output_events(self, run: Run, *, after: int = 0, limit: Optional[int] = None):
        return self.output_journal.read(run.paths.state / "outputs.jsonl", after=after, limit=limit)

    def reconcile_output(self, run, store):
        """저널이 Conversation보다 앞선 부분 저장을 복구한다. 실행 효과나 Run 성공 상태는 재현하지 않는다."""
        values = self.outputs(run)
        if not values:
            return False
        visible = [v for v in values if v.visibility == "user"]
        final = next((v for v in visible if v.final and v.step_id is None and (v.text or v.data is None)), None)
        text = final.text if final is not None else "".join(v.text for v in visible)
        message = store.get(run.assistant_message_id)
        if message.run_id != run.id or message.role != MessageRole.ASSISTANT:
            raise ValueError("Output reconciliation ownership mismatch")
        if message.content == text:
            return False
        store.reconcile(message.id, text, run_id=run.id)
        return True

    def outputs(self, run: Run) -> list[EngineOutput]:
        """부분/확정 출력을 ID별로 투영한다. Run/Step 상태는 별도로 조회한다."""
        projection = OutputProjection()
        # 사용자 저장소/저널의 기존 읽기 확장점을 우회하지 않는다.
        if (type(self).output_events is RunRepository.output_events
                and type(self.output_journal).read is OutputJournal.read):
            with closing(self.output_journal.iter_events(run.paths.state / "outputs.jsonl")) as events:
                for value in events:
                    projection.add(value)
        else:
            for value in self.output_events(run):
                projection.add(value)
        return list(projection.snapshot().values())

    def record_checkpoint(self, run: Run, event: EngineEvent) -> None:
        CheckpointRepository().record(run, event)
        if event.metadata["operation"] == "initialize":
            # 이 표시를 저장하기 전에는 Engine에 ACK를 보내지 않는다. 초기 복사 전의
            # 재개 시도만 원본으로 복구할 수 있고, 실행 이후 파일 누락은 손상으로 거부한다.
            run.metadata.setdefault("checkpoints", []).append(event.metadata["name"])
            self.save(run)

    def interaction_requests(self, session: Session, run: Run) -> list[InteractionRequest]:
        names = run.metadata.get("checkpoints", []) or ([run.metadata["resume"]["checkpoint"]] if "resume" in run.metadata else [])
        requests = []
        for name in names:
            values = InteractionRepository().requests({**self.resolve_checkpoint(session, run, name), "name": name}, run)
            values = InteractionRepository().effective(run, values)
            if any(v.binding.get("checkpoint") != name for v in values):
                raise ValueError("Interaction checkpoint binding mismatch")
            requests.extend(values)
        if len({v.id for v in requests}) != len(requests):
            raise ValueError("Duplicate interaction identity")
        return requests

    def interaction_responses(self, session: Session, run: Run) -> list[InteractionResponse]:
        return InteractionRepository().responses(run, self.interaction_requests(session, run))

    def respond(self, session: Session, run: Run, response: InteractionResponse) -> InteractionResponse:
        return InteractionRepository().respond(run, self.interaction_requests(session, run), response)

    def interaction_decisions(self, session: Session, run: Run, checkpoint: dict, explicit: dict, *, retry_nodes=(), confirm=False):
        """조회와 실행이 같은 응답 저장소를 사용한다. 주입된 저장소도 이 경로를 공유한다."""
        interactions = InteractionRepository()
        requests = self.interaction_requests(session, run)
        if any(interactions.envelope(run, r).get("cancelled") for r in requests):
            raise ValueError("Interaction is cancelled; renew before resuming")
        return interactions.decisions(requests, self.interaction_responses(session, run), explicit,
                                      retry_nodes=retry_nodes, confirm=confirm)

    def apply_interaction_policy(self, session, run, policy):
        """정책 응답만 저장한다. 실행 재개와 호스트 허용 여부 검사는 별도다."""
        if not policy.get("enabled", False):
            return []
        ranks = {"low": 0, "medium": 1, "high": 2}
        answered = {r.request_id for r in self.interaction_responses(session, run)}
        saved = []
        for request in self.interaction_requests(session, run):
            if (request.id in answered or request.expired or request.risk not in ranks
                    or request.category == "execution.retry_uncertain" or not request.action.get("auto_approval_allowed")
                    or InteractionRepository().envelope(run, request).get("cancelled")):
                continue
            for rule in policy.get("rules", []):
                if rule["category"] == request.category and ranks[request.risk] <= ranks[rule["max_risk"]]:
                    option = next((o for o in request.options if o.effect == "approve"), None)
                    if option is not None:
                        response = replace(request.respond(option.id), actor="policy", policy_id=rule["id"])
                        saved.append(self.respond(session, run, response))
                    break
        return saved

    def resume_link(self, session, run, store):
        message = next((m for m in store.list() if m.metadata.get("resume", {}).get("run_id") == run.id
                        and m.status != MessageStatus.CANCELLED), None)
        resumed = next((r for r in self.list(session) if r.metadata.get("resume", {}).get("run_id") == run.id), None)
        return message, resumed

    def interaction_views(self, session, run, store):
        message, resumed = self.resume_link(session, run, store)
        return InteractionRepository().views(run, self.interaction_requests(session, run),
            self.interaction_responses(session, run), resume_message=message, resumed_run=resumed)

    def change_interaction(self, session, run, request, *, operation, expires_at=None):
        current = next((r for r in self.interaction_requests(session, run) if r.id == request.id), None)
        if current is None or current.fingerprint != request.fingerprint:
            raise ValueError("Interaction changed; reload before editing")
        repository = InteractionRepository()
        if operation == "cancel":
            repository.cancel(run, current)
            return current
        if operation == "renew":
            return repository.renew(run, current, expires_at=expires_at)
        raise ValueError("Unknown interaction operation")

    def checkpoint(self, run: Run, name: str = "graph") -> dict:
        return CheckpointRepository().load(run, name)

    def resolve_checkpoint(self, session: Session, run: Run, name: str = "graph") -> dict:
        """초기 복사 전에 끝난 재개 시도만 검증된 원본을 따라 복원한다."""
        selected, visited = run, set()
        chain = []
        while True:
            if selected.id in visited:
                raise ValueError("Cyclic resume lineage")
            visited.add(selected.id)
            try:
                checkpoint = self.checkpoint(selected, name)
                break
            except FileNotFoundError:
                descriptor = selected.metadata.get("resume")
                if not descriptor or name in selected.metadata.get("checkpoints", ()):
                    raise
                if descriptor["checkpoint"] != name:
                    raise ValueError("Resume checkpoint name mismatch")
                parent = self.load(session, descriptor["run_id"])
                if parent.engine != run.engine or parent.status not in (
                        RunStatus.PAUSED, RunStatus.FAILED, RunStatus.INTERRUPTED):
                    raise ValueError("Invalid resume checkpoint parent")
                chain.append((selected, descriptor))
                selected = parent
        for attempt, descriptor in reversed(chain):
            if checkpoint_digest(checkpoint) != descriptor["digest"]:
                raise ValueError("Resume checkpoint changed before initialization")
            checkpoint = {**checkpoint, "run_id": attempt.id, "engine": attempt.engine}
        return checkpoint

    def list(self, session: Session, *, query=None) -> list[Run]:
        if type(query) is Query:
            return [self.load(session, identifier) for identifier in self.catalog.select(session.paths.runs.glob("*/run.json"), query)]
        return select(sorted((self.load(session, path.parent.name)
                       for path in session.paths.runs.glob("*/run.json")),
                      key=lambda run: (run.created_at, run.id)), query)


class RunEventPublisher:
    def __init__(self, callback: Optional[Callable[[Run, EngineEvent], None]] = None, on_error=None) -> None:
        self.callback = callback
        self.on_error = on_error

    async def publish(self, run: Run, event: EngineEvent) -> None:
        # UI callbacks must be synchronous and nonblocking. Persistence has
        # already succeeded; a display exception must not fail the Engine.
        if self.callback is not None:
            try:
                result = self.callback(deepcopy(run), deepcopy(event))
                if inspect.isawaitable(result):
                    await result
            except Exception:
                if self.on_error is not None:
                    self.on_error(run)
                else:
                    log_event(run.paths.logs, "observer.failed", entity_id=run.id)


class RunErrorCode(StrEnum):
    ENGINE_REQUIRED = "engine_required"
    ENGINE_NOT_REGISTERED = "engine_not_registered"
    COMPONENT_NOT_REGISTERED = "component_not_registered"
    CAPABILITY_FAILED = "capability_failed"
    ENGINE_FAILED = "engine_failed"
    PROVIDER_CAPACITY = "provider_capacity"
    INTERRUPTED = "interrupted"
    PROCESS_RESTART = "process_restart"
    RESUME_REJECTED = "resume_rejected"
    QUEUE_FULL = "queue_full"
    POLICY_UNAVAILABLE = "policy_unavailable"
    REQUEST_CANCELLED = "request_cancelled"
    RUN_TIMEOUT = "run_timeout"
    TOOL_DENIED = "tool_denied"
    TOOL_CONTRACT = "tool_contract"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_BUDGET_EXCEEDED = "tool_budget_exceeded"
    CONTEXT_BUDGET_EXCEEDED = "context_budget_exceeded"
    OPERATION_CONFLICT = "operation_conflict"
    OPERATION_UNCERTAIN = "operation_uncertain"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
    PROCESS_FAILED = "process_failed"


class RunRequestError(ValueError):
    """An actionable request rejection before queue admission."""

    def __init__(self, code: RunErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)

    @property
    def diagnostic(self) -> Diagnostic:
        return Diagnostic.from_exception(self)


class RunEventType(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class RunEvent:
    type: RunEventType
    run: Run


class _RunPaused(Exception):
    """Engine이 저장된 경계에서 실행을 양보했다. 실패나 자동 재개가 아니다."""


# 영속 요청 큐를 실행으로 전환하고 이벤트 저장과 복구를 책임진다.
class RunManager:
    """One Session-bound scheduler; separate instances execute separate Sessions."""

    def __init__(self, sessions: SessionManager, engines: EngineRegistry, *, session: Session,
                 steps: Optional[StepManager] = None,
                 on_event: Optional[Callable[[Run, EngineEvent], None]] = None,
                 on_run_event: Optional[Callable[[RunEvent], None]] = None,
                 repository: Optional[RunRepository] = None,
                 capabilities: Optional[Union[CapabilityResolver, ComponentRegistry]] = None,
                 conversations: Optional[Callable[[Session], Conversation]] = None,
                 context_builder: Optional[ConversationContextBuilder] = None,
                 event_handlers: Optional[EventHandlers] = None,
                 subscriptions: Optional[EventSubscriptions] = None,
                 tool_policy: Optional[ToolPolicy] = None,
                 policy_resolver=None, provider_calls=None, output_policy=None) -> None:
        if not isinstance(session, Session):
            raise TypeError("RunManager requires a Session")
        self._session = deepcopy(session)
        self.sessions = sessions
        self.engines = engines
        self.repository = repository if repository is not None else sessions.run_repository
        if self.repository is None:
            self.repository = RunRepository()
            sessions.run_repository = self.repository
        self.steps = steps if steps is not None else StepManager()
        self.recorder = StepEventRecorder(self.steps)
        self.results = RunResultQuery(sessions, self.repository)
        self.events = RunEventPublisher(on_event, self._observer_failed)
        self.on_run_event = on_run_event
        self.event_handlers = event_handlers if event_handlers is not None else EventHandlers()
        self.subscriptions = subscriptions if subscriptions is not None else EventSubscriptions()
        self._owns_subscriptions = subscriptions is None
        if capabilities is None:
            capabilities = ComponentRegistry()
        self.capabilities = (ComponentToolResolver(capabilities, data_factory=self._component_data)
                             if isinstance(capabilities, ComponentRegistry) else capabilities)
        if not callable(getattr(self.capabilities, "resolve", None)):
            raise TypeError("Capability resolver must implement resolve(project, names)")
        self.conversations = conversations if conversations is not None else sessions.conversations
        self.context_builder = context_builder if context_builder is not None else sessions.context_builder
        self._active: Optional[SessionRuntime] = None
        self._closed = False
        self._control = None
        self._io = StorageIO(sessions.ownership)
        self._store_instance: Optional[Conversation] = None
        self._request_changed: Optional[asyncio.Event] = None
        self.policy_resolver = policy_resolver if policy_resolver is not None else ProjectPolicyResolver()
        self.tool_policy = tool_policy if tool_policy is not None else ToolPolicy()
        self.provider_calls = provider_calls if provider_calls is not None else ProviderCalls()
        self.output_policy = output_policy or OutputPolicy()
        self.pending_work = PendingWork()

    def _check_component_access(self):
        if self._closed:
            raise RuntimeError("RunManager is shut down")

    def _component_data(self, project, name):
        """실행용 capability도 기존 저장소·잠금·수명 검사를 공유한다."""
        from llm.services.lifecycle.components import ComponentData
        registry = self.capabilities.components
        component = registry.get(name)
        data_class = getattr(component, "data_class", None) or ComponentData
        return data_class(self.sessions.project_access, registry, project, name).bind_model_usage(
            self.sessions, getattr(self.policy_resolver, "token_counters", {})).bind_runtime(
            runner=self._io.run, access_check=self._check_component_access,
            history_reader=lambda session_id, run_id, tool_call_id, **options: self.results.tool_result(
                project, session_id, run_id, tool_call_id, steps=self.steps, **options))

    def _observer_failed(self, run):
        """실행 루프에서 발생한 관찰자 오류에도 백엔드의 로그 설정을 적용한다."""
        from llm.services.infrastructure.logging import logging_scope
        with logging_scope(self.sessions.ownership.logger):
            log_event(run.paths.logs, "observer.failed", entity_id=run.id)

    def _control_lock(self):
        if self._control is None:
            self._control = asyncio.Lock()
        return self._control

    def _notify_requests(self):
        if self._request_changed is not None:
            self._request_changed.set()
            self._request_changed = None

    async def _publish_run(self, run: Run) -> None:
        self._notify_requests()
        event = RunEvent(RunEventType(run.status.value if run.status != RunStatus.RUNNING else "started"),
                         deepcopy(run))
        if self.on_run_event is not None:
            try:
                result = self.on_run_event(deepcopy(event))
                if inspect.isawaitable(result):
                    await result
            except Exception:
                self._observer_failed(run)
        await self.subscriptions.publish("run", event, on_error=lambda: self._observer_failed(run))

    def _store(self, session: Session) -> Conversation:
        if (session.project_id, session.id) != (self._session.project_id, self._session.id):
            raise ValueError("Session does not match this RunManager")
        if self._store_instance is None:
            self._store_instance = self.conversations(session)
        return self._store_instance

    def _prepare(self):
        project = self.sessions._owner(self._session)
        current = self.sessions.load(project, self._session.id)
        if current.status == SessionStatus.DELETED:
            raise ValueError("Session is deleted")
        return project, current

    def _validate_request(self, engine: str) -> str:
        if not isinstance(engine, str) or not engine.strip():
            raise RunRequestError(RunErrorCode.ENGINE_REQUIRED, "Specify an Engine for each request")
        project, _ = self._prepare()
        if engine not in self.engines.names():
            raise RunRequestError(RunErrorCode.ENGINE_NOT_REGISTERED, "Requested Engine is not registered")
        try:
            self.policy_resolver.resolve(project.config.policies)
        except (ValueError, TypeError, ExecutionLimitError) as error:
            raise RunRequestError(RunErrorCode.POLICY_UNAVAILABLE, str(error)) from error
        if isinstance(self.capabilities, ComponentToolResolver):
            try:
                self.capabilities.components.validate(project.components)
            except ValueError as error:
                raise RunRequestError(RunErrorCode.COMPONENT_NOT_REGISTERED, str(error)) from error
        return engine

    async def _runtime(self) -> SessionRuntime:
        # Caller holds _control. OS ownership is retained before recovery; the
        # worker and all pending storage finish before that ownership is released.
        if self._closed:
            raise RuntimeError("RunManager is shut down")
        project, current = await self._io.run(self._prepare)
        if self._active is not None:
            runtime = self._active
            if runtime.worker is not None and runtime.worker.done():
                runtime.worker.result()
                raise RuntimeError("Session worker has stopped")
            return runtime

        def recover():
            # Revalidate under the same ownership scope as attachment/recovery.
            owner, fresh = self._prepare()
            self.sessions.attach_runtime(fresh)
            try:
                recovered = self._recover(fresh)
                queued = [message.id for message in self._store(fresh).list()
                          if message.role == MessageRole.USER
                          and message.status == MessageStatus.QUEUED]
                log_event(fresh.paths.logs, "runtime.started", entity_id=fresh.id,
                          count=len(queued))
                return owner, fresh, queued, recovered
            except BaseException:
                self.sessions.detach_runtime(fresh)
                raise

        project, current, queued, recovered = await self._io.run(recover)
        for run in recovered:
            await self._publish_run(run)
        runtime = SessionRuntime(deepcopy(project), current)
        for message_id in queued:
            runtime.queue.put_nowait(message_id)
        self._active = runtime
        runtime.worker = asyncio.create_task(self._worker(runtime), name=f"llm-session-{current.id}")
        return runtime

    async def _start(self) -> SessionRuntime:
        async with self._control_lock():
            return await self._runtime()

    async def _submit(self, content: str,
                      engine: str) -> Message:
        async with self._control_lock():
            if self._closed:
                raise RuntimeError("RunManager is shut down")
            selected = await self._io.run(self._validate_request, engine)
            runtime = await self._runtime()
            def persist():
                self._check_queue(runtime.session)
                message = self._store(runtime.session).create(
                    MessageRole.USER, content, MessageStatus.QUEUED,
                    metadata={"engine": selected})
                log_event(runtime.session.paths.logs, "request.queued", entity_id=message.id,
                          related_id=runtime.session.id)
                return message

            message = await self._io.run(persist)
            # Accepted submission is shielded through this insertion, including
            # caller cancellation and concurrent shutdown. The selected store is
            # updated first (fsync for the default file store).
            runtime.queue.put_nowait(message.id)
            return message

    def _check_queue(self, session):
        project = self.sessions._owner(session)
        maximum = project.config.policies.get("run", {}).get("max_queued")
        if maximum is not None and self._store(session).count(
                status=MessageStatus.QUEUED, role=MessageRole.USER) >= maximum:
            raise RunRequestError(RunErrorCode.QUEUE_FULL, "Session request queue is full")

    def _cancel_queued(self, message_id):
        _, session = self._prepare()
        store = self._store(session)
        message = store.get(message_id)
        if message.role != MessageRole.USER:
            raise ValueError("Request ID must identify a user message")
        if message.status != MessageStatus.QUEUED or message.run_id is not None:
            return False
        # 새 백엔드의 복구 이전에도 이미 Run으로 청구된 요청은 취소하지 않는다.
        if self._active is None and any(run.input_message_id == message_id for run in self.repository.list(session)):
            return False
        store.set_status(message_id, MessageStatus.CANCELLED)
        return True

    async def _cancel_request(self, message_id):
        async with self._control_lock():
            if self._closed:
                raise RuntimeError("RunManager is shut down")
            cancelled = await self._io.run(self._cancel_queued, message_id)
            if cancelled:
                self._notify_requests()
            return cancelled

    def _resume_request(self, runtime, run_id, engine, retry_nodes, decisions, *, preview=False):
        """소유권 잠금 안에서 중복 재개를 거부하고 요청을 먼저 영속 큐에 저장한다."""
        if self.pending_work.active:
            raise RunRequestError(RunErrorCode.RESUME_REJECTED, "Cancelled work is still finishing")
        self._validate_request(engine)
        self._check_queue(runtime.session)
        source = self.repository.load(runtime.session, run_id)
        if source.engine != engine or source.status not in (
                RunStatus.PAUSED, RunStatus.INTERRUPTED, RunStatus.FAILED):
            raise ValueError("Resume requires a paused/interrupted/failed Run and its original Engine")
        strategy = self.engines.resolve(engine)
        name = getattr(strategy, "checkpoint_name", None)
        if not name or not callable(getattr(strategy, "validate_resume", None)):
            raise ValueError("Engine does not support checkpoint resume")
        checkpoint = self.repository.resolve_checkpoint(runtime.session, source, name)
        decisions, receipts, retry_nodes = self.repository.interaction_decisions(
            runtime.session, source, checkpoint, decisions, retry_nodes=retry_nodes, confirm=True)
        strategy.validate_resume(checkpoint, retry_nodes=retry_nodes, **({"decisions": decisions} if decisions else {}))
        # 파일 대화는 대기 요청도 재시작 뒤 유지한다. 메모리 큐가 없어져도 이미 시작한
        # 재개 Run의 연결은 남으므로 같은 원본에서 부작용을 두 번 이어가지 않는다.
        store = self._store(runtime.session)
        if any(self.repository.resume_link(runtime.session, source, store)):
            raise ValueError("This Run already has a resume request; resume its latest attempt instead")
        descriptor = {"run_id": source.id, "checkpoint": name,
                      "digest": checkpoint_digest(checkpoint), "retry_nodes": list(retry_nodes), "decisions": deepcopy(decisions)}
        project = self.sessions.require_project(runtime.project)
        candidate = deepcopy(source)
        candidate.metadata["resume"] = descriptor
        candidate.metadata["policies"] = deepcopy(project.config.policies)
        strategy.validate_resume(checkpoint, retry_nodes=retry_nodes,
                                 context=self._context(runtime, candidate, project=project), **({"decisions": decisions} if decisions else {}))
        if preview:
            return descriptor
        for response in receipts:
            self.repository.respond(runtime.session, source, response)
        return store.create(MessageRole.USER, store.get(source.input_message_id).content, MessageStatus.QUEUED,
                            metadata={"engine": engine, "resume": descriptor})

    def _resume_plan(self, run_id, engine) -> ResumePlan:
        project, session = self._prepare()
        source = self.repository.load(session, run_id)
        views = self.repository.interaction_views(session, source, self._store(session))
        ref = ResourceRef("run", run_id, project_id=project.id, session_id=session.id, run_id=run_id)
        reused, retries, blockers, can_resume = [], [], [], False
        try:
            strategy = self.engines.resolve(engine)
            checkpoint = self.repository.resolve_checkpoint(session, source, strategy.checkpoint_name)
            reused = [k for k, v in checkpoint["records"].items() if v.get("status") == "completed"]
            retries = [v.request.binding["key"] for v in views if v.request.binding.get("target") == "retry_nodes"]
            self._resume_request(SessionRuntime(project, session), run_id, engine, (), {}, preview=True)
            can_resume = not any(v.status in ("pending", "denied", "expired", "cancelled", "submitted", "unavailable") for v in views)
            if not can_resume:
                blockers.append(Diagnostic("interaction_required", "Resolve pending interactions before execution", source=ref))
        except (ValueError, KeyError, RuntimeError, AttributeError, FileNotFoundError) as error:
            blockers.append(Diagnostic.from_exception(error, code="resume_rejected", source=ref))
        return ResumePlan(ref, engine, can_resume, views, reused, retries, blockers)

    async def _resume(self, run_id, engine, retry_nodes, decisions):
        async with self._control_lock():
            runtime = await self._runtime()
            try:
                message = await self._io.run(self._resume_request, runtime, run_id, engine, retry_nodes, decisions)
            except RunRequestError:
                raise
            except (ValueError, KeyError, FileNotFoundError, TypeError, RuntimeError) as error:
                raise RunRequestError(RunErrorCode.RESUME_REJECTED, str(error)) from error
            runtime.queue.put_nowait(message.id)
            return message

    def _request_run(self, message_id: str) -> Optional[Run]:
        _, session = self._prepare()
        message = self._store(session).get(message_id)
        if message.role != MessageRole.USER:
            raise ValueError("Request ID must identify a user message")
        if message.run_id is None:
            if message.status == MessageStatus.CANCELLED:
                raise RunRequestError(RunErrorCode.REQUEST_CANCELLED, "Request was cancelled before execution")
            if message.status != MessageStatus.QUEUED:
                raise ValueError("Message has no executable request")
            return None
        run = self.repository.load(session, message.run_id)
        if run.input_message_id != message.id:
            raise ValueError("Request and Run ownership mismatch")
        return run

    async def _shutdown(self) -> None:
        async with self._control_lock():
            try:
                await self._shutdown_locked()
            finally:
                if self._owns_subscriptions:
                    await self.subscriptions.close()

    async def _shutdown_locked(self) -> None:
        """Stop accepting input, interrupt active work, and preserve the durable queue."""
        self._closed = True
        runtime = self._active
        if runtime is None:
            return
        runtime.closed = True
        self.pending_work.wake()
        if runtime.execution is not None:
            runtime.execution.cancel()
        runtime.queue.put_nowait(None)
        try:
            if runtime.worker is not None:
                await runtime.worker
        finally:
            if runtime.worker is None or runtime.worker.done():
                await self._io.run(self._detach, runtime.session)
                self._store_instance = None
                self._active = None

    def _detach(self, session: Session) -> None:
        log_event(session.paths.logs, "runtime.stopped", entity_id=session.id)
        # 스토리지 스레드와 이벤트 루프 사이 완료 경쟁에서도 소유권을 정확히 해제한다.
        self.pending_work.when_idle(lambda: self.sessions.detach_runtime(session))

    def _recover(self, session: Session) -> list[Run]:
        from llm.services.history.recovery import recover_session
        return recover_session(self.sessions, self.repository, self.steps, session, self._store(session))

    def _begin(self, runtime: SessionRuntime, message: Message) -> Optional[Run]:
        from llm.services.infrastructure.transactions import watch
        watch(runtime.session)
        runtime.project = self.sessions.require_project(runtime.project)
        session = runtime.session
        store = self._store(session)
        # cancel과 시작을 같은 소유권/I/O 직렬 경로에서 판정한다.
        if store.get(message.id).status != MessageStatus.QUEUED:
            return None
        run_id = new_id()
        # 선택이 누락된 복구 요청은 실패시킨다. Engine을 추측하지 않는다.
        engine = message.metadata.get("engine")
        run = Run(run_id, session.id, message.id, new_id(),
                  engine if isinstance(engine, str) and engine.strip() else "",
                  self.repository.paths(session, run_id))
        # 대기 중 변경은 새 Run부터 반영한다. 실행 도중에는 저장된 값과 동일한 사본을 사용한다.
        run.metadata["policies"] = deepcopy(runtime.project.config.policies)
        if "resume" in message.metadata:
            run.metadata["resume"] = deepcopy(message.metadata["resume"])
        self.repository.save(run)
        store.bind_run(message.id, run.id)
        store.set_status(message.id, MessageStatus.COMMITTED)
        store.create(MessageRole.ASSISTANT, "", MessageStatus.STREAMING,
                     message_id=run.assistant_message_id, run_id=run.id)
        session.current_run_id = run.id
        session.status = SessionStatus.RUNNING
        self.sessions._save_runtime(session)
        run.status = RunStatus.RUNNING
        run.started_at = now()
        self.repository.save(run)
        log_event(run.paths.logs, "run.started", entity_id=run.id,
                  related_id=session.id, status=run.status)
        return run

    def _resume_checkpoint(self, session, run):
        descriptor = run.metadata["resume"]
        source = self.repository.load(session, descriptor["run_id"])
        if source.engine != run.engine or source.status not in (
                RunStatus.PAUSED, RunStatus.INTERRUPTED, RunStatus.FAILED):
            raise ValueError("Resume source is no longer eligible")
        checkpoint = self.repository.resolve_checkpoint(session, source, descriptor["checkpoint"])
        if checkpoint_digest(checkpoint) != descriptor["digest"]:
            raise ValueError("Resume checkpoint changed after admission")
        if any(request.expired for request in self.repository.interaction_requests(session, source)):
            raise ValueError("Interaction expired while resume was queued")
        return checkpoint

    def _context(self, runtime: SessionRuntime, run: Run, policies=None, *, project=None) -> EngineContext:
        context_policy, completion_policy, limits = (policies if policies is not None
                                                   else self.policy_resolver.resolve(run.metadata["policies"]))
        checkpoint = None
        store = self._store(runtime.session)
        if "resume" in run.metadata:
            checkpoint = self._resume_checkpoint(runtime.session, run)
            header = checkpoint["header"]
            try:
                history = tuple(store.get(identifier) for identifier in header["message_ids"])
            except KeyError as error:
                raise ValueError("Original conversation is unavailable; memory-mode resume requires the original backend") from error
            history = tuple(replace(item, id=run.input_message_id, run_id=run.id)
                            if item.id == header["input_message_id"] else item for item in history)
        else:
            history = self.context_builder.for_run(store.list(), run.input_message_id, policy=context_policy)
        # Copy domain state, but do not deepcopy Python handler closures or live
        # capabilities. The resolver returns a fresh registry for this Run.
        engine = self.engines.resolve(run.engine)
        names = required_capabilities(engine)
        project = deepcopy(project if project is not None else runtime.project)
        project.config["policies"] = deepcopy(run.metadata["policies"])
        values = {}
        if names:
            values = self.capabilities.resolve(project, names)
            if not isinstance(values, dict) or any(name not in values for name in names):
                raise ValueError("Capability resolver omitted a required capability")
        request = getattr(engine, "additional_capabilities", None)
        for expansion in count() if request is not None else ():
            extra = request(values)
            if (not isinstance(extra, tuple) or any(not isinstance(name, str) or not name.strip() for name in extra)
                    or len(set(extra)) != len(extra)):
                raise ValueError("additional_capabilities must return a tuple of distinct names")
            missing = tuple(name for name in extra if name not in values)
            if not missing:
                break
            if expansion == limits.max_capability_rounds:
                raise ValueError("Capability expansion limit exceeded")
            resolved = self.capabilities.resolve(project, missing)
            if not isinstance(resolved, dict) or any(name not in resolved for name in missing):
                raise ValueError("Capability resolver omitted a required capability")
            values.update(resolved)
        retries = run.metadata["policies"].get("tool_retry", {})
        tool_policy = replace(self.tool_policy, **{
            target: retries[source] for source, target in (("max_retries", "max_retries"), ("delay_seconds", "retry_delay"))
            if source in retries and target not in self.tool_policy._explicit_retry})
        tool_scope = ToolExecutionScope(tool_policy,
            operations=ToolOperations(deepcopy(runtime.session), self._io, self.repository, steps=self.steps))
        for name in values.get("tools", ToolRegistry()).names():
            tool_scope.validate(values["tools"].get(name))
        return EngineContext(project, deepcopy(runtime.session), deepcopy(run), history,
                             tools=values.get("tools", ToolRegistry()), capabilities=values,
                             checkpoint=checkpoint, tool_scope=tool_scope, pending_work=self.pending_work,
                             completion_policy=completion_policy)

    async def _consume_limited(self, runtime, run):
        from llm.providers.runtime import logging_scope as provider_logging_scope
        policies = self.policy_resolver.resolve(run.metadata["policies"])
        guard = timeout(policies[2].timeout_seconds)
        try:
            async with guard:
                counter = getattr(self.policy_resolver, "token_counters", {}).get(run.metadata["policies"].get("completion", {}).get("counter"))
                source = {"project_id": runtime.project.id, "session_id": runtime.session.id, "run_id": run.id}
                with provider_logging_scope(self.sessions.ownership.path.parent), self.provider_calls.scope(), UsageScope(run.metadata["policies"].get("usage", {}), counter, source).scope(), retry_scope(run.metadata["policies"].get("provider_retry", {})):
                    await self._consume(runtime, run, policies)
        except asyncio.TimeoutError as error:
            expired = guard.expired() if callable(guard.expired) else guard.expired
            if expired:
                raise ExecutionLimitError(RunErrorCode.RUN_TIMEOUT, "Run execution deadline exceeded") from error
            raise

    async def _consume(self, runtime: SessionRuntime, run: Run, policies) -> None:
        if not run.engine:
            raise RunRequestError(RunErrorCode.ENGINE_REQUIRED, "Queued request has no Engine selection")
        try:
            engine = self.engines.resolve(run.engine)
        except KeyError as error:
            raise RunRequestError(RunErrorCode.ENGINE_NOT_REGISTERED, "Requested Engine is not registered") from error
        store = self._store(runtime.session)
        try:
            if "resume" in run.metadata:
                checkpoint = await self._io.run(self._resume_checkpoint, runtime.session, run)
                # 실행 설정 검증이 실패해도 재개 시도의 원본 체크포인트는 남긴다.
                await self._io.run(self.repository.record_checkpoint, run,
                    EngineEvent(EngineEventType.CHECKPOINT, metadata={
                        "name": run.metadata["resume"]["checkpoint"], "operation": "initialize",
                        "header": checkpoint["header"], "records": checkpoint["records"]}))
            context = await self._io.run(self._context, runtime, run, policies)
        except ExecutionLimitError:
            raise
        except Exception as error:
            raise RunRequestError(RunErrorCode.CAPABILITY_FAILED, str(error)) from error
        events = engine.execute(context)
        paused = False
        outputs = RunOutputState()
        active_steps = set()
        async def handle(batch):
            nonlocal paused
            pending_outputs, pending_deltas, notifications = [], [], []
            for event in batch:
                if paused:
                    raise ValueError("Engine emitted events after pause")
                event, change = outputs.accept(event, active_steps, has_result="output" in run.metadata)
                value = event.delta if event.type == EngineEventType.TEXT_DELTA else event.output
                if event.type == EngineEventType.TEXT_DELTA:
                    pending_outputs.append(value)
                    if change is not None:
                        pending_deltas.append(change)
                elif event.type in (EngineEventType.OUTPUT, EngineEventType.COMPLETION,
                                    EngineEventType.CHECKPOINT, EngineEventType.PAUSED,
                                    EngineEventType.STEP_STARTED, EngineEventType.STEP_UPDATED, EngineEventType.STEP_COMPLETED,
                                    EngineEventType.STEP_FAILED, EngineEventType.STEP_INTERRUPTED,
                                    EngineEventType.STEP_CANCELLED):
                    # 한 이벤트의 쓰기를 한 저장 경계에서 끝낸 뒤 런타임 상태를 진행한다.
                    await self._io.run(self._record_engine_event, run, store, event)
                    if event.type == EngineEventType.STEP_STARTED:
                        active_steps.add(event.step_id)
                    elif event.type in (EngineEventType.STEP_COMPLETED, EngineEventType.STEP_FAILED,
                                        EngineEventType.STEP_INTERRUPTED, EngineEventType.STEP_CANCELLED):
                        active_steps.discard(event.step_id)
                    elif event.type == EngineEventType.PAUSED:
                        paused = True
                else:
                    await self.event_handlers.handle(EventContext(run, context, self._io, self.repository), event)
                notifications.append(event)
            if pending_outputs:
                await drain_on_cancel(self._io.run(self._record_deltas, run, store, pending_outputs, pending_deltas))
            for event in notifications:
                await self.events.publish(run, event)
                await self.subscriptions.publish("engine", run, event, on_error=lambda: self._observer_failed(run))

        try:
            output_policy = self.output_policy.for_project(run.metadata["policies"].get("output", {}))
            await consume_events(events, output_policy, handle)

        finally:
            close = getattr(events, "aclose", None)
            if close is not None:
                await close()
        if paused:
            raise _RunPaused()
        if any(step.status in (StepStatus.PENDING, StepStatus.RUNNING, StepStatus.FAILED)
               for step in await self._io.run(self.steps.list, run)):
            raise RuntimeError("Engine ended with unfinished or failed Steps")

    def _record_engine_event(self, run: Run, store: Conversation, event: EngineEvent) -> None:
        """표준 이벤트 하나의 저장 단위. IO 실행기가 잠금·트랜잭션을 제공한다."""
        if event.type == EngineEventType.COMPLETION:
            self._record_completion(run, event)
        elif event.type == EngineEventType.CHECKPOINT:
            self.repository.record_checkpoint(run, event)
        elif event.type == EngineEventType.PAUSED:
            # 일시정지를 알리기 전에 체크포인트가 실제로 존재해야 한다.
            self.repository.checkpoint(run, event.metadata["checkpoint"])
        else:
            if event.output is not None:
                self.repository.record_output(run, event.output)
            if event.type == EngineEventType.OUTPUT and event.output.step_id is None:
                self._record_result(run, store, event.output)
            else:
                if event.type == EngineEventType.OUTPUT:
                    event = replace(event, type=EngineEventType.STEP_UPDATED)
                self.recorder.record(run, event)

    def _record_deltas(self, run: Run, store: Conversation, outputs, deltas) -> None:
        """출력 저널과 대화 델타를 같은 트랜잭션에 저장한다. 알림은 호출자가 보낸다."""
        if len(outputs) == 1:
            self.repository.record_output(run, outputs[0])
        else:
            self.repository.record_outputs(run, outputs)
        if len(deltas) == 1:
            text, operation = deltas[0]
            if operation == "append":
                store.delta(run.assistant_message_id, text)
            else:
                store.delta(run.assistant_message_id, text, operation=operation)
        elif deltas:
            store.deltas(run.assistant_message_id, deltas)

    def _record_result(self, run: Run, store: Conversation, value: EngineOutput) -> None:
        from llm.services.infrastructure.transactions import watch
        watch(run)
        # 최종 텍스트만 다음 대화에 전달한다. 구조화 Graph 결과를 문자열로 바꾸지 않는다.
        # 같은 본문은 재저장하지 않아 일반적인 단일 응답의 중복 쓰기를 피한다.
        if (value.visibility == "user" and (value.text or value.data is None)
                and store.get(run.assistant_message_id).content != value.text):
            store.delta(run.assistant_message_id, value.text, operation="replace")
        run.metadata["output"] = value.to_dict()
        self.repository.save(run)

    def _record_completion(self, run: Run, event: EngineEvent) -> None:
        if event.completion is None:
            raise ValueError("Completion event requires a result")
        result = asdict(event.completion)
        child(run.paths.state, result["id"])
        entries = run.metadata.setdefault("completions", [])
        for index, previous in enumerate(entries):
            if previous["id"] == result["id"]:
                entries[index] = result
                break
        else:
            policy = run.metadata["policies"].get("usage", {})
            project_entries = []
            auxiliary = []
            project = self.sessions.project_access.load(self._session.project_id)
            if isinstance(self.capabilities, ComponentToolResolver):
                auxiliary = component_usage(self.capabilities.components, project)
            if policy.get("project_max_calls") is not None or policy.get("project_max_tokens") is not None:
                project = self.sessions.project_access.load(self._session.project_id)
                reader = getattr(self.repository, "completion_history", None)
                project_entries = [entry for session in self.sessions.list(project, include_deleted=True)
                                   for entry in (reader(session) if reader else [e for r in self.repository.list(session)
                                       for e in r.metadata.get("completions", [])])]
            check_admission(run, result, event.completion.reserved_tokens, [*project_entries, *auxiliary],
                            [e for e in auxiliary if e.get("run_id") == run.id])
            entries.append(result)
        self.repository.save(run)

    def _finish(self, runtime: SessionRuntime, run: Run, status: RunStatus,
                error: Optional[str] = None, error_code: Optional[str] = None) -> None:
        from llm.services.infrastructure.transactions import watch
        watch(runtime.session)
        for step in self.steps.list(run):
            if step.status in (StepStatus.PENDING, StepStatus.RUNNING):
                if status == RunStatus.FAILED:
                    self.steps.fail(step, error or "Run failed")
                else:
                    self.steps.interrupt(step)
        store = self._store(runtime.session)
        store.set_status(run.assistant_message_id, MessageStatus(status.value))
        run.status = status
        run.error = error
        run.error_code = error_code
        run.ended_at = now()
        self.repository.save(run)
        log_event(run.paths.logs, f"run.{status.value}", entity_id=run.id,
                  status=status)
        runtime.session.current_run_id = None
        runtime.session.status = SessionStatus.IDLE
        self.sessions._save_runtime(runtime.session)

    async def _worker(self, runtime: SessionRuntime) -> None:
        while not runtime.closed:
            while self.pending_work.active and not runtime.closed:
                await self.pending_work.wait()
            if runtime.closed:
                return
            message_id = await runtime.queue.get()
            try:
                if runtime.closed or message_id is None:
                    return
                runtime.preparing = True
                runtime.interrupt_requested = False
                runtime.finished = asyncio.Event()
                message = await self._io.run(self._store(runtime.session).get, message_id)
                if message.status != MessageStatus.QUEUED:
                    continue
                # Set up persistent state before creating the cancellable child.
                # Finalization belongs to the worker, so cancellation before the
                # child's first instruction still leaves a terminal Run.
                if runtime.closed:
                    return
                run = await self._io.run(self._begin, runtime, message)
                if run is None:
                    continue
                await self._publish_run(run)
                runtime.preparing = False
                runtime.execution = asyncio.create_task(self._consume_limited(runtime, run))
                if runtime.closed or runtime.interrupt_requested:
                    runtime.execution.cancel()
                status, error, error_code = RunStatus.COMPLETED, None, None
                try:
                    await runtime.execution
                except _RunPaused:
                    status = RunStatus.PAUSED
                except asyncio.CancelledError:
                    status = RunStatus.INTERRUPTED
                    error_code = RunErrorCode.INTERRUPTED
                except Exception as failure:
                    status, error = RunStatus.FAILED, str(failure)
                    error_code = RunErrorCode.PROVIDER_CAPACITY if isinstance(failure, ProviderCapacityError) else failure.code if isinstance(failure, (RunRequestError, ExecutionLimitError)) else RunErrorCode.ENGINE_FAILED
                finally:
                    try:
                        await self._io.run(self._finish, runtime, run, status, error, error_code)
                        if status == RunStatus.PAUSED:
                            try:
                                # 선택적 자동 응답 실패가 이미 확정한 PAUSED를 되돌리지 않는다.
                                await self._io.run(self.repository.apply_interaction_policy, runtime.session, run,
                                                   run.metadata["policies"].get("approval", {}))
                            except Exception:
                                log_event(run.paths.logs, "interaction.policy_failed", entity_id=run.id,
                                          status="response_write_failed")
                        await self._publish_run(run)
                    finally:
                        runtime.execution = None
                        runtime.finished.set()
            finally:
                runtime.preparing = False
                runtime.finished.set()
                runtime.queue.task_done()

    # 공개 API
    @property
    def on_event(self) -> Optional[Callable[[Run, EngineEvent], None]]:
        return self.events.callback

    @on_event.setter
    def on_event(self, callback: Optional[Callable[[Run, EngineEvent], None]]) -> None:
        self.events.callback = callback

    @property
    def session(self) -> Session:
        return deepcopy(self._session)

    async def start(self) -> None:
        """Session을 복구하고 선택한 대화 저장소에 남아 있는 대기 요청만 예약한다."""
        await drain_on_cancel(self._start())

    async def submit(self, content: str, *,
                     engine: str) -> Message:
        """Queue a request for an explicitly named registered Engine."""
        return await drain_on_cancel(self._submit(content, engine))

    async def resume_plan(self, run_id: str, *, engine: str) -> ResumePlan:
        """큐 등록이나 모델 호출 없이 재사용·승인·재개 차단 사유를 조회한다."""
        return await self._io.run(self._resume_plan, run_id, engine)

    async def resume(self, run_id: str, *, engine: str, retry_nodes=(), decisions=None) -> Message:
        """체크포인트에서 새 Run을 예약한다. 불확실한 노드의 키는 명시적으로 승인한다."""
        if decisions is not None and not isinstance(decisions, dict):
            raise TypeError("Resume decisions must be a JSON object")
        decisions = json.loads(json.dumps(decisions if decisions is not None else {}, allow_nan=False))
        return await drain_on_cancel(self._resume(run_id, engine, tuple(retry_nodes), decisions))

    async def cancel_request(self, message_id: str) -> bool:
        """대기 요청만 취소한다. 실행 중이면 False이며 interrupt로 명시적으로 중단한다."""
        return await drain_on_cancel(self._cancel_request(message_id))

    async def wait_request(self, message_id: str, *, timeout: Optional[float] = None) -> Run:
        """Wait for one stored request; timeout/caller cancellation never stops it.

        Completed history is read without attaching a runtime. Pending requests
        start recovery/scheduling. Shutdown before this request finishes raises
        RuntimeError; queued messages can be waited on again while their store
        survives (file queues are durable; owned memory ends at backend shutdown).
        """
        async def wait():
            run = await self._io.run(self._request_run, message_id)
            if run is not None and run.status not in (RunStatus.PENDING, RunStatus.RUNNING):
                return run
            runtime = await drain_on_cancel(self._start())
            while True:
                if self._request_changed is None:
                    self._request_changed = asyncio.Event()
                changed = self._request_changed
                run = await self._io.run(self._request_run, message_id)
                if run is not None and run.status not in (RunStatus.PENDING, RunStatus.RUNNING):
                    return run
                if runtime.worker.done():
                    runtime.worker.result()
                    raise RuntimeError("Session runtime stopped before this request finished")
                notification = asyncio.create_task(changed.wait())
                try:
                    await asyncio.wait((notification, runtime.worker), return_when=asyncio.FIRST_COMPLETED)
                finally:
                    notification.cancel()
                    await asyncio.gather(notification, return_exceptions=True)
        return await asyncio.wait_for(wait(), timeout=timeout)

    async def interrupt(self) -> bool:
        runtime = self._active
        if runtime is None:
            return False
        if not runtime.preparing and (runtime.execution is None or runtime.execution.done()):
            return False
        finished = runtime.finished
        runtime.interrupt_requested = True
        if runtime.execution is not None:
            runtime.execution.cancel()
        await finished.wait()
        await self._io.run(log_event, runtime.session.paths.logs, "runtime.interrupted", entity_id=runtime.session.id)
        return True

    async def wait_idle(self) -> None:
        runtime = await drain_on_cancel(self._start())
        assert runtime.worker is not None
        joined = asyncio.create_task(runtime.queue.join())
        try:
            done, _ = await asyncio.wait((joined, runtime.worker),
                                         return_when=asyncio.FIRST_COMPLETED)
            if runtime.worker in done:
                runtime.worker.result()
                if not joined.done():
                    raise RuntimeError("Session worker stopped before draining its queue")
            await joined
        finally:
            joined.cancel()
            await asyncio.gather(joined, return_exceptions=True)

    async def shutdown(self) -> None:
        await drain_on_cancel(self._shutdown())
