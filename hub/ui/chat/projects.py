"""Project navigation and CRUD for the conversation sidebar."""

from llm.services.schema import ProjectConfiguration

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit
from prompt_toolkit.widgets import Label

from ...widget.controls import TextArea
from ...widget.sidebar import SidebarItem, SidebarList
from ..error_presentation import operation_notice


class ChatProjects:
    def __init__(self, view):
        self.view = view
        self.items = []
        self.loaded = self.busy = False
        self.selected = ""
        self.pending = None
        self.control = SidebarList(self.rows, lambda: self.selected or getattr(view, "project_id", ""), self.select)

    def rows(self):
        return [SidebarItem(item["id"], item["title"]) for item in self.items]

    def call(self, operation, *args, completed=None):
        if self.busy or self.view.settings.request is None:
            return
        self.busy = True
        token = self.view.progress.start(self.view.t("progress_settings_work"))
        def done(value, error=None):
            self.busy = False
            self.view.progress.finish(token)
            if error is not None:
                self.view.operation_diagnostics.record(getattr(self.view, "project_id", ""), error)
                self.view.notice = self.view.t("error", error=operation_notice(error, self.view.t))
                self.selected = getattr(self.view, "project_id", "")
            elif completed:
                completed(value)
            if self.pending is not None and not self.busy:
                target, self.pending = self.pending, None
                self.select(target)
            get_app().invalidate()
        self.view.settings.request(operation, args, done)

    def refresh(self):
        def loaded(items):
            self.items = items
            self.loaded = True
            self.selected = getattr(self.view, "project_id", "")
        self.call("list_projects", completed=loaded)

    def select(self, identifier):
        self.view._session_focus = True
        self.selected = identifier
        if self.busy:
            self.pending = identifier
            return
        if identifier == getattr(self.view, "project_id", ""):
            return
        self.view.remember_position()
        if self.view.on_save_view:
            self.view.on_save_view()
        def activated(_):
            # Drafts remain owned by their project; hidden filters must not leak across projects.
            self.view.settings.project_changed(identifier)
        self.call("activate_project", identifier, completed=activated)

    def name_dialog(self, title, initial, action):
        field = TextArea(text=initial, height=1, multiline=False)
        def validate():
            if field.text.strip():
                return True
            self.view.notice = self.view.t("settings_title_required")
            return False
        def accept():
            action(field.text.strip())
        self.view.open_dialog(title, HSplit([Label(self.view.t("settings_project_name")), field]),
                              accept, field, validate=validate)
        def entered(_):
            if validate():
                self.view.close_dialog()
                accept()
            return True
        field.buffer.accept_handler = entered

    def perform(self, key):
        if self.busy:
            return
        identifier = self.selected or getattr(self.view, "project_id", "")
        item = next((item for item in self.items if item["id"] == identifier), None)
        def saved(record):
            if record is None:
                return
            view = ProjectConfiguration.view(record)
            target, title = view.project_id, view.values.title
            self.items = [row for row in self.items if row["id"] != target] + [{"id": target, "title": title}]
            if target == getattr(self.view, "project_id", ""):
                self.view.project_title = title
            self.select(target)
        if key == "c":
            self.name_dialog(self.view.t("settings_create"), "",
                lambda title: self.call("create_project", {"title": title, "config": {}}, completed=saved))
        elif item and key in ("e", "r"):
            operation = "rename_project" if key == "e" else "clone_project"
            title = item["title"] + (" " + self.view.t("settings_copy_suffix") if key == "r" else "")
            self.name_dialog(self.view.t("settings_rename" if key == "e" else "settings_clone"), title,
                lambda name: self.call(operation, identifier, name, completed=saved))
        elif item and key == "d":
            self.view.open_dialog(self.view.t("settings_delete"),
                Label(self.view.t("settings_delete_confirm", title=item["title"])),
                lambda: self.call("delete_project", identifier, completed=lambda _: self.refresh()))
