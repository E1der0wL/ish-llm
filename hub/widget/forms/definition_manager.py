"""Prompt/Agent/Workflow CRUD with inline JSON and live graph preview."""

import json
import re

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import HSplit, VSplit
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Frame, Label

from ..controls import Button, TextArea
from ..dialog import Dialog
from ..workflow_graph import workflow_diagram
from .record_manager import RecordManager


def initial_definition(name):
    return {
        "prompts": {"messages": [{"role": "system", "content": "You are a helpful assistant."}]},
        "agents": {"purpose": "", "engine": "loop", "system_prompt": ""},
        "workflows": {"schema_version": 1, "entry": "done", "nodes": {"done": {"type": "end"}}, "edges": []},
        "memory": {"content": "", "kind": "note", "scope": "project", "status": "confirmed", "tags": []},
    }[name]


class DefinitionManager(RecordManager):
    def __init__(self, view, request, *, name, edit_text):
        self.create_button = Button(view.t("create"), handler=self.create_item, width=10)
        self.edit_button = Button(view.t("definition_edit"), handler=self.open_item, width=10)
        self.delete_button = Button(view.t("delete"), handler=self.delete, width=10)
        super().__init__(view, request, name=name, edit_text=edit_text,
                         extra_buttons=(self.create_button, self.edit_button, self.delete_button), button_width=10)

    def hint(self):
        return self.status or self.view.t("definition_manager_hint")

    def create_item(self):
        if not self.busy:
            editor = DefinitionEditor(self)
            self.view.dialogs.show(editor, editor.identifier)

    def open_item(self):
        if not self.selected or self.busy:
            return
        name = self.selected
        def ready(record):
            if self.view._dialog is self:
                editor = DefinitionEditor(self, name, record)
                self.view.dialogs.show(editor, editor.input)
        self.call("read", name, completed=ready)


class DefinitionEditor(Dialog):
    def __init__(self, manager, name="", record=None):
        self.manager, self.view, self.original_name = manager, manager.view, name
        self.version = record["version"] if record else None
        self.busy, self.status, self._preview_timer = False, "", None
        text = record["text"] if record else json.dumps(initial_definition(manager.name), ensure_ascii=False, indent=2)
        self.identifier = TextArea(text=name, multiline=False, height=1, read_only=Condition(lambda: bool(name) or self.busy))
        self.input = TextArea(text=text, multiline=True, scrollbar=True, wrap_lines=False, read_only=Condition(lambda: self.busy))
        self.preview = TextArea(read_only=True, scrollbar=True, wrap_lines=False)
        content = Frame(self.input, title="JSON")
        if manager.name == "workflows":
            content = VSplit([content, Frame(self.preview, title=self.view.t("workflow_preview"))], padding=1)
        self.buttons = [Button(self.view.t("settings_save"), handler=self.save),
                        Button(self.view.t("cancel"), handler=self.close)]
        super().__init__(title=self.view.t(manager.name + "_title"), body=HSplit([
            Label(self.view.t("definition_name")), self.identifier, content, Label(lambda: self.status)],
            height=lambda: Dimension(preferred=max(8, self.view._rows() - 8), min=4)),
            buttons=self.buttons, width=lambda: Dimension(preferred=max(20, self.view._columns() - 4)))
        self.on_close = self.close
        self.input.buffer.on_text_changed += self.schedule_preview
        self.refresh_preview()

    def schedule_preview(self, _=None):
        if self._preview_timer:
            self._preview_timer.cancel()
        # Parse at most once per short typing burst, never on every paint.
        self._preview_timer = get_app().loop.call_later(.1, self.refresh_preview)

    def refresh_preview(self):
        self._preview_timer = None
        try:
            if len(self.input.text) > 1024 * 1024:
                raise ValueError(self.view.t("definition_too_large"))
            value = json.loads(self.input.text)
            if not isinstance(value, dict):
                raise ValueError(self.view.t("definition_object"))
            if self.manager.name == "workflows":
                self.preview.text = workflow_diagram(self.input.text)
            self.status = self.view.t("definition_validation")
        except (ValueError, TypeError) as error:
            self.status = self.view.t("error", error=error)
            if self.manager.name == "workflows":
                self.preview.text = self.view.t("workflow_invalid") + "\n" + str(error)
        get_app().invalidate()

    def save(self):
        if self.busy:
            return
        name = self.identifier.text.strip()
        try:
            if len(self.input.text) > 1024 * 1024:
                raise ValueError(self.view.t("definition_too_large"))
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
                raise ValueError(self.view.t("definition_name_invalid"))
            if not isinstance(json.loads(self.input.text), dict):
                raise ValueError(self.view.t("definition_object"))
        except ValueError as error:
            self.status = self.view.t("error", error=error)
            return
        self.busy = True
        token = self.view.progress.start(self.view.t("component_loading"))
        def done(entries):
            self.view.progress.finish(token)
            self.busy = False
            if entries is None:
                self.status = self.view.t("definition_save_failed")
            else:
                self.manager.entries, self.manager.selected = entries, name
                self.manager.status = self.view.t("manager_saved")
                if self.view._dialog is self:
                    self.close()
            get_app().invalidate()
        self.manager.request("save" if self.original_name else "create",
            {"name": name, "text": self.input.text}, self.version, done)

    def close(self):
        if not self.busy:
            self.view.dialogs.show(self.manager, self.manager.list)

    def dispose(self):
        if self._preview_timer:
            self._preview_timer.cancel()
            self._preview_timer = None
