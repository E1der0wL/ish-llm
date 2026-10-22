"""Skill/Workflow/Agent/RAG editing through the shared component manager."""

import subprocess

from prompt_toolkit.application.current import get_app

from .component_manager import ComponentManager
from ...components import component_spec, ManagerKind


class RecordManager(ComponentManager):
    def __init__(self, view, request, *, name, edit_text, extra_buttons=(), detail_lines=1, button_width=12):
        super().__init__(view, request, name=name, extra_buttons=extra_buttons, detail_lines=detail_lines,
                         button_width=button_width, component_settings=component_spec(name).manager != ManagerKind.AGENT)
        self.edit_text = edit_text
        self.drafts = {}

    def open_item(self):
        name = self.selected
        if not name:
            return
        def ready(record):
            if self.view._dialog is self:
                get_app().create_background_task(self.edit(name, record))
        self.call("read", name, completed=ready)

    async def edit(self, name, record):
        self.busy = True
        # A failed save keeps both the edited text and its original version.
        # Reopening never silently overwrites a concurrent component change.
        text, version = self.drafts.get(name, (record["text"], record["version"]))
        try:
            edited = await self.edit_text(text, self.view.general, suffix=".json")
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            self.status = self.view.t("error", error=error)
            return
        finally:
            self.busy = False
            get_app().invalidate()
        if edited == record["text"]:
            self.drafts.pop(name, None)
            return
        self.drafts[name] = edited, version
        def saved(entries):
            self.entries = entries
            self.drafts.pop(name, None)
            self.status = self.view.t("manager_saved")
        self.call("save", {"name": name, "text": edited}, version, completed=saved)
