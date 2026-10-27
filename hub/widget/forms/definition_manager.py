"""Prompt/Agent/Workflow CRUD with inline JSON and live graph preview."""


from hub.locales.keys import MessageKey, MessagePattern
from .manager_values import (
    ManagerView,
    DefinitionView,
)
from hub.backend.project.management_values import (
    ManagementAction,
    EditorRecord,
)
from hub.components import ManagedComponents
from .manager_values import DefinitionDraft

from ...ui.input.json_document import JsonDocument


from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import HSplit, VSplit
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Frame, Label

from ..controls import Button, TextArea
from ..dialog import Dialog, PopupSize
from ..section import Heading
from ..workflow_graph import workflow_diagram
from .record_manager import RecordManager


def initial_definition(name):
    return DefinitionDraft.create(name)


class DefinitionManager(RecordManager):
    def __init__(self, view, request, *, name, edit_text):
        self.create_button = Button(
            view.t(MessageKey.COMPONENT_CREATE),
            handler=self.create_item,
            width=DefinitionView.BUTTON_WIDTH,
        )
        self.edit_button = Button(
            view.t(MessageKey.DEFINITION_EDIT),
            handler=self.open_item,
            width=DefinitionView.BUTTON_WIDTH,
        )
        self.delete_button = Button(
            view.t(ManagerView.DELETE_TEXT),
            handler=self.delete,
            width=DefinitionView.BUTTON_WIDTH,
        )
        super().__init__(
            view,
            request,
            name=name,
            edit_text=edit_text,
            extra_buttons=(self.create_button, self.edit_button, self.delete_button),
            button_width=DefinitionView.BUTTON_WIDTH,
        )

    def create_item(self):
        if not self.busy:
            if self.name == ManagedComponents.AGENTS.name:
                self.agent_editor()
                return
            editor = DefinitionEditor(self)
            self.view.dialogs.show(editor, editor.identifier)

    def open_item(self):
        if not self.selected or self.busy:
            return
        name = self.selected

        def ready(record):
            if self.view._dialog is self:
                if self.name == ManagedComponents.AGENTS.name:
                    self.agent_editor(name, record)
                    return
                editor = DefinitionEditor(self, name, record)
                self.view.dialogs.show(editor, editor.input)

        self.call(ManagementAction.READ, name, completed=ready)

    def agent_editor(self, name="", record=None):
        from .agent_editor import AgentEditor
        editor = AgentEditor(self, name, record)
        self.view.dialogs.show(editor, editor.workspace.sidebar)
        editor.load()


