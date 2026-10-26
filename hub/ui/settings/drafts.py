"""Own settings drafts, saved preferences and save/discard/reload transitions."""

from dataclasses import asdict
from prompt_toolkit.widgets import Label

from ...backend.project.settings_contracts import (
    SettingsBatch,
    SettingsCatalog,
    SettingsOperation,
)
from ...config.preference_records import PreferenceSnapshot, SidebarWidth
from ...config.sections import PreferenceSection
from ...config.theme import HubTheme
from ...config.workspace import WorkspaceSelection
from ...locales.keys import MessageKey
from ...model import HubSnapshot
from .pages import GlobalPage, ProjectPage


class SettingsDrafts:
    NO_RESTART = None
    NO_WIDTH = None
    NO_PAGE = None
    NO_RESULT = None
    RESTART_SEPARATOR = "\n"

    def __init__(self, screen):
        self.screen = screen
        self.pages = {}
        self._preferences = PreferenceSnapshot({})
        self._applied_preferences = PreferenceSnapshot({})
        self.workspace_anchor = WorkspaceSelection.UNBOUND_ANCHOR
        self.on_restart = self.NO_RESTART
        self.current_project_id = HubSnapshot.NO_PROJECT
        self.initial_appearance = asdict(screen.view.theme)
        self.saved_sidebar_width = self.NO_WIDTH

    @property
    def preferences(self):
        return self._preferences.record

    @preferences.setter
    def preferences(self, value):
        self._preferences = PreferenceSnapshot(value)

    @property
    def applied_preferences(self):
        return self._applied_preferences.record

    @applied_preferences.setter
    def applied_preferences(self, value):
        self._applied_preferences = PreferenceSnapshot(value)

    def replace_preferences(self, observation):
        self._preferences = observation

    def prepare_preferences(self):
        self._preferences.prepare_ui(self.initial_appearance)
        if self.saved_sidebar_width is not self.NO_WIDTH:
            SidebarWidth.store(
                self._preferences.section(PreferenceSection.APPEARANCE.value),
                self.saved_sidebar_width,
            )

    def preview_preferences(self):
        self._preferences = PreferenceSnapshot.preview(self.initial_appearance)

    def preference_values(self, name):
        return self._preferences.section(name)

    def store_preference(self, name, value):
        self._preferences.store_section(name, value)

    def set_sidebar_width(self, width, *, persist):
        if persist:
            self.saved_sidebar_width = width
        SidebarWidth.store(self.initial_appearance, width)
        self._preferences.set_sidebar(width, self.initial_appearance.copy())
        page = self.pages.get(PreferenceSection.APPEARANCE.value)
        if page is not self.NO_PAGE:
            page.accept_sidebar_width(width)

    def reset_for_preset(self, preferences):
        self.pages.clear()
        self.screen.clear_page()
        self.saved_sidebar_width = preferences.optional_sidebar_width

    def select_project(self, identifier):
        self.current_project_id = identifier

    def active_pages(self):
        return [
            page
            for page in self.pages.values()
            if isinstance(page, GlobalPage)
            or ProjectPage.identifier_for(page) == self.current_project_id
        ]

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
            self.prompt_restart(tuple(PreferenceSection))
            return

        def saved(batch):
            if batch is self.NO_RESULT:
                return
            observation = SettingsBatch.observe(batch)
            target = self.current_project_id
            for page, result in zip(pages, observation.results):
                page.accept_saved(result)
            self.current_project_id = target
            if observation.optional_error:
                failed = pages[len(observation.results)]
                self.screen.select_location(
                    failed.name if isinstance(failed, GlobalPage) else failed.section
                )
                self.screen.show_page(failed)
                self.screen.error(observation.required_error)
            else:
                self.screen.choose(selected)
                self.screen.status = self.screen.t(MessageKey.SETTINGS_SAVED)
                self.prompt_restart(tuple(PreferenceSection))

        self.screen.call(SettingsOperation.SAVE_ALL, requests, completed=saved)

    def prompt_restart(self, sections):
        from ...config.restart import restart_changes

        changed = [
            name
            for section in sections
            for name in restart_changes(
                section,
                self._preferences.section_or(section, {}),
                self._applied_preferences.section_or(section, {}),
                anchor=self.workspace_anchor,
            )
        ]
        if not changed or self.on_restart is self.NO_RESTART:
            return
        self.screen.view.open_dialog(
            self.screen.t(MessageKey.SETTINGS_RESTART_TITLE),
            Label(
                self.screen.t(
                    MessageKey.SETTINGS_RESTART_CONFIRM,
                    fields=self.RESTART_SEPARATOR.join(changed),
                )
            ),
            self.on_restart,
            accept_text=self.screen.t(MessageKey.SETTINGS_RESTART_NOW),
        )

    def discard(self):
        if self.screen.busy:
            return
        self.screen.navigation.finish_edit()
        self.screen.view.set_theme(self._preferences.theme_or(self.initial_appearance))
        selected = self.screen.selected
        project = self.pages.get(self.current_project_id)
        for key in (*self.screen.global_keys, self.current_project_id):
            self.pages.pop(key, self.NO_PAGE)
        if project is not self.NO_PAGE:
            self.pages[self.current_project_id] = ProjectPage(
                self.screen, project.record
            )
        self.screen.reset_draft_page()
        self.screen.choose(selected)
        self.screen.status = self.screen.t(MessageKey.SETTINGS_DISCARDED)

    def reload(self, page):
        if self.screen.busy:
            return
        self.screen.navigation.finish_edit()
        if isinstance(page, GlobalPage):

            def loaded(value):
                if value is not self.NO_RESULT:
                    observation = SettingsCatalog.observe(value)
                    self._preferences.update(observation.preferences)
                    if self.saved_sidebar_width is not self.NO_WIDTH:
                        self._preferences.set_sidebar(
                            self.saved_sidebar_width, self.initial_appearance.copy()
                        )
                    self.screen.usage = observation.usage
                    self.cancel(page)

            if self.screen.request:
                self.screen.call(SettingsOperation.CATALOG, completed=loaded)
            else:
                self.cancel(page)
        else:
            self.screen.call(
                SettingsOperation.LOAD_PROJECT,
                page.identifier,
                completed=self.screen.loaded_project,
            )

    def cancel(self, page):
        if self.screen.busy:
            return
        self.screen.navigation.finish_edit()
        if isinstance(page, GlobalPage):
            values = self._preferences.section_or(page.name, page.original)
            if page.name == PreferenceSection.APPEARANCE:
                values = asdict(HubTheme(**values))
                self.screen.view.set_theme(HubTheme(**values))
            self.pages[page.name] = GlobalPage(self.screen, page.name, values)
            self.screen.show_page(self.pages[page.name])
        else:
            self.screen.loaded_project(page.record)
        self.screen.status = self.screen.t(MessageKey.SETTINGS_CANCELLED)
