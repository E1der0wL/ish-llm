"""Tool package browser with built-in toggles and the system file manager."""

import asyncio

from prompt_toolkit.application.current import get_app

from .component_manager import ComponentManager


class ToolManager(ComponentManager):
    def __init__(self, view, request):
        super().__init__(view, request, name="tools", directories=True, toggle=True)

    def open_item(self):
        if self.selected and not self.selected.startswith("builtin:"):
            self.call("open", self.selected,
                      completed=lambda path: get_app().create_background_task(self.launch(path)))

    async def launch(self, path):
        try:
            process = await asyncio.create_subprocess_exec("xdg-open", path,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            code = await process.wait()
            if code:
                raise OSError(self.view.t("tools_explorer_failed", code=code))
        except OSError as error:
            self.status = self.view.t("error", error=error)
        get_app().invalidate()

    def toggle(self):
        item = next((item for item in self.entries if item["name"] == self.selected), None)
        if item and item.get("builtin"):
            self.call("toggle", item["name"], item["version"])
