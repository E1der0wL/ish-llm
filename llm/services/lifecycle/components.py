"""선택된 Component 데이터의 공통 CRUD 핸들. 모든 접근에서 Project의 최신 상태와 잠금을 확인하고 Facade 수명 규칙을 적용한다.

Lifecycle-checked component data access without domain-specific knowledge."""

from llm.providers.observations import ModelObservationContract
from .lifecycle_records import ComponentLogEvent

from llm.core.editable_snapshot import EditableSnapshot
from llm.core.component_reference import ComponentReference

from copy import deepcopy
from contextlib import contextmanager, nullcontext
import asyncio
from typing import Optional

from llm.core.models import Project
from llm.components.registry import ComponentRegistry
from llm.components.base import Component
from llm.services.lifecycle.access import ProjectAccess
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.logging import log_event
from llm.services.infrastructure.storage import StorageIO, async_method, revision_token, check_revision


# 잠금과 수명 검사 뒤에 Component CRUD를 호출하는 공개 핸들.
class ComponentData:
    """A reusable handle; every operation reloads Project state under its lock."""

    UNSPECIFIED = None
    FOREIGN_LOOP = "Use component async methods on their original event loop"
    UNBOUND_AGENTS = "Agent domain is not bound"
    UNBOUND_REFERENCES = "Component domain references are not bound"
    STEP_REQUIRES_RUN = "Step reference requires a Run"
    UNBOUND_STEPS = "Component Step references are not bound"
    UNOBSERVABLE_QUOTA = "Configured quota requires observable component model clients"
    INVALID_RUNTIME = "Component runtime hooks must be callable"

    def __init__(
        self, access: ProjectAccess, registry: ComponentRegistry, project: Project, name: str
    ) -> None:
        self.access, self.registry = access, registry
        self.project, self.name = deepcopy(project), name
        self.ownership = access.repository.ownership
        from llm.services.lifecycle.references import ProjectReferences

        self.references = ProjectReferences(
            registry, access.repository.agents, selected_references=access.agent_references
        )
        self._async_runner = ComponentData.UNSPECIFIED
        self._access_check = ComponentData.UNSPECIFIED
        self._async_storage = StorageIO(self.ownership)
        self._async_loop = ComponentData.UNSPECIFIED
        self.history_reader = ComponentData.UNSPECIFIED
        self._model_usage = ComponentData.UNSPECIFIED
        self._provider_calls = ComponentData.UNSPECIFIED
        self._provider_diagnostics = ComponentData.UNSPECIFIED
        self._observability = ComponentData.UNSPECIFIED
        self._sessions = ComponentData.UNSPECIFIED
        self._steps = ComponentData.UNSPECIFIED
        self._agents = ComponentData.UNSPECIFIED

    async def _async_call(self, operation, *args, **kwargs):
        if self._async_runner is not ComponentData.UNSPECIFIED:
            return await self._async_runner(operation, *args, **kwargs)
        loop = asyncio.get_running_loop()
        if self._async_loop is ComponentData.UNSPECIFIED:
            self._async_loop = loop
        elif self._async_loop is not loop:
            raise RuntimeError(ComponentData.FOREIGN_LOOP)
        return await self._async_storage.run(operation, *args, **kwargs)

    def _current(self):
        if self._access_check is not ComponentData.UNSPECIFIED:
            self._access_check()
        project = self.access.require(self.project)
        self.registry.validate(project.components)
        ComponentRegistry.require_enabled(project, self.name)
        return project, self.registry.get(self.name)

    # 공개 API
    def bind_resources(self, sessions, steps, *, agents=UNSPECIFIED):
        """Component의 참조 조회에도 실행과 같은 저장소를 사용한다. 특정 기능은 해석하지 않는다."""
        self._sessions, self._steps = sessions, steps
        self._agents = agents
        return self

    @workspace_locked
    def agents(self):
        """Reach the independent domain without a Component identity or activation."""
        from llm.services.lifecycle.agents import AgentHandle

        project, _ = self._current()
        if self._agents is ComponentData.UNSPECIFIED:
            raise RuntimeError(ComponentData.UNBOUND_AGENTS)
        handle = AgentHandle(self._agents, project)
        if self._async_runner is not ComponentData.UNSPECIFIED:
            handle.bind_runtime(runner=self._async_runner, access_check=self._access_check)
        return handle

    @workspace_locked
    def reference(self, session_id, run_id=UNSPECIFIED, step_id=UNSPECIFIED):
        """소유 Project 안의 영속 참조를 검증하고 원본 레코드 사본을 반환한다."""
        from llm.services.infrastructure.storage import record

        project, _ = self._current()
        if self._sessions is ComponentData.UNSPECIFIED:
            raise RuntimeError(ComponentData.UNBOUND_REFERENCES)
        session = self._sessions.load(project, session_id)
        result = ComponentReference.for_session(record(session))
        if run_id is not ComponentData.UNSPECIFIED:
            run = self._sessions.run_repository.load(session, run_id)
            result.include_run(record(run))
            if step_id is not ComponentData.UNSPECIFIED:
                if self._steps is ComponentData.UNSPECIFIED:
                    raise RuntimeError(ComponentData.UNBOUND_STEPS)
                result.include_step(record(self._steps.load(run, step_id)))
        elif step_id is not ComponentData.UNSPECIFIED:
            raise ValueError(ComponentData.STEP_REQUIRES_RUN)
        return result.to_dict()

    @workspace_locked
    def related(self, name):
        """같은 Project의 다른 선택 Component에 수명·저장소·비동기 경계를 전달한다."""
        project, _ = self._current()
        ComponentRegistry.require_enabled(project, name)
        component = self.registry.get(name)
        handle = (Component.data_type(component) or ComponentData)(self.access, self.registry, project, name)
        handle.bind_resources(self._sessions, self._steps, agents=self._agents)
        if self._async_runner is not ComponentData.UNSPECIFIED:
            handle.bind_runtime(
                runner=self._async_runner,
                access_check=self._access_check,
                history_reader=self.history_reader,
                provider_calls=self._provider_calls,
                provider_diagnostics=self._provider_diagnostics,
                observability=self._observability,
            )
        if self._model_usage is not ComponentData.UNSPECIFIED:
            handle.bind_model_usage(self._model_usage.sessions, self._model_usage.counters)
        return handle

    areference = async_method(reference)
    arelated = async_method(related)

    def bind_model_usage(self, sessions, counters):
        from llm.services.runtime.usage import ComponentUsage

        self._model_usage = ComponentUsage(self, sessions, counters)
        return self

    @contextmanager
    def model_scope(self):
        from llm.providers.observations import model_observer
        from llm.providers.runtime import logging_scope

        admission = (
            self._provider_calls.scope()
            if self._provider_calls is not ComponentData.UNSPECIFIED
            else nullcontext()
        )
        observations = (
            self._observability.scope()
            if self._observability is not ComponentData.UNSPECIFIED
            else nullcontext()
        )
        with (
            observations,
            admission,
            model_observer(self._model_usage),
            logging_scope(
                self.ownership.path.parent, project_id=self.project.id, diagnostics=self._provider_diagnostics
            ),
        ):
            yield

    @workspace_locked
    def require_model_observation(self, *clients):
        """한도가 켜졌으면 관찰 계약을 지원하지 않는 주입 클라이언트를 호출 전에 거부한다."""
        from llm.services.runtime.usage import current_usage
        from llm.policies import ExecutionLimitError
        from llm.core.policies import UsagePolicy, UsageErrorCode

        project, _ = self._current()
        scope = current_usage()
        policies = [UsagePolicy.from_policies(project.config.policies)]
        if scope is not ComponentData.UNSPECIFIED:
            policies.append(scope.policy)
        limited = any(policy.run.active or policy.project.active for policy in policies)
        if limited and (
            self._model_usage is ComponentData.UNSPECIFIED
            or any(
                c is not ComponentData.UNSPECIFIED and not ModelObservationContract.supported(c)
                for c in clients
            )
        ):
            raise ExecutionLimitError(UsageErrorCode.CONFIGURATION, ComponentData.UNOBSERVABLE_QUOTA)

    @workspace_locked
    def model_usage(self):
        project, component = self._current()
        return component.model_usage(project)

    amodel_usage = async_method(model_usage)

    def bind_runtime(
        self,
        *,
        runner,
        access_check,
        history_reader=UNSPECIFIED,
        provider_calls=UNSPECIFIED,
        observability=UNSPECIFIED,
        provider_diagnostics=UNSPECIFIED,
    ) -> "ComponentData":
        """Facade의 비동기 실행기와 수명 검사를 명시적으로 연결한다."""
        if (
            not callable(runner)
            or not callable(access_check)
            or history_reader is not ComponentData.UNSPECIFIED
            and not callable(history_reader)
        ):
            raise TypeError(ComponentData.INVALID_RUNTIME)
        self._async_runner, self._access_check = runner, access_check
        self.history_reader = history_reader
        self._provider_calls = provider_calls
        self._provider_diagnostics = provider_diagnostics
        self._observability = observability
        return self

    @workspace_locked
    def get_config(self) -> dict:
        project, component = self._current()
        return component.get_config(project)

    @workspace_locked
    def configure(self, data: dict, *, expected_version=UNSPECIFIED) -> None:
        project, component = self._current()
        check_revision(component.get_config(project), expected_version)
        project.config.set_component_parameters(self.name, data)
        self.registry.validate_config(project)
        self.access.repository.save(project)
        log_event(project.paths.logs, ComponentLogEvent.CONFIGURED, entity_id=project.id)

    @workspace_locked
    def resolve_config(self) -> dict:
        from llm.core.configuration import resolve_component_config

        project, component = self._current()
        return resolve_component_config(component, project)

    aresolve_config = async_method(resolve_config)

    @workspace_locked
    def create(self, data: dict, *, identifier: Optional[str] = UNSPECIFIED) -> str:
        project, component = self._current()
        identifier = component.create(project, data, identifier=identifier)
        log_event(project.paths.logs, ComponentLogEvent.CREATED, entity_id=project.id)
        return identifier

    @workspace_locked
    def load(self, identifier: str) -> dict:
        project, component = self._current()
        return component.load(project, identifier)

    @workspace_locked
    def snapshot(self, identifier=UNSPECIFIED) -> dict:
        """UI 편집용 데이터와 충돌 검사용 버전을 함께 조회한다. None이면 Component 설정이다."""
        project, component = self._current()
        data = (
            component.get_config(project)
            if identifier is ComponentData.UNSPECIFIED
            else component.load(project, identifier)
        )
        return EditableSnapshot(data, revision_token(data)).to_dict()

    @workspace_locked
    def list(self) -> dict[str, dict]:
        project, component = self._current()
        return component.list(project)

    @workspace_locked
    def save(self, identifier: str, data: dict, *, expected_version=UNSPECIFIED) -> None:
        project, component = self._current()
        if expected_version is not ComponentData.UNSPECIFIED:
            check_revision(component.load(project, identifier), expected_version)
        component.save(project, identifier, data)
        log_event(project.paths.logs, ComponentLogEvent.SAVED, entity_id=project.id)

    @workspace_locked
    def update(self, identifier: str, changes: dict, *, expected_version=UNSPECIFIED) -> dict:
        project, component = self._current()
        if expected_version is not ComponentData.UNSPECIFIED:
            check_revision(component.load(project, identifier), expected_version)
        data = component.update(project, identifier, changes)
        log_event(project.paths.logs, ComponentLogEvent.UPDATED, entity_id=project.id)
        return data

    @workspace_locked
    def delete(self, identifier: str, *, expected_version=UNSPECIFIED) -> None:
        project, component = self._current()
        self.references.require_unreferenced(project, self.name, identifier)
        if expected_version is not ComponentData.UNSPECIFIED:
            check_revision(component.load(project, identifier), expected_version)
        component.delete(project, identifier)
        log_event(project.paths.logs, ComponentLogEvent.DELETED, entity_id=project.id)

    aget_config = async_method(get_config)

    aconfigure = async_method(configure)

    acreate = async_method(create)

    aload = async_method(load)
    asnapshot = async_method(snapshot)

    alist = async_method(list)

    asave = async_method(save)

    aupdate = async_method(update)

    adelete = async_method(delete)
