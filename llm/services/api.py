"""Project, Session, Request, Run, Step을 탐색하는 공개 Facade. 동기 메서드와 a 접두사 비동기 메서드를 제공하며 변경은 담당 서비스에 위임한다.

Navigable service handles; these objects are never persisted domain models."""

from llm.core.views import SessionRuntimeView, RunView
from llm.core.plans import ResumePlan, RecoveryPlan, RecoveryResult, RetentionPlan

from copy import deepcopy
from typing import TYPE_CHECKING, Optional, Sequence, Union

from llm.core.models import Message, MessageRole, MessageStatus, Project, ProjectConfig, Run, Step, Session
from llm.core.results import ExecutionResult, EngineOutput, EngineDelta
from llm.core.interactions import InteractionRequest, InteractionResponse
from llm.core.models import RunStatus
from llm.core.configuration import UNSET
from llm.core.paths import ProjectPaths, SessionPaths
from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.storage import async_method
from llm.services.query import Query, select

if TYPE_CHECKING:
    from llm.llm import LargeLanguageModel


class AsyncFacade:
    """Share the backend's bounded off-loop transaction lane."""

    async def _async_call(self, operation, *args, **kwargs):
        return await self.app._storage_call(operation, *args, **kwargs)


class Projects(AsyncFacade):
    def __init__(self, app: "LargeLanguageModel") -> None:
        self.app = app

    def create(self, title: str = "Untitled project", *, config: Optional[dict] = None,
               components: Sequence[str] = (),
               conversation_storage: Optional[str] = None) -> "ProjectHandle":
        """대화 저장 방식을 프로젝트에 고정한다. 생략하면 백엔드 기본값을 사용한다."""
        self.app._check_open()
        settings = ProjectConfig(config) if config is not None else None
        return ProjectHandle(self.app, self.app.project_manager.create(
            title, config=settings, components=components, conversation_storage=conversation_storage))

    def recover_deletions(self):
        self.app._check_open()
        return self.app.project_manager.recover_deletions()

    arecover_deletions = async_method(recover_deletions)

    def load(self, project_id: str) -> "ProjectHandle":
        self.app._check_open()
        return ProjectHandle(self.app, self.app.project_manager.load(project_id))

    def list(self, *, include_deleted: bool = False, query: Optional[Query] = None) -> list["ProjectHandle"]:
        self.app._check_open()
        return [ProjectHandle(self.app, model) for model in
                self.app.project_manager.list(include_deleted=include_deleted, query=query)]

    def restore_backup(self, source) -> "ProjectHandle":
        self.app._check_open()
        return ProjectHandle(self.app, self.app.project_manager.restore_backup(source))

    def upgrade_backup(self, source, destination, *, transform):
        self.app._check_open()
        return self.app.project_manager.upgrade_backup(source, destination, transform=transform)

    arestore_backup = async_method(restore_backup)
    aupgrade_backup = async_method(upgrade_backup)

    acreate = async_method(create)
    aload = async_method(load)
    alist = async_method(list)


