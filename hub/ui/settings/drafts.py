"""Own settings drafts, saved preferences and save/discard/reload transitions."""

from hub.locales.keys import MessageKey

from dataclasses import asdict
from prompt_toolkit.widgets import Label
from ...config.theme import HubTheme
from .pages import GlobalPage


class SettingsDrafts:
    def __init__(self, screen):
        self.screen = screen
        self.pages = {}
        self.preferences = {}
        self.applied_preferences = {}
        self.workspace_anchor = None
        self.on_restart = None
        self.current_project_id = ""
        self.initial_appearance = asdict(screen.view.theme)
        self.saved_sidebar_width = None

    def reset_for_preset(self, preferences):
        self.pages.clear()
        self.screen.clear_page()
        self.saved_sidebar_width = preferences.optional_sidebar_width

    def select_project(self, identifier):
        self.current_project_id = identifier

    def active_pages(self):
        return [page for page in self.pages.values() if isinstance(page, GlobalPage)
                or getattr(page, "identifier", None) == self.current_project_id]

    def save(self):
        if self.screen.busy:
            return
        self.screen.navigation.finish_edit()
        pages = [page for page in self.active_pages() if page.dirty]
        selected = self.screen.selected
        try:
            requests = [page.save_request() for page in pages]
        except (ValueError, TypeError) as error:
            self.screen.error(error)
            return
        if not requests:
            self.screen.status = self.screen.t(MessageKey.SETTINGS_SAVED)
            self.prompt_restart(self.screen.global_keys)
            return
        def saved(batch):
            if batch is None:
                return
            target = self.current_project_id
            for page, result in zip(pages, batch["results"]):
                page.accept_saved(result)
            self.current_project_id = target
            if batch.get("error"):
                failed = pages[len(batch["results"])]
                self.screen.selected = self.screen.navigation.left_key = (
                    failed.name if isinstance(failed, GlobalPage) else failed.section)
                self.screen.show_page(failed)
                self.screen.error(batch["error"])
            else:
                self.screen.choose(selected)
                self.screen.status = self.screen.t(MessageKey.SETTINGS_SAVED)
                self.prompt_restart(self.screen.global_keys)
        self.screen.call("save_all", requests, completed=saved)

    def prompt_restart(self, sections):
        from ...config.restart import restart_changes
        changed = [name for section in sections for name in restart_changes(section,
            self.preferences.get(section, {}), self.applied_preferences.get(section, {}),
            anchor=self.workspace_anchor)]
        if not changed or self.on_restart is None:
            return
        self.screen.view.open_dialog(self.screen.t(MessageKey.SETTINGS_RESTART_TITLE),
            Label(self.screen.t(MessageKey.SETTINGS_RESTART_CONFIRM, fields="\n".join(changed))),
            self.on_restart, accept_text=self.screen.t(MessageKey.SETTINGS_RESTART_NOW))

    def discard(self):
        if self.screen.busy:
            return
        self.screen.navigation.finish_edit()
        self.screen.view.set_theme(HubTheme(**self.preferences.get("appearance", self.initial_appearance)))
        selected = self.screen.selected
        project = self.pages.get(self.current_project_id)
        for key in (*self.screen.global_keys, self.current_project_id):
            self.pages.pop(key, None)
        if project is not None:
            from .pages import ProjectPage
            self.pages[self.current_project_id] = ProjectPage(self.screen, project.record)
        self.screen.page = None
        self.screen._engine_target = self.screen._component_target = self.screen._pending_selection = None
        self.screen.choose(selected)
        self.screen.status = self.screen.t(MessageKey.SETTINGS_DISCARDED)

    def reload(self, page):
        if self.screen.busy:
            return
        self.screen.navigation.finish_edit()
        if isinstance(page, GlobalPage):
            def loaded(value):
                if value is not None:
                    self.preferences.update(value["preferences"])
                    if self.saved_sidebar_width is not None:
                        self.preferences.setdefault("appearance", self.initial_appearance.copy())["sidebar_width"] = self.saved_sidebar_width
                    self.screen.usage = value.get("usage", [])
                    self.cancel(page)
            if self.screen.request:
                self.screen.call("catalog", completed=loaded)
            else:
                self.cancel(page)
        else:
            self.screen.call("load_project", page.identifier, completed=self.screen.loaded_project)

    def cancel(self, page):
        if self.screen.busy:
            return
        self.screen.navigation.finish_edit()
        if isinstance(page, GlobalPage):
            values = self.preferences.get(page.name, page.original)
            if page.name == "appearance":
                values = asdict(HubTheme(**values))
                self.screen.view.set_theme(HubTheme(**values))
            self.pages[page.name] = GlobalPage(self.screen, page.name, values)
            self.screen.show_page(self.pages[page.name])
        else:
            self.screen.loaded_project(page.record)
        self.screen.status = self.screen.t(MessageKey.SETTINGS_CANCELLED)
