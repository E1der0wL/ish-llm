"""Project, Task, Request, Run, Step을 탐색하는 공개 Facade. 동기 메서드와 a 접두사 비동기 메서드를 제공하며 변경은 담당 서비스에 위임한다.

Navigable service handles; these objects are never persisted domain models."""

from llm.core.views import TaskRuntimeView, RunView
from llm.core.plans import ResumePlan, RecoveryPlan, RecoveryResult, RetentionPlan

from copy import deepcopy
from typing import TYPE_CHECKING, Optional, Sequence, Union

from llm.core.models import Message, MessageRole, MessageStatus, Project, ProjectConfig, Run, Step, Task
from llm.core.results import ExecutionResult, EngineOutput, EngineDelta
from llm.core.interactions import InteractionRequest, InteractionResponse
from llm.core.models import RunStatus
from llm.core.paths import ProjectPaths, TaskPaths
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

    def get_default(self, *, title: str = "Default project", config: Optional[dict] = None) -> "ProjectHandle":
        """기본 프로젝트를 명시적으로 생성/조회한다. loop 등록은 필요하며 기존 설정은 덮어쓰지 않는다."""
        self.app._check_open()
        if "loop" not in self.app.engines.names():
            raise ValueError("Register the 'loop' engine before requesting the default Project")
        settings = ProjectConfig(config) if config is not None else None
        return ProjectHandle(self.app, self.app.project_manager.get_default(title=title, config=settings))

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
    aget_default = async_method(get_default)
    aload = async_method(load)
    alist = async_method(list)


# Project의 Task, Component와 결과 조회를 연결한다.
class ProjectHandle(AsyncFacade):
    """Bind Project service operations without adding runtime fields to Project."""

    def __init__(self, app: "LargeLanguageModel", model: Project) -> None:
        self.app = app
        self._snapshot = deepcopy(model)
        self.tasks = Tasks(self)
        self.components = Components(self)
        self.results = Results(self)

    def _configuration(self, config=None, *, expected_version=None) -> dict:
        self.app._check_open()
        value = self.app.project_manager.configuration(self._snapshot, config=config,
                                                       expected_version=expected_version)
        value["schema"] = self.app.project_schema(components=list(value["components"]))
        form_config = deepcopy(value["project"]["config"])
        form_config["component_configurations"] = value.pop("component_configurations")
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
             conversation_storage: Optional[str] = None, expected_version=None) -> None:
        """제목/설정을 저장한다. 대화 저장 방식은 Task가 없는 프로젝트만 변경할 수 있다."""
        current = self.data
        if title is not None:
            current.title = title
        if config is not None:
            current.config = ProjectConfig(config)
        if conversation_storage is not None:
            current.conversation_storage = conversation_storage
        self.app.project_manager.save(current, expected_version=expected_version)

    def configuration(self) -> dict:
        """저장 원본과 기본값을 병합한 UI 폼 값을 함께 반환한다."""
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

    def model_usage(self):
        self.app._check_open()
        return self.app.project_manager.model_usage(self._snapshot)

    amodel_usage = async_method(model_usage)

    def retention(self, *, apply=False, expected_version=None) -> RetentionPlan:
        """보관 정책에 따른 정리 미리보기. apply=True는 검토한 Task 기록을 영구 삭제한다."""
        self.app._check_open()
        name = self.data.config.policies["retention"]["counter"]
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


class Tasks(AsyncFacade):
    def __init__(self, project: ProjectHandle) -> None:
        self.project = project
        self.app = project.app

    def create(self, title: str = "Untitled task", *,
               config: Optional[dict] = None) -> "TaskHandle":
        self.project.app._check_open()
        task = self.project.app.project_manager.tasks.create(
            self.project._snapshot, title, config=config)
        return TaskHandle(self.project, task)

    def load(self, task_id: str) -> "TaskHandle":
        self.project.app._check_open()
        return TaskHandle(self.project, self.project.app.project_manager.tasks.load(
            self.project._snapshot, task_id))

    def list(self, *, include_deleted: bool = False, query: Optional[Query] = None) -> list["TaskHandle"]:
        self.project.app._check_open()
        return [TaskHandle(self.project, task) for task in
                self.project.app.project_manager.tasks.list(
                    self.project._snapshot, include_deleted=include_deleted, query=query)]

    acreate = async_method(create)
    aload = async_method(load)
    alist = async_method(list)