# Project의 Session, Component와 결과 조회를 연결한다.
class ProjectHandle(AsyncFacade):
    """Bind Project service operations without adding runtime fields to Project."""

    def __init__(self, app: "LargeLanguageModel", model: Project) -> None:
        self.app = app
        self._snapshot = deepcopy(model)
        self.sessions = Sessions(self)
        self.components = Components(self)
        self.results = Results(self)

    def _configuration(self, config=None, *, expected_version=None) -> dict:
        self.app._check_open()
        value = self.app.project_manager.configuration(self._snapshot, config=config,
                                                       expected_version=expected_version)
        value["schema"] = self.app.project_schema(components=list(value["components"]))
        form_config = deepcopy(value["project"]["config"])
        value["values"] = {"title": value["project"]["title"],
                           "conversation_storage": value["project"]["conversation_storage"],
                           "components": list(value["components"]), "config": form_config}
        from llm.services.schema import effective_engines
        value["effective_engines"] = effective_engines(self.app, value["project"]["config"])
        return value

    @property
    def id(self) -> str:
        return self._snapshot.id

    @property
    def data(self) -> Project:
        """Fresh, detached persisted state; edits require save()."""
        self.app._check_open()
        return self.app.project_manager.load(self.id)

    @property
    def paths(self) -> ProjectPaths:
        return self._snapshot.paths

    def save(self, *, title: Optional[str] = None, config: Optional[dict] = None,
             conversation_storage: Optional[str] = None, expected_version=None,
             components: Optional[Sequence[str]] = None, expected_components: Optional[Sequence[str]] = None) -> None:
        """제목/설정/선택을 함께 저장한다. components는 선택적이며 기존 선택을 유지할 수 있다.

        expected_components는 편집 시작 시 선택 목록이다. 설정 버전과 함께 충돌을 검사한다.
        대화 저장 방식은 Session이 없는 프로젝트만 변경할 수 있다.
        """
        current = self.data
        if title is not None:
            current.title = title
        if config is not None:
            current.config = ProjectConfig(config)
        if conversation_storage is not None:
            current.conversation_storage = conversation_storage
        self.app.project_manager.save(current, expected_version=expected_version,
                                      components=components, expected_components=expected_components)

    def configuration(self) -> dict:
        """저장된 명시값·UI 스키마·Engine별 적용값을 반환한다. 기본값을 생성하지 않는다."""
        return self._configuration()

    def validate_configuration(self, config: dict, *, expected_version=None) -> dict:
        """전체 ProjectConfig 후보를 저장 없이 검증한다. 버전은 현재 저장 원본을 가리킨다."""
        return self._configuration(config, expected_version=expected_version)

    avalidate_configuration = async_method(validate_configuration)

    def recovery(self, *, apply=False, expected_version=None) -> Union[RecoveryPlan, RecoveryResult]:
        self.app._check_open()
        return self.app.project_manager.recovery(self._snapshot, apply=apply, expected_version=expected_version)

    arecovery = async_method(recovery)

    def maintenance(self, *, apply=False, expected_version=None):
        self.app._check_open()
        return self.app.project_manager.maintenance(self._snapshot, apply=apply, expected_version=expected_version)

    amaintenance = async_method(maintenance)

    def activity(self, *, limit=None, newest_first=True):
        """최근 lifecycle 참조 조회. limit 생략 시 전체이며 상세 결과는 Run/Step에서 조회한다."""
        self.app._check_open()
        return self.app.project_manager.activity(self._snapshot, limit=limit, newest_first=newest_first)

    aactivity = async_method(activity)

    def model_usage(self):
        self.app._check_open()
        return self.app.project_manager.model_usage(self._snapshot)

    amodel_usage = async_method(model_usage)

    def retention(self, *, apply=False, expected_version=None) -> RetentionPlan:
        """보관 정책에 따른 정리 미리보기. apply=True는 검토한 Session 기록을 영구 삭제한다."""
        self.app._check_open()
        name = self.data.config.policies.get("retention", {}).get("counter")
        counter = getattr(self.app.policy_resolver, "token_counters", {}).get(name)
        return self.app.project_manager.retention(self._snapshot, counter=counter,
                                                  apply=apply, expected_version=expected_version)

    aretention = async_method(retention)

    def recover_retention(self):
        self.app._check_open()
        return self.app.project_manager.recover_retention(self._snapshot)

    arecover_retention = async_method(recover_retention)

    def configure_policies(self, changes: dict, *, expected_version=None) -> dict:
        """프로젝트 정책 일부를 병합한다. 새 Run부터 적용하며 원본 대화는 삭제하지 않는다."""
        self.app._check_open()
        return self.app.project_manager.configure_policies(self._snapshot, changes, expected_version=expected_version)

    def delete(self, *, permanent: bool = False) -> None:
        self.app._check_open()
        self.app.project_manager.delete(self._snapshot, permanent=permanent)

    def restore(self) -> None:
        self.app._check_open()
        self.app.project_manager.restore(self._snapshot)

    def clone(self, *, title: Optional[str] = None) -> "ProjectHandle":
        self.app._check_open()
        return ProjectHandle(self.app, self.app.project_manager.clone(self._snapshot, title=title))

    def backup(self, destination):
        self.app._check_open()
        return self.app.project_manager.backup(self._snapshot, destination)

    abackup = async_method(backup)

    async def aget_data(self) -> Project:
        return await self._async_call(lambda: self.data)

    asave = async_method(save)
    aconfiguration = async_method(configuration)
    aconfigure_policies = async_method(configure_policies)
    adelete = async_method(delete)
    arestore = async_method(restore)
    aclone = async_method(clone)


