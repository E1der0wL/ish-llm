"""Tool 데이터의 편의 API. 활성화 목록 수정도 공통 ComponentData의 잠금과 수명 검사를 거쳐 실행한다.

Locked Project access to tool selection and the common component CRUD API."""

from typing import Sequence

from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import async_method


class ToolData(ComponentData):
    """Tool conveniences; runtime handlers are never required to edit selection."""

    @workspace_locked
    def enabled(self) -> list[str]:
        """Return a detached, ordered list of selected tool names."""
        project, component = self._current()
        return component.enabled(project)

    @workspace_locked
    def enable(self, *names: str) -> None:
        """Add names once, preserving existing selection and other settings."""
        _, component = self._current()
        names = component._selection_names(names)
        configuration = self.configuration()
        configuration["enabled"] = list(dict.fromkeys(configuration["enabled"] + names))
        self.configure(configuration)

    @workspace_locked
    def disable(self, *names: str) -> None:
        """Remove selected names; already disabled names are harmless."""
        _, component = self._current()
        names = component._selection_names(names)
        configuration = self.configuration()
        configuration["enabled"] = [name for name in configuration["enabled"] if name not in names]
        self.configure(configuration)

    @workspace_locked
    def set_enabled(self, names: Sequence[str]) -> None:
        """Replace selection, preserving all other component settings."""
        _, component = self._current()
        configuration = self.configuration()
        configuration["enabled"] = component._selection_names(names)
        self.configure(configuration)

    aenabled = async_method(enabled)
    aenable = async_method(enable)
    adisable = async_method(disable)
    aset_enabled = async_method(set_enabled)