# Task 수정, 대화 조회와 요청 제출을 연결한다.
class TaskHandle(AsyncFacade):
    def __init__(self, project: ProjectHandle, model: Task) -> None:
        self.project = project
        self.app = project.app
        self._snapshot = deepcopy(model)
        self.run = Runs(self)
        self.results = Results(self)

    @property
    def id(self) -> str:
        return self._snapshot.id

    @property
    def data(self) -> Task:
        self.app._check_open()
        return self.app.project_manager.tasks.load(self.project._snapshot, self.id)

    @property
    def paths(self) -> TaskPaths:
        return self._snapshot.paths

    def save(self, *, title: Optional[str] = None, config: Optional[dict] = None,
             metadata: Optional[dict] = None) -> None:
        current = self.data
        if config is None:
            self.app.project_manager.tasks.update_details(current, title=title, metadata=metadata)
            return
        if metadata is not None:
            current.metadata = deepcopy(metadata)
        if title is not None:
            current.title = title
        if config is not None:
            current.config = deepcopy(config)
        self.app.project_manager.tasks.save(current)

    def delete(self, *, permanent: bool = False) -> None:
        self.app._check_open()
        self.app.project_manager.tasks.delete(self._snapshot, permanent=permanent)

    def restore(self) -> None:
        self.app._check_open()
        self.app.project_manager.tasks.restore(self._snapshot)

    def clone(self, *, title: Optional[str] = None) -> "TaskHandle":
        self.app._check_open()
        return TaskHandle(self.project, self.app.project_manager.tasks.clone(
            self._snapshot, self.project._snapshot, title=title))

    def configuration(self) -> dict:
        """Task 덮어쓰기까지 적용한 설정 조회. 실행 중 Run의 설정을 변경하지 않는다."""
        from llm.services.schema import effective_engines
        with self.app.project_manager.ownership.scope():
            task, project = self.data, self.project.data
            return {"config": deepcopy(task.config), "effective_engines":
                    effective_engines(self.app, project.config, task_config=task.config)}

    aconfiguration = async_method(configuration)

    def conversation(self, *, query: Optional[Query] = None) -> list[Message]:
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            return select(self.app.project_manager.tasks.conversations(self.data).list(), query)

    async def aget_data(self) -> Task:
        return await self._async_call(lambda: self.data)

    asave = async_method(save)
    adelete = async_method(delete)
    arestore = async_method(restore)
    aclone = async_method(clone)
    aconversation = async_method(conversation)