class Sessions(AsyncFacade):
    def __init__(self, project: ProjectHandle) -> None:
        self.project = project
        self.app = project.app

    def create(self, title: str = "Untitled session", *,
               config: Optional[dict] = None) -> "SessionHandle":
        self.project.app._check_open()
        session = self.project.app.project_manager.sessions.create(
            self.project._snapshot, title, config=config)
        return SessionHandle(self.project, session)

    def load(self, session_id: str) -> "SessionHandle":
        self.project.app._check_open()
        return SessionHandle(self.project, self.project.app.project_manager.sessions.load(
            self.project._snapshot, session_id))

    def list(self, *, include_deleted: bool = False, query: Optional[Query] = None) -> list["SessionHandle"]:
        self.project.app._check_open()
        return [SessionHandle(self.project, session) for session in
                self.project.app.project_manager.sessions.list(
                    self.project._snapshot, include_deleted=include_deleted, query=query)]

    acreate = async_method(create)
    aload = async_method(load)
    alist = async_method(list)


# Session 수정, 대화 조회와 요청 제출을 연결한다.
class SessionHandle(AsyncFacade):
    def __init__(self, project: ProjectHandle, model: Session) -> None:
        self.project = project
        self.app = project.app
        self._snapshot = deepcopy(model)
        self.run = Runs(self)
        self.results = Results(self)

    @property
    def id(self) -> str:
        return self._snapshot.id

    @property
    def data(self) -> Session:
        self.app._check_open()
        return self.app.project_manager.sessions.load(self.project._snapshot, self.id)

    @property
    def paths(self) -> SessionPaths:
        return self._snapshot.paths

    def save(self, *, title: Optional[str] = None, config: Optional[dict] = None,
             metadata: Optional[dict] = None) -> None:
        current = self.data
        if config is None:
            self.app.project_manager.sessions.update_details(current, title=title, metadata=metadata)
            return
        if metadata is not None:
            current.metadata = deepcopy(metadata)
        if title is not None:
            current.title = title
        if config is not None:
            current.config = deepcopy(config)
        self.app.project_manager.sessions.save(current)

    def delete(self, *, permanent: bool = False) -> None:
        self.app._check_open()
        self.app.project_manager.sessions.delete(self._snapshot, permanent=permanent)

    def restore(self) -> None:
        self.app._check_open()
        self.app.project_manager.sessions.restore(self._snapshot)

    def clone(self, *, title: Optional[str] = None, through_message_id: str | None = None) -> "SessionHandle":
        self.app._check_open()
        return SessionHandle(self.project, self.app.project_manager.sessions.clone(
            self._snapshot, self.project._snapshot, title=title, through_message_id=through_message_id))

    def delete_turn(self, request_id: str) -> None:
        """일반 대화에서 턴을 숨긴다. 미완료 재개가 참조하는 턴과 실행 중 Session은 보호한다."""
        self.app._check_open()
        self.app.project_manager.sessions.delete_turn(self._snapshot, request_id)

    adelete_turn = async_method(delete_turn)

    def turns(self) -> list[list[Message]]:
        from llm.services.history.turns import conversation_turns
        return conversation_turns(self.conversation())

    aturns = async_method(turns)

    def configuration(self) -> dict:
        """Session 덮어쓰기까지 적용한 설정 조회. 실행 중 Run의 설정을 변경하지 않는다."""
        from llm.services.schema import effective_engines
        with self.app.project_manager.ownership.scope():
            session, project = self.data, self.project.data
            return {"config": deepcopy(session.config), "effective_engines":
                    effective_engines(self.app, project.config, session_config=session.config)}

    aconfiguration = async_method(configuration)

    def conversation(self, *, query: Optional[Query] = None, include_deleted: bool = False) -> list[Message]:
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            messages = self.app.project_manager.sessions.conversations(self.data).list()
            return select([m for m in messages if include_deleted or not m.metadata.get("conversation_deleted")], query)

    async def aget_data(self) -> Session:
        return await self._async_call(lambda: self.data)

    asave = async_method(save)
    adelete = async_method(delete)
    arestore = async_method(restore)
    aclone = async_method(clone)
    aconversation = async_method(conversation)