class DefinitionEditor(Dialog):
    def __init__(self, manager, name=ManagerView.EMPTY, record=ManagerView.ABSENT):
        self.manager, self.view, self.original_name = manager, manager.view, name
        self.version = EditorRecord(record).version if record else ManagerView.ABSENT
        self.busy, self.status, self._preview_timer = (
            ManagerView.IDLE,
            ManagerView.EMPTY,
            ManagerView.ABSENT,
        )
        text = (
            EditorRecord(record).text
            if record
            else JsonDocument.render(
                initial_definition(manager.name),
                allow_non_finite=JsonDocument.ALLOW_NON_FINITE_PREVIEW,
            )
        )
        self.identifier = TextArea(
            text=name,
            multiline=DefinitionView.SINGLE_LINE,
            height=DefinitionView.IDENTIFIER_HEIGHT,
            read_only=Condition(lambda: bool(name) or self.busy),
        )
        self.input = TextArea(
            text=text,
            multiline=DefinitionView.MULTILINE,
            scrollbar=DefinitionView.SCROLLBAR,
            wrap_lines=DefinitionView.WRAP_LINES,
            read_only=Condition(lambda: self.busy),
        )
        self.preview = TextArea(
            read_only=DefinitionView.READ_ONLY,
            scrollbar=DefinitionView.SCROLLBAR,
            wrap_lines=DefinitionView.WRAP_LINES,
        )
        content = Frame(self.input, title=DefinitionView.JSON_LABEL)
        if manager.name == ManagedComponents.WORKFLOWS.name:
            content = VSplit(
                [
                    content,
                    Frame(self.preview, title=self.view.t(MessageKey.WORKFLOW_PREVIEW)),
                ],
                padding=DefinitionView.PREVIEW_PADDING,
            )
        self.buttons = [
            Button(self.view.t(MessageKey.SETTINGS_SAVE), handler=self.save),
            Button(self.view.t(MessageKey.CANCEL), handler=self.close),
        ]
        super().__init__(
            title=self.view.t(MessagePattern.component_title(manager.name)),
            body=HSplit(
                [
                    Heading(self.view.t(MessageKey.DEFINITION_NAME)),
                    self.identifier,
                    content,
                ],
                height=lambda: Dimension(
                    preferred=max(
                        DefinitionView.MINIMUM_PREFERRED_HEIGHT,
                        self.view._rows() - DefinitionView.VERTICAL_MARGIN,
                    ),
                    min=DefinitionView.MINIMUM_HEIGHT,
                ),
            ),
            buttons=self.buttons,
            status_bar=self.view.progress.container(lambda: self.status, surface=self),
            content_padding=(0 if manager.name == ManagedComponents.WORKFLOWS.name
                             else Dialog.CONTENT_PADDING),
            size=(PopupSize.LARGE if manager.name == ManagedComponents.WORKFLOWS.name
                  else PopupSize.MEDIUM),
        )
        self.on_close = self.close
        self.input.buffer.on_text_changed += self.schedule_preview
        self.refresh_preview()

    def schedule_preview(self, _=ManagerView.ABSENT):
        if self._preview_timer:
            self._preview_timer.cancel()
        # Parse at most once per short typing burst, never on every paint.
        self._preview_timer = get_app().loop.call_later(
            DefinitionView.PREVIEW_DELAY, self.refresh_preview
        )

    def refresh_preview(self):
        self._preview_timer = ManagerView.ABSENT
        try:
            DefinitionDraft.require_size(self.input.text, self.view.t)
            value = DefinitionDraft.parse_object(self.input.text, self.view.t)
            if self.manager.name == ManagedComponents.WORKFLOWS.name:
                self.preview.text = workflow_diagram(self.input.text)
            self.status = self.view.t(MessageKey.DEFINITION_VALIDATION)
        except (ValueError, TypeError) as error:
            self.status = self.view.t(MessageKey.ERROR, error=error)
            if self.manager.name == ManagedComponents.WORKFLOWS.name:
                self.preview.text = (
                    self.view.t(MessageKey.WORKFLOW_INVALID)
                    + DefinitionView.NEWLINE
                    + str(error)
                )
        get_app().invalidate()

    def save(self):
        if self.busy:
            return
        name = self.identifier.text.strip()
        try:
            DefinitionDraft.require_size(self.input.text, self.view.t)
            DefinitionDraft.require_name(name, self.view.t)
            definition = DefinitionDraft.parse_object(self.input.text, self.view.t)
        except ValueError as error:
            self.status = self.view.t(MessageKey.ERROR, error=error)
            return
        self.busy = ManagerView.BUSY
        token = self.view.progress.start(self.view.t(MessageKey.COMPONENT_LOADING))

        def done(entries):
            self.view.progress.finish(token)
            self.busy = ManagerView.IDLE
            if entries is ManagerView.ABSENT:
                self.status = self.view.t(MessageKey.DEFINITION_SAVE_FAILED)
            else:
                self.manager.entries, self.manager.selected = entries, name
                self.manager.status = self.view.t(MessageKey.MANAGER_SAVED)
                if self.view._dialog is self:
                    self.close()
            get_app().invalidate()

        self.manager.request(
            ManagementAction.SAVE if self.original_name else ManagementAction.CREATE,
            EditorRecord.change(name, self.input.text),
            self.version,
            self.view.progress.callback(token, done),
        )

    def close(self):
        if not self.busy:
            self.view.dialogs.show(self.manager, self.manager.list)

    def dispose(self):
        if self._preview_timer:
            self._preview_timer.cancel()
            self._preview_timer = ManagerView.ABSENT
