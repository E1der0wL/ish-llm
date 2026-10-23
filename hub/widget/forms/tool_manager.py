"""Tool package browser with built-in toggles and the system file manager."""

from hub.locales.keys import MessageKey

from prompt_toolkit.application.current import get_app

from .component_manager import ComponentManager


class ToolManager(ComponentManager):
    def __init__(self, view, request, *, open_directory):
        super().__init__(view, request, name="tools", directories=True, toggle=True)
        self.open_directory = open_directory

    def open_item(self):
        if self.selected and not self.selected.startswith("builtin:"):
            self.call("open", self.selected,
                      completed=lambda path: get_app().create_background_task(self.launch(path)))

    async def launch(self, path):
        try:
            code = await self.open_directory(path)
            if code:
                raise OSError(self.view.t(MessageKey.TOOLS_EXPLORER_FAILED, code=code))
        except OSError as error:
            self.status = self.view.t(MessageKey.ERROR, error=error)
        get_app().invalidate()

    def toggle(self):
        item = next((item for item in self.entries if item["name"] == self.selected), None)
        if item and item.get("builtin"):
            self.call("toggle", item["name"], item["version"])