class Runs(AsyncFacade):
    """Session-bound execution commands and read-only execution history."""

    def __init__(self, session: SessionHandle) -> None:
        self.session = session
        self.app = session.app

    async def start(self) -> None:
        await self.session.app._manager(self.session._snapshot).start()

    async def submit(self, content: str, *, engine: str, engine_options=UNSET) -> "RequestHandle":
        """요청별 JSON 실행 인자를 저장한다. Graph는 workflow ID를 명시해야 한다."""
        message = await self.session.app._manager(self.session._snapshot).submit(
            content, engine=engine, engine_options=engine_options)
        return RequestHandle(self.session, message.id)

    async def steer(self, run_id: str, content: str, *, targets=None):
        """실행 중 대상에 지시를 저장한다. Graph는 instruction_targets()의 ID 목록이 필수다."""
        return await self.app._manager(self.session._snapshot).steer(run_id, content, targets=targets)

    async def reserve_instruction(self, run_id: str, content: str, *, targets):
        """Run의 예약 경로별 다음 Agent 실행에 한 번만 전달한다."""
        return await self.app._manager(self.session._snapshot).reserve_instruction(run_id, content, targets=targets)

    async def resume_plan(self, run_id: str, *, engine: str) -> ResumePlan:
        return await self.app._manager(self.session._snapshot).resume_plan(run_id, engine=engine)

    async def resume(self, run_id: str, *, engine: str, retry_nodes=(), decisions=None) -> "RequestHandle":
        """이전 Run의 체크포인트를 이어갈 새 요청. 실행 중이던 노드만 명시적으로 재시도한다."""
        message = await self.app._manager(self.session._snapshot).resume(
            run_id, engine=engine, retry_nodes=retry_nodes, decisions=decisions)
        return RequestHandle(self.session, message.id)

    async def wait_idle(self) -> None:
        await self.session.app._manager(self.session._snapshot).wait_idle()

    async def interrupt(self) -> bool:
        return await self.session.app._manager(self.session._snapshot).interrupt()

    async def shutdown(self) -> None:
        """Detach just this Session; a later submit creates a fresh manager."""
        await self.session.app._stop_session(self.session._snapshot)

    def status(self, *, queued_limit: Optional[int] = None) -> SessionRuntimeView:
        """실행을 시작하지 않고 UI 상태를 조회한다. 과거 Run/Step 전체 목록을 읽지 않는다."""
        if queued_limit is not None and (type(queued_limit) is not int or queued_limit < 0):
            raise ValueError("queued_limit must be a nonnegative integer")
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            session = self.session.data
            store = self.app.project_manager.sessions.conversations(session)
            from llm.core.steering import is_queued_request
            requests = [m for m in store.list(query=Query(status=MessageStatus.QUEUED)) if is_queued_request(m)]
            queued = requests if queued_limit is None else requests[:queued_limit]
            active = self.app.run_repository.load(session, session.current_run_id) if session.current_run_id else None
            return SessionRuntimeView(**{"session_id": session.id, "status": session.status,
                    "unfinished_work": getattr(self.app._managers.get((session.project_id, session.id)), "pending_work", None).active if (session.project_id, session.id) in self.app._managers else 0,
                    "active_run_id": active.id if active else None,
                    "engine": active.engine if active else None,
                    "started_at": active.started_at if active else None,
                    "queued_count": len(requests),
                    "queued_request_ids": [message.id for message in queued]})

    def load(self, run_id: str) -> "RunHandle":
        self.session.app._check_open()
        with self.session.app.project_manager.ownership.scope():
            model = self.app.run_repository.load(self.session.data, run_id)
        return RunHandle(self.session, model.id)

    def operation(self, key: str) -> dict:
        """Session 범위 작업 키의 영속 실행/결과 기록을 조회한다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            return self.app.run_repository.tool_operation(self.session.data, key)

    def reconcile_operation(self, key: str, *, result, evidence: str) -> None:
        """Session 런타임을 해제한 뒤 외부에서 확인한 결과를 확정한다. 재실행하지 않는다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            session = self.session.data
            self.app.project_manager.sessions.require_inactive(session)
            self.app.run_repository.reconcile_tool_operation(session, key, result=result, evidence=evidence)
            from llm.services.runtime.operations import record_operation_step
            record_operation_step(self.app.run_repository, self.app.step_manager, session,
                                  self.app.run_repository.tool_operation(session, key))

    async def verify_operation(self, key: str, *, apply=False):
        """호스트 operation_probe로 외부 결과를 조회한다. apply=True만 원장에 반영한다."""
        from llm.services.infrastructure.storage import revision_token
        probe = self.app.services.tool_policy.operation_probe
        if probe is None:
            raise ValueError("Configure ToolPolicy.operation_probe first")
        def snapshot():
            session = self.app.project_manager.sessions.require_inactive(self.session.data)
            value = self.app.run_repository.tool_operation(session, key)
            if value["status"] != "started":
                raise ValueError("Only uncertain operations need verification")
            return value
        value = await self._async_call(snapshot)
        version = revision_token(value)
        observation = await probe(deepcopy(value))
        if not apply:
            return {"operation": value, "observation": observation, "version": version}
        def commit():
            session = self.app.project_manager.sessions.require_inactive(self.session.data)
            value = self.app.run_repository.verify_tool_operation(session, key, observation, version)
            from llm.services.runtime.operations import record_operation_step
            record_operation_step(self.app.run_repository, self.app.step_manager, session, value)
            return value
        return await self._async_call(commit)

    def list(self, *, query: Optional[Query] = None) -> list["RunHandle"]:
        from llm.services.runtime.runs import RunRepository
        self.session.app._check_open()
        with self.session.app.project_manager.ownership.scope():
            repository = self.app.run_repository
            if type(repository).list is RunRepository.list:
                models = repository.list(self.session.data, query=query)
            else:
                models = select(repository.list(self.session.data), query)
        return [RunHandle(self.session, model.id) for model in models]

    def request(self, message_id: str) -> "RequestHandle":
        """선택한 저장소의 사용자 Message ID로 요청을 연다. 파일 모드는 재시작 후에도 가능하다."""
        handle = RequestHandle(self.session, message_id)
        handle.data  # Validate identity and parent ownership now.
        return handle

    aload = async_method(load)
    alist = async_method(list)
    arequest = async_method(request)
    astatus = async_method(status)
    aoperation = async_method(operation)
    areconcile_operation = async_method(reconcile_operation)


