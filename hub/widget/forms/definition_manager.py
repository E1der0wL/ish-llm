"""Definition CRUD dispatches to the JSON, Agent or graph authoring editor."""


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
from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Frame

from ..controls import Button, TextArea
from ..dialog import Dialog, PopupSize
from ..section import Heading
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
            editor = self.definition_editor()
            self.view.dialogs.show(editor, editor.identifier)
            editor.load()

    def open_item(self):
        if not self.selected or self.busy:
            return
        name = self.selected

        def ready(record):
            if self.view._dialog is self:
                if self.name == ManagedComponents.AGENTS.name:
                    self.agent_editor(name, record)
                    return
                editor = self.definition_editor(name, record)
                self.view.dialogs.show(editor, editor.focus_target)
                editor.load()

        self.call(ManagementAction.READ, name, completed=ready)

    def agent_editor(self, name="", record=None):
        from .agent_editor import AgentEditor
        editor = AgentEditor(self, name, record)
        self.view.dialogs.show(editor, editor.workspace.sidebar)
        editor.load()

    def definition_editor(self, name='', record=None):
        if self.name == ManagedComponents.WORKFLOWS.name:
            from .workflow_editor import WorkflowEditor
            return WorkflowEditor(self, name, record)
        return DefinitionEditor(self, name, record)


class DefinitionEditor(Dialog):
    actions_active = None
    actions_focus = None
    @property
    def focus_target(self):
        return self.input

    def load(self):
        """Specialized editors may request their implementation declarations."""

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
        content = Frame(self.input, title=DefinitionView.JSON_LABEL)
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
            actions_active=self.actions_active, actions_focus=self.actions_focus,
            status_bar=self.view.progress.container(lambda: self.status, surface=self),
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
            DefinitionDraft.parse_object(self.input.text, self.view.t)
            self.status = self.view.t(MessageKey.DEFINITION_VALIDATION)
        except (ValueError, TypeError) as error:
            self.status = self.view.t(MessageKey.ERROR, error=error)
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
