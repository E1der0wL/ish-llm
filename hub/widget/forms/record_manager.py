"""Skill/Workflow/Agent/RAG editing through the shared component manager."""

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


import subprocess

from prompt_toolkit.application.current import get_app

from .component_manager import ComponentManager
from ...components import component_spec, ManagerKind


class RecordManager(ComponentManager):
    def __init__(
        self,
        view,
        request,
        *,
        name,
        edit_text,
        extra_buttons=(),
        detail_lines=ManagerView.DETAIL_LINES,
        button_width=ManagerView.BUTTON_WIDTH
    ):
        super().__init__(
            view,
            request,
            name=name,
            extra_buttons=extra_buttons,
            detail_lines=detail_lines,
            button_width=button_width,
            component_settings=component_spec(name).manager != ManagerKind.AGENT,
        )
        self.edit_text = edit_text
        self.drafts = {}

    def open_item(self):
        name = self.selected
        if not name:
            return

        def ready(record):
            if self.view._dialog is self:
                get_app().create_background_task(self.edit(name, record))

        self.call(ManagementAction.READ, name, completed=ready)

    async def edit(self, name, record):
        self.busy = ManagerView.BUSY
        # A failed save keeps both the edited text and its original version.
        # Reopening never silently overwrites a concurrent component change.
        text, version = self.drafts.get(
            name, (EditorRecord(record).text, EditorRecord(record).version)
        )
        try:
            edited = await self.edit_text(
                text, self.view.general, suffix=component_spec(self.name).editor_suffix
            )
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            self.status = self.view.t(MessageKey.ERROR, error=error)
            return
        finally:
            self.busy = ManagerView.IDLE
            get_app().invalidate()
        if edited == EditorRecord(record).text:
            self.drafts.pop(name, ManagerView.ABSENT)
            return
        self.drafts[name] = edited, version

        def saved(entries):
            self.entries = entries
            self.drafts.pop(name, ManagerView.ABSENT)
            self.status = self.view.t(MessageKey.MANAGER_SAVED)

        self.call(
            ManagementAction.SAVE,
            EditorRecord.change(name, edited),
            version,
            completed=saved,
        )