# Run 데이터/응답/결과/Step을 조회하는 서비스 핸들.
class RunHandle(AsyncFacade):
    def __init__(self, session: SessionHandle, run_id: str) -> None:
        self.session = session
        self.app = session.app
        self.id = run_id
        self.steps = Steps(self)

    def _change_interaction(self, request, operation, *, expires_at=None):
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            session, run = self.session.data, self.data
            store = self.app.project_manager.sessions.conversations(session)
            if run.status not in (RunStatus.PAUSED, RunStatus.FAILED, RunStatus.INTERRUPTED) or any(
                    self.app.run_repository.resume_link(session, run, store)):
                raise ValueError("Interaction cannot change after execution admission")
            result = self.app.run_repository.change_interaction(session, run, request, operation=operation, expires_at=expires_at)
            self.app._interaction_changed(run, self.app.run_repository.interaction_views(session, run, store))
            return result

    @property
    def data(self) -> Run:
        self.session.app._check_open()
        with self.session.app.project_manager.ownership.scope():
            return self.app.run_repository.load(self.session.data, self.id)

    @property
    def engine(self) -> str:
        """Persisted strategy name, including engines no longer registered."""
        return self.data.engine

    @property
    def result(self) -> ExecutionResult:
        return ExecutionResult.from_run(self.session.project.id, self.data)

    @property
    def response(self) -> Message:
        """선택한 저장소의 Assistant 응답. 메모리 수명 종료 등으로 없으면 KeyError다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            session = self.session.data
            run = self.app.run_repository.load(session, self.id)
            message = self.app.project_manager.sessions.conversations(session).get(run.assistant_message_id)
            if message.run_id != self.id or message.role != MessageRole.ASSISTANT:
                raise ValueError("Assistant message and Run ownership mismatch")
            return message

    async def aget_data(self) -> Run:
        return await self._async_call(lambda: self.data)

    async def aresult(self) -> ExecutionResult:
        return await self._async_call(lambda: self.result)

    async def aresponse(self) -> Message:
        return await self._async_call(lambda: self.response)

    def checkpoint(self, name: str = "graph") -> dict:
        """UI용 JSON 스냅샷: 정의/입력과 노드별 completed/started/waiting 상태를 반환한다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            return self.app.run_repository.resolve_checkpoint(self.session.data, self.data, name)

    acheckpoint = async_method(checkpoint)

    def instructions(self):
        """접수한 추가 지시와 명시적 재개에서 참조한 지시의 저장된 상태를 조회한다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            session, run = self.session.data, self.data
            store = self.app.project_manager.sessions.conversations(session)
            return self.app.run_repository.instructions(session, run, store)

    ainstructions = async_method(instructions)

    def instruction_targets(self):
        """저장된 실행 대상 스냅샷. 조회 후 종료될 수 있으므로 접수 시 다시 검증한다."""
        from llm.services.runtime.steering import instruction_targets
        return instruction_targets(self.data)

    ainstruction_targets = async_method(instruction_targets)

    def instruction_routes(self):
        """Graph 실행 스냅샷의 예약 가능한 경로 목록. 정의 편집을 반영하지 않는다."""
        from llm.services.runtime.steering import instruction_routes
        return instruction_routes(self.data)

    ainstruction_routes = async_method(instruction_routes)

    def interactions(self, *, pending_only: bool = False) -> list[InteractionRequest]:
        """Run의 공통 사용자 요청. 응답/만료 상태는 별도 조회하며 요청 원본은 변경하지 않는다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            session, run = self.session.data, self.data
            requests = self.app.run_repository.interaction_requests(session, run)
            if pending_only:
                store = self.app.project_manager.sessions.conversations(session)
                requests = [v.request for v in self.app.run_repository.interaction_views(session, run, store) if v.can_respond]
            return requests

    def interaction_responses(self) -> list[InteractionResponse]:
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            return self.app.run_repository.interaction_responses(self.session.data, self.data)

    def respond(self, response: InteractionResponse) -> InteractionResponse:
        """응답을 원자적으로 저장한다. 실행은 session.run.resume(run.id, engine=...)로 명시한다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            session, run = self.session.data, self.data
            if run.status not in (RunStatus.PAUSED, RunStatus.FAILED, RunStatus.INTERRUPTED):
                raise ValueError("Interaction responses require an inactive Run")
            store = self.app.project_manager.sessions.conversations(session)
            if any(self.app.run_repository.resume_link(session, run, store)):
                raise ValueError("This Run already has a resume request")
            if not isinstance(response, InteractionResponse) or response.actor != "user":
                raise ValueError("Public responses must identify a user choice")
            result = self.app.run_repository.respond(session, run, response)
            self.app._interaction_changed(run, self.app.run_repository.interaction_views(session, run, store))
            return result

    def interaction_views(self):
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            session, run = self.session.data, self.data
            return self.app.run_repository.interaction_views(session, run, self.app.project_manager.sessions.conversations(session))

    def cancel_interaction(self, request: InteractionRequest):
        return self._change_interaction(request, "cancel")

    def renew_interaction(self, request: InteractionRequest, *, expires_at=None):
        return self._change_interaction(request, "renew", expires_at=expires_at)

    ainteraction_views = async_method(interaction_views)
    acancel_interaction = async_method(cancel_interaction)
    arenew_interaction = async_method(renew_interaction)
    ainteractions = async_method(interactions)
    ainteraction_responses = async_method(interaction_responses)
    arespond = async_method(respond)

    def outputs(self) -> list[EngineOutput]:
        """Run 및 자식 Step의 부분/확정 EngineOutput 목록. UI 카드의 초기 상태로 사용한다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            return self.app.run_repository.outputs(self.data)

    def reconcile_output(self) -> bool:
        """비활성 Session의 저장된 출력으로 대화 본문을 복구한다. 유실된 메모리 대화를 생성하지 않는다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            session = self.app.project_manager.sessions.require_inactive(self.session.data)
            run = self.app.run_repository.load(session, self.id)
            return self.app.run_repository.reconcile_output(run, self.app.project_manager.sessions.conversations(session))

    areconcile_output = async_method(reconcile_output)

    def view(self, *, after=0, limit=None) -> RunView:
        """UI 재접속/알림 유실 복구용 동일 잠금 스냅샷. cursor까지의 전체 출력과 이후 이벤트를 제공한다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            run = self.data
            outputs = self.app.run_repository.outputs(run)
            return RunView(**{"run": run, "outputs": outputs, "cursor": max((v.sequence for v in outputs), default=0),
                    "events": self.app.run_repository.output_events(run, after=after, limit=limit)})

    aview = async_method(view)

    def output_events(self, *, after: int = 0, limit: Optional[int] = None) -> list[Union[EngineDelta, EngineOutput]]:
        """마지막 sequence 이후 EngineDelta/EngineOutput을 저장 순서대로 조회한다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            return self.app.run_repository.output_events(self.data, after=after, limit=limit)

    aoutputs = async_method(outputs)
    aoutput_events = async_method(output_events)


# 선택한 저장소의 사용자 요청을 가리킨다. 대기 중에는 아직 Run이 없을 수 있다.
class RequestHandle(AsyncFacade):
    """저장된 사용자 요청. 파일은 영속적이고 메모리는 해당 저장소 수명 동안 유효하다."""

    def __init__(self, session: SessionHandle, message_id: str) -> None:
        self.session, self.app, self.id = session, session.app, message_id

    def _load_run(self) -> Optional[Run]:
        """한 트랜잭션에서 요청과 Run 연결을 검증한다. 조회 간 캐시는 두지 않는다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            session = self.session.data
            message = self.app.project_manager.sessions.conversations(session).get(self.id)
            from llm.core.steering import is_instruction
            if message.role != MessageRole.USER or is_instruction(message):
                raise ValueError("Request ID must identify a user message")
            if message.run_id is None:
                return None
            run = self.app.run_repository.load(session, message.run_id)
            if run.input_message_id != self.id:
                raise ValueError("Request and Run ownership mismatch")
            return run

    # 공개 API
    @property
    def data(self) -> Message:
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            message = self.app.project_manager.sessions.conversations(self.session.data).get(self.id)
            from llm.core.steering import is_instruction
            if message.role != MessageRole.USER or is_instruction(message):
                raise ValueError("Request ID must identify a user message")
            return message

    @property
    def run(self) -> Optional[RunHandle]:
        run = self._load_run()
        return RunHandle(self.session, run.id) if run is not None else None

    @property
    def result(self) -> Optional[ExecutionResult]:
        run = self._load_run()
        return ExecutionResult.from_run(self.session.project.id, run) if run is not None else None

    async def wait(self, *, timeout: Optional[float] = None) -> RunHandle:
        """Return this request's terminal Run, including failed/interrupted runs."""
        manager = self.app._manager(self.session._snapshot)
        run = await manager.wait_request(self.id, timeout=timeout)
        return RunHandle(self.session, run.id)

    async def cancel(self) -> bool:
        """실행 전 대기 요청만 취소한다. 실행 중인 Run은 중단하지 않는다."""
        return await self.app._manager(self.session._snapshot).cancel_request(self.id)

    async def aget_data(self) -> Message:
        return await self._async_call(lambda: self.data)

    async def aget_run(self) -> Optional[RunHandle]:
        return await self._async_call(lambda: self.run)

    async def aresult(self) -> Optional[ExecutionResult]:
        return await self._async_call(lambda: self.result)


