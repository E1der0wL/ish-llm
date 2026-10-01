"""Tool 데이터의 편의 API. 활성화 목록 수정도 공통 ComponentData의 잠금과 수명 검사를 거쳐 실행한다.

Locked Project access to tool selection and the common component CRUD API."""

from typing import Sequence

from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import async_method


class ToolData(ComponentData):
    """Python Tool package CRUD와 명시적인 dependency 준비/활성화."""

    @workspace_locked
    def enabled(self) -> list[str]:
        """Return a detached, ordered list of selected tool names."""
        project, component = self._current()
        return component.enabled(project)

    @workspace_locked
    def enable(self, *names: str) -> None:
        """Add names once, preserving existing selection and other settings."""
        project, component = self._current()
        names = component._selection_names(names)
        for name in names:
            component.load(project, name)
        configuration = self.configuration()
        configuration["enabled"] = list(dict.fromkeys(configuration.get("enabled", []) + names))
        self.configure(configuration)

    @workspace_locked
    def disable(self, *names: str) -> None:
        """Remove selected names; already disabled names are harmless."""
        _, component = self._current()
        names = component._selection_names(names)
        configuration = self.configuration()
        configuration["enabled"] = [name for name in configuration.get("enabled", []) if name not in names]
        self.configure(configuration)

    @workspace_locked
    def set_enabled(self, names: Sequence[str]) -> None:
        """Replace selection, preserving all other component settings."""
        _, component = self._current()
        configuration = self.configuration()
        configuration["enabled"] = component._selection_names(names)
        project, _ = self._current()
        for name in configuration["enabled"]:
            component.load(project, name)
        self.configure(configuration)

    @workspace_locked
    def prepare(self, identifier: str) -> dict:
        """Host/UI 관리 API. ish 공용 dependency 확인/설치 후 main 계약을 검증한다."""
        from llm.services.runtime.tools import current_tool_call
        from llm.services.runtime.usage import current_usage
        if current_tool_call() is not None or current_usage() is not None:
            raise RuntimeError("Tool dependencies cannot be prepared from a running execution")
        project, component = self._current()
        return component.prepare(project, identifier)

    aprepare = async_method(prepare)
    aenabled = async_method(enabled)
    aenable = async_method(enable)
    adisable = async_method(disable)
    aset_enabled = async_method(set_enabled)
