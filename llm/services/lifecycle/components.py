"""선택된 Component 데이터의 공통 CRUD 핸들. 모든 접근에서 Project의 최신 상태와 잠금을 확인하고 Facade 수명 규칙을 적용한다.

Lifecycle-checked component data access without domain-specific knowledge."""

from copy import deepcopy
from contextlib import contextmanager, nullcontext
import asyncio
from typing import Optional

from llm.core.models import Project
from llm.components.registry import ComponentRegistry
from llm.services.lifecycle.access import ProjectAccess
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.logging import log_event
from llm.services.infrastructure.storage import StorageIO, async_method, revision_token, check_revision


# 잠금과 수명 검사 뒤에 Component CRUD를 호출하는 공개 핸들.
class ComponentData:
    """A reusable handle; every operation reloads Project state under its lock."""

    def __init__(self, access: ProjectAccess, registry: ComponentRegistry,
                 project: Project, name: str) -> None:
        self.access, self.registry = access, registry
        self.project, self.name = deepcopy(project), name
        self.ownership = access.repository.ownership
        self._async_runner = None
        self._access_check = None
        self._async_storage = StorageIO(self.ownership)
        self._async_loop = None
        self.history_reader = None
        self._model_usage = None
        self._provider_calls = None
        self._observability = None

    async def _async_call(self, operation, *args, **kwargs):
        if self._async_runner is not None:
            return await self._async_runner(operation, *args, **kwargs)
        loop = asyncio.get_running_loop()
        if self._async_loop is None:
            self._async_loop = loop
        elif self._async_loop is not loop:
            raise RuntimeError("Use component async methods on their original event loop")
        return await self._async_storage.run(operation, *args, **kwargs)

    def _current(self):
        if self._access_check is not None:
            self._access_check()
        project = self.access.require(self.project)
        self.registry.validate(project.components)
        if self.name not in project.components:
            raise ValueError("Component is not enabled for this Project")
        return project, self.registry.get(self.name)

    # 공개 API
    def bind_model_usage(self, sessions, counters):
        from llm.services.runtime.usage import ComponentUsage
        self._model_usage = ComponentUsage(self, sessions, counters)
        return self

    @contextmanager
    def model_scope(self):
        from llm.providers.observations import model_observer
        from llm.providers.runtime import logging_scope
        admission = self._provider_calls.scope() if self._provider_calls is not None else nullcontext()
        observations = self._observability.scope() if self._observability is not None else nullcontext()
        with observations, admission, model_observer(self._model_usage), logging_scope(self.ownership.path.parent):
            yield

    @workspace_locked
    def require_model_observation(self, *clients):
        """한도가 켜졌으면 관찰 계약을 지원하지 않는 주입 클라이언트를 호출 전에 거부한다."""
        from llm.services.runtime.usage import current_usage
        from llm.services.runtime.policies import ExecutionLimitError
        project, _ = self._current()
        scope = current_usage()
        policies = [project.config.policies.get("usage", {}), scope.settings if scope else {}]
        limited = any(policy.get(key) is not None for policy in policies
                      for key in ("max_calls", "max_tokens", "project_max_calls", "project_max_tokens"))
        if limited and (self._model_usage is None or any(c is not None and getattr(c, "observes_model_calls", False) is not True for c in clients)):
            raise ExecutionLimitError("usage_configuration", "Configured quota requires observable component model clients")

    @workspace_locked
    def model_usage(self):
        project, component = self._current()
        return component.model_usage(project)

    amodel_usage = async_method(model_usage)

    def bind_runtime(self, *, runner, access_check, history_reader=None, provider_calls=None, observability=None) -> "ComponentData":
        """Facade의 비동기 실행기와 수명 검사를 명시적으로 연결한다."""
        if not callable(runner) or not callable(access_check) or history_reader is not None and not callable(history_reader):
            raise TypeError("Component runtime hooks must be callable")
        self._async_runner, self._access_check = runner, access_check
        self.history_reader = history_reader
        self._provider_calls = provider_calls
        self._observability = observability
        return self

    @workspace_locked
    def configuration(self) -> dict:
        project, component = self._current()
        return component.configuration(project)

    @workspace_locked
    def configure(self, data: dict, *, expected_version=None) -> None:
        project, component = self._current()
        check_revision(component.configuration(project), expected_version)
        project.config.parameters.setdefault("components", {})[self.name] = deepcopy(data)
        self.registry.validate_configuration(project)
        self.access.repository.save(project)
        log_event(project.paths.logs, "component.configured", entity_id=project.id)

    @workspace_locked
    def effective_configuration(self) -> dict:
        from llm.core.configuration import component_configuration
        project, component = self._current()
        return component_configuration(component, project)

    aeffective_configuration = async_method(effective_configuration)

    @workspace_locked
    def create(self, data: dict, *, identifier: Optional[str] = None) -> str:
        project, component = self._current()
        identifier = component.create(project, data, identifier=identifier)
        log_event(project.paths.logs, "component.record_created", entity_id=project.id)
        return identifier

    @workspace_locked
    def load(self, identifier: str) -> dict:
        project, component = self._current()
        return component.load(project, identifier)

    @workspace_locked
    def snapshot(self, identifier=None) -> dict:
        """UI 편집용 데이터와 충돌 검사용 버전을 함께 조회한다. None이면 Component 설정이다."""
        project, component = self._current()
        data = component.configuration(project) if identifier is None else component.load(project, identifier)
        return {"data": deepcopy(data), "version": revision_token(data)}

    @workspace_locked
    def list(self) -> dict[str, dict]:
        project, component = self._current()
        return component.list(project)

    @workspace_locked
    def save(self, identifier: str, data: dict, *, expected_version=None) -> None:
        project, component = self._current()
        if expected_version is not None:
            check_revision(component.load(project, identifier), expected_version)
        component.save(project, identifier, data)
        log_event(project.paths.logs, "component.record_saved", entity_id=project.id)

    @workspace_locked
    def update(self, identifier: str, changes: dict, *, expected_version=None) -> dict:
        project, component = self._current()
        if expected_version is not None:
            check_revision(component.load(project, identifier), expected_version)
        data = component.update(project, identifier, changes)
        log_event(project.paths.logs, "component.record_updated", entity_id=project.id)
        return data

    @workspace_locked
    def delete(self, identifier: str, *, expected_version=None) -> None:
        project, component = self._current()
        if expected_version is not None:
            check_revision(component.load(project, identifier), expected_version)
        component.delete(project, identifier)
        log_event(project.paths.logs, "component.record_deleted", entity_id=project.id)

    aconfiguration = async_method(configuration)

    aconfigure = async_method(configure)

    acreate = async_method(create)

    aload = async_method(load)
    asnapshot = async_method(snapshot)

    alist = async_method(list)

    asave = async_method(save)

    aupdate = async_method(update)

    adelete = async_method(delete)
