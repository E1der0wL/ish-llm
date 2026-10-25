"""Tool package browser with built-in toggles and the system file manager."""

from hub.locales.keys import MessageKey
from .manager_values import (
    ManagerView,
    ManagerCallbacks,
    DefinitionView,
    RAGManagerView,
)
from hub.backend.project.management_values import (
    ManagementAction,
    ManagementEntry,
    EditorRecord,
)
from hub.components import ManagedComponents


from prompt_toolkit.application.current import get_app

from .component_manager import ComponentManager


class ToolManager(ComponentManager):
    def __init__(self, view, request, *, open_directory):
        super().__init__(
            view,
            request,
            name=ManagedComponents.TOOLS.name,
            directories=ManagerView.OPEN_DIRECTORIES,
            toggle=ManagerView.ENABLE_TOGGLE,
        )
        self.open_directory = open_directory

    def open_item(self):
        if self.selected and not self.selected.startswith(
            ManagementEntry.BUILTIN_PREFIX
        ):
            self.call(
                ManagementAction.OPEN,
                self.selected,
                completed=lambda path: get_app().create_background_task(
                    self.launch(path)
                ),
            )

    async def launch(self, path):
        try:
            code = await self.open_directory(path)
            if code:
                raise OSError(self.view.t(MessageKey.TOOLS_EXPLORER_FAILED, code=code))
        except OSError as error:
            self.status = self.view.t(MessageKey.ERROR, error=error)
        get_app().invalidate()

    def toggle(self):
        item = next(
            (
                item
                for item in self.entries
                if ManagementEntry(item).name == self.selected
            ),
            ManagerView.ABSENT,
        )
        if item and ManagementEntry(item).builtin:
            self.call(
                ManagementAction.TOGGLE,
                ManagementEntry(item).name,
                ManagementEntry(item).version,
            )
