"""Project CRUD dialogs; the screen owns catalog rendering and request dispatch."""

from prompt_toolkit.layout import HSplit
from prompt_toolkit.widgets import Label
from ...widget.controls import TextArea
from .pages import ProjectPage


class SettingsProjectActions:
    def __init__(self, screen):
        self.screen = screen

    def perform(self, key):
        if self.screen.busy:
            return
        identifier = self.screen.navigation.focused_key()
        if key == "c":
            self.name_dialog(self.screen.t("settings_create"), "",
                lambda title: self.screen.call("create_project", {"title": title, "config": {}}, completed=self.screen.saved_project))
            return
        if identifier in (None, "new", *self.screen.global_keys):
            return
        def ready(record):
            if record is None:
                return
            page = ProjectPage(self.screen, record)
            {"d": self.delete, "e": self.rename, "r": self.clone}[key](page)
        self.screen.call("load_project", identifier, completed=ready)

    def name_dialog(self, title, initial, action):
        name = TextArea(text=initial, height=1, multiline=False)
        def validate():
            if name.text.strip():
                return True
            self.screen.error(self.screen.t("settings_title_required"))
            return False
        def accept():
            action(name.text.strip())
        self.screen.view.open_dialog(title, HSplit([Label(self.screen.t("settings_project_name")), name]), accept, name, validate=validate)
        def entered(_):
            if validate():
                self.screen.view.close_dialog()
                accept()
            return True
        name.buffer.accept_handler = entered

    def clone(self, page):
        if not self.screen.busy:
            self.name_dialog(self.screen.t("settings_clone"), page.record["values"]["title"] + " " + self.screen.t("settings_copy_suffix"),
                lambda title: self.screen.call("clone_project", page.identifier, title, completed=self.screen.saved_project))

    def rename(self, page):
        def renamed(record):
            if record is None:
                return
            identifier, title = record["project"]["id"], record["values"]["title"]
            for item in self.screen.projects:
                if item["id"] == identifier:
                    item["title"] = title
            cached = self.screen.drafts.pages.get(identifier)
            if cached is not None:
                field = cached.form.fields[("title",)]
                if field.input.text == field.initial:
                    field.input.text = title
                field.initial = field.original = title
                cached.record["values"]["title"] = title
            self.screen._sidebar()
            self.screen.status = self.screen.t("settings_saved")
        self.name_dialog(self.screen.t("settings_rename"), page.record["values"]["title"],
            lambda title: self.screen.call("rename_project", page.identifier, title, completed=renamed))

    def delete(self, page):
        if self.screen.busy:
            return
        def deleted(value):
            if value:
                self.screen.drafts.pages.pop(page.identifier, None)
                self.screen.projects = [item for item in self.screen.projects if item["id"] != page.identifier]
                if self.screen.drafts.current_project_id == page.identifier:
                    self.screen.drafts.current_project_id = getattr(self.screen.view, "project_id", "")
                self.screen._sidebar()
                self.screen.choose("profile")
                self.screen.status = self.screen.t("settings_deleted")
        self.screen.view.open_dialog(self.screen.t("settings_delete"), Label(self.screen.t("settings_delete_confirm",
            title=page.record["values"]["title"])),
            lambda: self.screen.call("delete_project", page.identifier, completed=deleted))