class Runs(AsyncFacade):
    """Task-bound execution commands and read-only execution history."""

    def __init__(self, task: TaskHandle) -> None:
        self.task = task
        self.app = task.app

    async def start(self) -> None:
        await self.task.app._manager(self.task._snapshot).start()

    async def submit(self, content: str, *, engine: str) -> "RequestHandle":
        message = await self.task.app._manager(self.task._snapshot).submit(content, engine=engine)
        return RequestHandle(self.task, message.id)

    async def resume_plan(self, run_id: str, *, engine: str) -> ResumePlan:
        return await self.app._manager(self.task._snapshot).resume_plan(run_id, engine=engine)

    async def resume(self, run_id: str, *, engine: str, retry_nodes=(), decisions=None) -> "RequestHandle":
        """이전 Run의 체크포인트를 이어갈 새 요청. 실행 중이던 노드만 명시적으로 재시도한다."""
        message = await self.app._manager(self.task._snapshot).resume(
            run_id, engine=engine, retry_nodes=retry_nodes, decisions=decisions)
        return RequestHandle(self.task, message.id)

    async def wait_idle(self) -> None:
        await self.task.app._manager(self.task._snapshot).wait_idle()

    async def interrupt(self) -> bool:
        return await self.task.app._manager(self.task._snapshot).interrupt()

    async def shutdown(self) -> None:
        """Detach just this Task; a later submit creates a fresh manager."""
        await self.task.app._stop_task(self.task._snapshot)

    def status(self, *, queued_limit: int = 20) -> TaskRuntimeView:
        """실행을 시작하지 않고 UI 상태를 조회한다. 과거 Run/Step 전체 목록을 읽지 않는다."""
        if type(queued_limit) is not int or queued_limit < 0:
            raise ValueError("queued_limit must be a nonnegative integer")
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            task = self.task.data
            store = self.app.project_manager.tasks.conversations(task)
            queued = store.list(query=Query(status=MessageStatus.QUEUED, limit=queued_limit))
            active = self.app.run_repository.load(task, task.current_run_id) if task.current_run_id else None
            return TaskRuntimeView(**{"task_id": task.id, "status": task.status,
                    "unfinished_work": getattr(self.app._managers.get((task.project_id, task.id)), "pending_work", None).active if (task.project_id, task.id) in self.app._managers else 0,
                    "active_run_id": active.id if active else None,
                    "engine": active.engine if active else None,
                    "started_at": active.started_at if active else None,
                    "queued_count": store.count(status=MessageStatus.QUEUED, role=MessageRole.USER),
                    "queued_request_ids": [message.id for message in queued]})

    def load(self, run_id: str) -> "RunHandle":
        self.task.app._check_open()
        with self.task.app.project_manager.ownership.scope():
            model = self.app.run_repository.load(self.task.data, run_id)
        return RunHandle(self.task, model.id)

    def operation(self, key: str) -> dict:
        """Task 범위 작업 키의 영속 실행/결과 기록을 조회한다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            return self.app.run_repository.tool_operation(self.task.data, key)

    def reconcile_operation(self, key: str, *, result, evidence: str) -> None:
        """Task 런타임을 해제한 뒤 외부에서 확인한 결과를 확정한다. 재실행하지 않는다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            task = self.task.data
            self.app.project_manager.tasks.require_inactive(task)
            self.app.run_repository.reconcile_tool_operation(task, key, result=result, evidence=evidence)

    async def verify_operation(self, key: str, *, apply=False):
        """호스트 operation_probe로 외부 결과를 조회한다. apply=True만 원장에 반영한다."""
        from llm.services.infrastructure.storage import revision_token
        probe = self.app.services.tool_policy.operation_probe
        if probe is None:
            raise ValueError("Configure ToolPolicy.operation_probe first")
        def snapshot():
            task = self.app.project_manager.tasks.require_inactive(self.task.data)
            value = self.app.run_repository.tool_operation(task, key)
            if value["status"] != "started":
                raise ValueError("Only uncertain operations need verification")
            return value
        value = await self._async_call(snapshot)
        version = revision_token(value)
        observation = await probe(deepcopy(value))
        if not apply:
            return {"operation": value, "observation": observation, "version": version}
        def commit():
            task = self.app.project_manager.tasks.require_inactive(self.task.data)
            return self.app.run_repository.verify_tool_operation(task, key, observation, version)
        return await self._async_call(commit)

    def list(self, *, query: Optional[Query] = None) -> list["RunHandle"]:
        from llm.services.runtime.runs import RunRepository
        self.task.app._check_open()
        with self.task.app.project_manager.ownership.scope():
            repository = self.app.run_repository
            if type(repository).list is RunRepository.list:
                models = repository.list(self.task.data, query=query)
            else:
                models = select(repository.list(self.task.data), query)
        return [RunHandle(self.task, model.id) for model in models]

    def request(self, message_id: str) -> "RequestHandle":
        """선택한 저장소의 사용자 Message ID로 요청을 연다. 파일 모드는 재시작 후에도 가능하다."""
        handle = RequestHandle(self.task, message_id)
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
    def __init__(self, task: TaskHandle, run_id: str) -> None:
        self.task = task
        self.app = task.app
        self.id = run_id
        self.steps = Steps(self)

    def _change_interaction(self, request, operation, *, expires_at=None):
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            task, run = self.task.data, self.data
            store = self.app.project_manager.tasks.conversations(task)
            if run.status not in (RunStatus.PAUSED, RunStatus.FAILED, RunStatus.INTERRUPTED) or any(
                    self.app.run_repository.resume_link(task, run, store)):
                raise ValueError("Interaction cannot change after execution admission")
            result = self.app.run_repository.change_interaction(task, run, request, operation=operation, expires_at=expires_at)
            self.app._interaction_changed(run, self.app.run_repository.interaction_views(task, run, store))
            return result

    @property
    def data(self) -> Run:
        self.task.app._check_open()
        with self.task.app.project_manager.ownership.scope():
            return self.app.run_repository.load(self.task.data, self.id)

    @property
    def engine(self) -> str:
        """Persisted strategy name, including engines no longer registered."""
        return self.data.engine

    @property
    def result(self) -> ExecutionResult:
        return ExecutionResult.from_run(self.task.project.id, self.data)

    @property
    def response(self) -> Message:
        """선택한 저장소의 Assistant 응답. 메모리 수명 종료 등으로 없으면 KeyError다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            task = self.task.data
            run = self.app.run_repository.load(task, self.id)
            message = self.app.project_manager.tasks.conversations(task).get(run.assistant_message_id)
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
            return self.app.run_repository.resolve_checkpoint(self.task.data, self.data, name)

    acheckpoint = async_method(checkpoint)

    def interactions(self, *, pending_only: bool = False) -> list[InteractionRequest]:
        """Run의 공통 사용자 요청. 응답/만료 상태는 별도 조회하며 요청 원본은 변경하지 않는다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            task, run = self.task.data, self.data
            requests = self.app.run_repository.interaction_requests(task, run)
            if pending_only:
                store = self.app.project_manager.tasks.conversations(task)
                requests = [v.request for v in self.app.run_repository.interaction_views(task, run, store) if v.can_respond]
            return requests

    def interaction_responses(self) -> list[InteractionResponse]:
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            return self.app.run_repository.interaction_responses(self.task.data, self.data)

    def respond(self, response: InteractionResponse) -> InteractionResponse:
        """응답을 원자적으로 저장한다. 실행은 task.run.resume(run.id, engine=...)로 명시한다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            task, run = self.task.data, self.data
            if run.status not in (RunStatus.PAUSED, RunStatus.FAILED, RunStatus.INTERRUPTED):
                raise ValueError("Interaction responses require an inactive Run")
            store = self.app.project_manager.tasks.conversations(task)
            if any(self.app.run_repository.resume_link(task, run, store)):
                raise ValueError("This Run already has a resume request")
            if not isinstance(response, InteractionResponse) or response.actor != "user":
                raise ValueError("Public responses must identify a user choice")
            result = self.app.run_repository.respond(task, run, response)
            self.app._interaction_changed(run, self.app.run_repository.interaction_views(task, run, store))
            return result

    def interaction_views(self):
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            task, run = self.task.data, self.data
            return self.app.run_repository.interaction_views(task, run, self.app.project_manager.tasks.conversations(task))

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
        """비활성 Task의 저장된 출력으로 대화 본문을 복구한다. 유실된 메모리 대화를 생성하지 않는다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            task = self.app.project_manager.tasks.require_inactive(self.task.data)
            run = self.app.run_repository.load(task, self.id)
            return self.app.run_repository.reconcile_output(run, self.app.project_manager.tasks.conversations(task))

    areconcile_output = async_method(reconcile_output)

    def view(self, *, after=0, limit=200) -> RunView:
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

    def __init__(self, task: TaskHandle, message_id: str) -> None:
        self.task, self.app, self.id = task, task.app, message_id

    def _load_run(self) -> Optional[Run]:
        """한 트랜잭션에서 요청과 Run 연결을 검증한다. 조회 간 캐시는 두지 않는다."""
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            task = self.task.data
            message = self.app.project_manager.tasks.conversations(task).get(self.id)
            if message.role != MessageRole.USER:
                raise ValueError("Request ID must identify a user message")
            if message.run_id is None:
                return None
            run = self.app.run_repository.load(task, message.run_id)
            if run.input_message_id != self.id:
                raise ValueError("Request and Run ownership mismatch")
            return run

    # 공개 API
    @property
    def data(self) -> Message:
        self.app._check_open()
        with self.app.project_manager.ownership.scope():
            message = self.app.project_manager.tasks.conversations(self.task.data).get(self.id)
            if message.role != MessageRole.USER:
                raise ValueError("Request ID must identify a user message")
            return message

    @property
    def run(self) -> Optional[RunHandle]:
        run = self._load_run()
        return RunHandle(self.task, run.id) if run is not None else None

    @property
    def result(self) -> Optional[ExecutionResult]:
        run = self._load_run()
        return ExecutionResult.from_run(self.task.project.id, run) if run is not None else None

    async def wait(self, *, timeout: Optional[float] = None) -> RunHandle:
        """Return this request's terminal Run, including failed/interrupted runs."""
        manager = self.app._manager(self.task._snapshot)
        run = await manager.wait_request(self.id, timeout=timeout)
        return RunHandle(self.task, run.id)

    async def cancel(self) -> bool:
        """실행 전 대기 요청만 취소한다. 실행 중인 Run은 중단하지 않는다."""
        return await self.app._manager(self.task._snapshot).cancel_request(self.id)

    async def aget_data(self) -> Message:
        return await self._async_call(lambda: self.data)

    async def aget_run(self) -> Optional[RunHandle]:
        return await self._async_call(lambda: self.run)

    async def aresult(self) -> Optional[ExecutionResult]:
        return await self._async_call(lambda: self.result)


class Results(AsyncFacade):
    """Project/Task views over Run-owned observations; no duplicate result files."""

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
        self.app = run.task.app

    def list(self, *, query: Optional[Query] = None) -> list[Step]:
        self.run.task.app._check_open()
        with self.run.task.app.project_manager.ownership.scope():
            return self.app.step_manager.list(self.run.data, query=query)

    def load(self, step_id: str) -> Step:
        self.run.task.app._check_open()
        with self.run.task.app.project_manager.ownership.scope():
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
        return handle.bind_runtime(runner=self.app._storage_call, access_check=self.app._check_open)

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