class Results(AsyncFacade):
    """Project/Session views over Run-owned observations; no duplicate result files."""

    def __init__(self, owner) -> None:
        self.owner, self.app = owner, owner.app

    def list(self, *, include_deleted: bool = False,
             include_running: bool = False, query: Optional[Query] = None) -> list[ExecutionResult]:
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            return self.app.project_manager.results.list(self.owner.data,
                include_deleted=include_deleted, include_running=include_running, query=query)

    def load(self, run_id: str) -> ExecutionResult:
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            return self.app.project_manager.results.load(self.owner.data, run_id)

    alist = async_method(list)
    aload = async_method(load)


class Steps(AsyncFacade):
    """History access only: Engines and StepEventRecorder own Step transitions."""

    def __init__(self, run: RunHandle) -> None:
        self.run = run
        self.app = run.session.app

    def list(self, *, query: Optional[Query] = None) -> list[Step]:
        self.run.session.app._check_open()
        with self.run.session.app.project_manager.ownership.scope():
            return self.app.step_manager.list(self.run.data, query=query)

    def load(self, step_id: str) -> Step:
        self.run.session.app._check_open()
        with self.run.session.app.project_manager.ownership.scope():
            return self.app.step_manager.load(self.run.data, step_id)

    alist = async_method(list)
    aload = async_method(load)


class Components(AsyncFacade):
    """Named access to selected component data, not raw component implementations."""

    def __init__(self, project: ProjectHandle) -> None:
        self.project = project
        self.app = project.app

    def __getitem__(self, name: str) -> ComponentData:
        self.project.app._check_open()
        handle = self.project.app.project_manager.component(self.project._snapshot, name)
        return handle.bind_runtime(runner=self.app._storage_call, access_check=self.app._check_open,
                                   provider_calls=self.app.provider_calls, observability=self.app._observability)

    def __getattr__(self, name: str) -> ComponentData:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def select(self, names: Sequence[str]) -> None:
        self.project.app._check_open()
        self.project.app.project_manager.set_components(self.project._snapshot, names)

    def remove(self, name: str, *, permanent: bool = False) -> None:
        self.project.app._check_open()
        self.project.app.project_manager.remove_component(
            self.project._snapshot, name, permanent=permanent)

    async def aget(self, name: str) -> ComponentData:
        return await self._async_call(self.__getitem__, name)

    aselect = async_method(select)
    aremove = async_method(remove)
