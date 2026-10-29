"""Project navigation and CRUD for the conversation sidebar."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit
from prompt_toolkit.widgets import Label

from llm.services.schema import ProjectConfiguration
from llm.services.configuration_records import ProjectConfigForm
from ...backend.project.settings_contracts import ProjectSummaries, SettingsOperation
from ...locales.keys import MessageKey
from ...model import HubSnapshot
from ...widget.controls import TextArea
from ...widget.dialog import PopupSize
from ...widget.section import Heading
from ...widget.sidebar import SidebarItem, SidebarList
from ..error_presentation import operation_notice
from ..input.keys import KeyName
from ..settings.screen import SettingsScreen
from ..view import HubView


class ChatProjects:
    NOT_LOADED = False
    LOADED = True
    BUSY = True
    IDLE = False
    NO_PENDING = None
    NO_COMPLETION = None
    NO_ERROR = None
    NO_RESULT = None
    NAME_HEIGHT = 1
    NAME_MULTILINE = False
    VALID_NAME = True
    INVALID_NAME = False
    INPUT_HANDLED = True
    NEW_TITLE = ""
    COPY_SEPARATOR = " "
    NO_COPY_SUFFIX = ""
    NAME_ACTIONS = (KeyName.EDIT, KeyName.R)

    def __init__(self, view):
        self.view = view
        self.items = []
        self.loaded = self.NOT_LOADED
        self.busy = self.IDLE
        self.selected = HubSnapshot.NO_PROJECT
        self.pending = self.NO_PENDING
        self.control = SidebarList(
            self.rows,
            lambda: self.selected or HubSnapshot.project_identifier(view),
            self.select,
        )

    @property
    def items(self):
        return self._project_rows.to_records()

    @items.setter
    def items(self, values):
        self._project_rows = ProjectSummaries(values)

    def rename_observed(self, identifier, title):
        self._project_rows.rename_matches(identifier, title)

    def rows(self):
        return [SidebarItem(row.id, row.title) for row in self._project_rows.rows()]

    def call(self, operation, *args, completed=NO_COMPLETION):
        if self.busy or self.view.settings.request is SettingsScreen.NO_REQUEST:
            return
        self.busy = self.BUSY
        token = self.view.progress.start(self.view.t(MessageKey.PROGRESS_SETTINGS_WORK))

        def done(value, error=self.NO_ERROR):
            self.busy = self.IDLE
            self.view.progress.finish(token)
            if error is not self.NO_ERROR:
                self.view.operation_diagnostics.record_view(self.view, error)
                self.view.notice = self.view.t(
                    MessageKey.ERROR, error=operation_notice(error, self.view.t)
                )
                self.selected = HubSnapshot.project_identifier(self.view)
            elif completed:
                completed(value)
            if self.pending is not self.NO_PENDING and not self.busy:
                target, self.pending = self.pending, self.NO_PENDING
                self.select(target)
            get_app().invalidate()

        self.view.settings.request(operation, args, self.view.progress.callback(token, done))

    def refresh(self):
        def loaded(items):
            self.items = items
            self.loaded = self.LOADED
            self.selected = HubSnapshot.project_identifier(self.view)

        self.call(SettingsOperation.LIST_PROJECTS, completed=loaded)

    def select(self, identifier):
        HubView.request_sidebar_focus(self.view)
        self.selected = identifier
        if self.busy:
            self.pending = identifier
            return
        if identifier == HubSnapshot.project_identifier(self.view):
            return
        self.view.remember_position()
        if self.view.on_save_view:
            self.view.on_save_view()

        def activated(_):
            # Drafts stay with their Project; hidden filters do not cross Projects.
            self.view.settings.project_changed(identifier)

        self.call(SettingsOperation.ACTIVATE_PROJECT, identifier, completed=activated)

    def name_dialog(self, title, initial, action, *, size=PopupSize.MEDIUM):
        field = TextArea(
            text=initial, height=self.NAME_HEIGHT, multiline=self.NAME_MULTILINE
        )

        def validate():
            if field.text.strip():
                return self.VALID_NAME
            self.view.notice = self.view.t(MessageKey.SETTINGS_TITLE_REQUIRED)
            return self.INVALID_NAME

        def accept():
            action(field.text.strip())

        self.view.open_dialog(
            title,
            HSplit([Heading(self.view.t(MessageKey.SETTINGS_PROJECT_NAME)), field]),
            accept,
            field,
            validate=validate,
            size=size,
            allow_small_input=size == PopupSize.SMALL,
        )

        def entered(_):
            if validate():
                self.view.close_dialog()
                accept()
            return self.INPUT_HANDLED

        field.buffer.accept_handler = entered

    def perform(self, key):
        if self.busy:
            return
        identifier = self.selected or HubSnapshot.project_identifier(self.view)
        item = self._project_rows.find(identifier)

        def saved(record):
            if record is self.NO_RESULT:
                return
            view = ProjectConfiguration.view(record)
            target, title = view.project_id, view.values.title
            self._project_rows.replace(target, title)
            if target == HubSnapshot.project_identifier(self.view):
                self.view.project_title = title
            self.select(target)

        if key == KeyName.C:
            self.name_dialog(
                self.view.t(MessageKey.SETTINGS_CREATE),
                self.NEW_TITLE,
                lambda title: self.call(
                    SettingsOperation.CREATE_PROJECT,
                    ProjectConfigForm.creation_request(title),
                    completed=saved,
                ),
            )
        elif (
            item is not ProjectSummaries.NO_MATCH
            and item.present
            and key in self.NAME_ACTIONS
        ):
            operation = (
                SettingsOperation.RENAME_PROJECT
                if key == KeyName.EDIT
                else SettingsOperation.CLONE_PROJECT
            )
            title = item.title + (
                self.COPY_SEPARATOR + self.view.t(MessageKey.SETTINGS_COPY_SUFFIX)
                if key == KeyName.R
                else self.NO_COPY_SUFFIX
            )
            self.name_dialog(
                self.view.t(
                    MessageKey.SETTINGS_RENAME
                    if key == KeyName.EDIT
                    else MessageKey.SETTINGS_CLONE
                ),
                title,
                lambda name: self.call(operation, identifier, name, completed=saved),
                size=PopupSize.SMALL,
            )
        elif (
            item is not ProjectSummaries.NO_MATCH
            and item.present
            and key == KeyName.CLEAR
        ):
            self.view.open_dialog(
                self.view.t(MessageKey.SETTINGS_DELETE),
                Label(
                    self.view.t(MessageKey.SETTINGS_DELETE_CONFIRM, title=item.title)
                ),
                lambda: self.call(
                    SettingsOperation.DELETE_PROJECT,
                    identifier,
                    completed=lambda _: self.refresh(),
                ),
                size=PopupSize.SMALL,
            )
