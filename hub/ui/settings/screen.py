"""Settings navigation: two panels with fields and actions."""

from dataclasses import asdict, replace

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import DynamicContainer, HSplit, VSplit, Window
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Frame, Label

from ...config.profile import UserProfile
from ...config.general import GeneralSettings
from .pages import GlobalPage, ProjectPage
from ...config.theme import HubTheme
from ...widget.controls import TextArea
from ...widget.forms.settings_viewport import SettingsPane
from .navigation import SettingsNavigation
from .drafts import SettingsDrafts
from .project_actions import SettingsProjectActions
from ...widget.sidebar import SidebarHeading, SidebarItem, SidebarList
from ..layout import TwoPanelPage
from ...widget.header import HeaderBar


class SettingsScreen:
    def __init__(self, view):
        self.view, self.t = view, view.t
        self.navigation = SettingsNavigation(self)
        self.drafts = SettingsDrafts(self)
        self.project_actions = SettingsProjectActions(self)
        self.request = None
        self.busy = False
        self.status = ""
        self.schema = None
        self.projects = []
        self.usage = []
        self.global_keys = ("profile", "appearance", "general")
        self.selected = "profile"
        self.page = None
        self._pending_selection = None
        self._engine_target = None
        self._component_target = None
        self._left = HSplit([])
        self.global_list = SidebarList(
            lambda: [SidebarItem(key, self._caption(key, self.t("settings_" + key))) for key in self.global_keys],
            lambda: self.navigation.left_key, self.select_left)
        self.project_list = SidebarList(
            lambda: [SidebarItem(item["id"], self._caption(item["id"], item["title"])) for item in self.projects]
                    or [SidebarItem("new", self.t("settings_empty"))],
            lambda: self.navigation.left_key, self.select_left)
        self.main = SettingsPane(DynamicContainer(lambda: self.page.container if self.page else
                                  Window(height=1)), show_scrollbar=True,
                                  width=lambda: max(1, self.main_width() - 1))
        bottom = DynamicContainer(lambda: self.page.bottom if self.page else Label(""))
        self.actions = Frame(HSplit([Window(height=1), bottom, Window(height=1)]), width=self.main_width,
                             title=self.action_title,
                             height=lambda: max(5, 4 + len(self.page.button_rows())) if self.page else 5)
        self.layout = TwoPanelPage(
            header=HeaderBar(lambda: self.t("settings_title"), icons=lambda: view.icons),
            sidebar=DynamicContainer(lambda: self._left),
            main=HSplit([
                        VSplit([Window(width=1), self.main], width=self.main_width,
                              height=Dimension(min=3, weight=1)),
                        self.actions], width=self.main_width),
            progress=view.progress, notice=lambda: self.status,
            footer=view.shortcut_bar(),
            sidebar_controls=lambda: (self.global_list, self.project_list),
            focus_sidebar=self.navigation.switch_panel, focus_main=lambda: self.navigation.focus_zone(self.navigation.main_zone),
            sidebar_width=view.sidebar_width, resize_sidebar=self.set_sidebar_width)
        self.layout.main_region.on_click = self.layout.focus_main
        self.container = self.layout.container
        self._sidebar()

    def main_width(self):
        return max(1, self.view._columns() - self.view.sidebar_width() - 1)

    def action_title(self):
        from ...asset.icon import SELECTED
        title = next((item["title"] for item in self.projects if item["id"] == self.drafts.current_project_id),
                     self.view.project_title)
        active = not self.layout.sidebar_focused
        return [("class:hub.focused" if active else "", (SELECTED + " " if active else "  ")
                 + self.t("settings_current_project", title=title))]

    def _sidebar(self):
        previous = self.navigation.focused_key()
        if self.navigation.left_key not in self.navigation.left_items():
            self.navigation.left_key = "profile"
        self._left = HSplit([SidebarHeading(lambda: self.view.icons.settings + " " + self.t("settings_global")),
                             Window(self.global_list, height=len(self.global_keys)),
                             Window(height=1), SidebarHeading(lambda: self.view.icons.project + " " + self.t("settings_projects")),
                             Window(self.project_list)],
                            width=self.view.sidebar_width)
        if previous and self.view.visible and self.view.settings_open:
            get_app().layout.focus(self.navigation.left_control())

    def select_left(self, key):
        self.navigation.left_key = key
        if key not in (*self.global_keys, "new"):
            self.drafts.select_project(key)
        if self.busy:
            self._pending_selection = key
        elif key != "new":
            self.choose(key)
        get_app().invalidate()

    def _caption(self, key, title):
        page = self.drafts.pages.get(key)
        return ("* " if page and page.dirty else "") + title

    def filter(self, query):
        if self.page is None:
            return
        self.navigation.finish_edit()
        self.page.filter(query)
        self.main.vertical_scroll = 0
        self.navigation.positions = [0, 0]
        self.status = self.t("settings_search_active", query=query) if query else ""
        self.navigation.focus_zone(0)

    def search(self):
        if self.page is None or self.busy:
            return
        field = TextArea(text=self.page.query, height=1, multiline=False)
        def accept():
            self.filter(field.text)
        self.view.open_dialog(self.t("settings_search"), HSplit([
            Label(self.t("settings_search_hint")), field]), accept, field)
        def entered(_):
            self.view.close_dialog()
            accept()
            return True
        field.buffer.accept_handler = entered

    def open_engine(self):
        project = getattr(self.view, "project_id", "")
        if not project:
            self.view.toggle_settings()
            return
        self._engine_target = (project, self.view.engine)
        self.view.toggle_settings()
        if self.schema is not None and not self.busy:
            self.choose(project)

    def open_component(self, name):
        project = getattr(self.view, "project_id", "")
        if not project:
            return
        self._component_target = (project, name)
        if not self.view.settings_open:
            self.view.toggle_settings()
        if self.schema is not None and not self.busy:
            self.choose(project)

    def resize(self, delta):
        self.set_sidebar_width(self.view.theme.sidebar_width + delta)

    def set_sidebar_width(self, width, *, persist=True):
        if self.busy:
            return
        width = max(18, min(60, max(18, self.view._columns() // 2), width))
        if width == self.view.theme.sidebar_width and (not persist or width == self.drafts.saved_sidebar_width):
            return
        if width != self.view.theme.sidebar_width:
            self.view.set_theme(replace(self.view.theme, sidebar_width=width))
        if persist:
            self.drafts.saved_sidebar_width = width
        self.drafts.initial_appearance["sidebar_width"] = width
        self.drafts.preferences.setdefault("appearance", self.drafts.initial_appearance.copy())["sidebar_width"] = width
        page = self.drafts.pages.get("appearance")
        if page is not None:
            field = page.form.fields[("sidebar_width",)]
            field.original = width
            field.input.text = field.initial = str(width)
            page.form.original["sidebar_width"] = page.original["sidebar_width"] = width
        save = getattr(self.view, "on_sidebar_width", None)
        if save and persist:
            save(width)

    def error(self, error):
        self.status = self.t("error", error=error)
        get_app().invalidate()

    def call(self, operation, *args, completed=None):
        if self.busy:
            return
        if self.request is None:
            self.error(self.t("not_connected"))
            return
        self.busy = True
        self.status = ""
        progress = self.view.progress.start(self.t(
            "progress_settings_load" if operation in ("catalog", "load_project") else "progress_settings_work"))
        def done(value, error=None):
            self.view.progress.finish(progress)
            self.busy = False
            self.status = ""
            if error is not None:
                self.error(error)
            if completed:
                completed(value)
            if self._pending_selection is not None:
                selected, self._pending_selection = self._pending_selection, None
                self.select_left(selected)
            get_app().invalidate()
        try:
            self.request(operation, args, done)
        except Exception as error:
            done(None, error)

    def open(self):
        self.drafts.current_project_id = getattr(self.view, "project_id", "") or self.drafts.current_project_id
        if self.request is None:
            self.drafts.preferences = {"profile": asdict(UserProfile()), "appearance": self.drafts.initial_appearance.copy(),
                                "general": asdict(GeneralSettings())}
            self.choose("profile")
            self.status = self.t("preview_notice")
            return
        self.call("catalog", completed=self.loaded_catalog)

    def loaded_catalog(self, value):
        if value is None:
            return
        self.schema, self.projects = value["schema"], value["projects"]
        if self.drafts.current_project_id not in {item["id"] for item in self.projects}:
            self.drafts.current_project_id = value.get("active_project", "")
        self.drafts.preferences = value["preferences"]
        self.usage = value.get("usage", [])
        if "profile" in self.drafts.pages:
            page = self.drafts.pages["profile"]
            page.usage_output.text = page.usage_text()
        self.drafts.preferences.setdefault("general", asdict(GeneralSettings()))
        self.drafts.preferences["appearance"] = asdict(HubTheme(**self.drafts.preferences.get("appearance", self.drafts.initial_appearance)))
        if self.drafts.saved_sidebar_width is not None:
            self.drafts.preferences["appearance"]["sidebar_width"] = self.drafts.saved_sidebar_width
        self._sidebar()
        if self._engine_target:
            self.choose(self._engine_target[0])
        elif self._component_target:
            self.choose(self._component_target[0])
        elif self.page is None:
            self.choose("profile")

    def choose(self, key):
        if self.busy:
            return
        self.navigation.finish_edit()
        if key not in (*self.global_keys, "new"):
            self.drafts.select_project(key)
        self.selected = key
        if key in self.navigation.left_items():
            self.navigation.left_key = key
        if self.view.visible and self.navigation.focused_key() is not None:
            get_app().layout.focus(self.navigation.left_control())
        if key in self.drafts.pages:
            self.show_page(self.drafts.pages[key])
        elif key in self.global_keys:
            self.drafts.pages[key] = GlobalPage(self, key, self.drafts.preferences[key])
            self.show_page(self.drafts.pages[key])
        elif key == "new":
            if self.schema is None:
                self.error(self.t("not_connected"))
                return
            # A fresh project is not a copy of the current project's settings.
            # Missing fields stay blank; storage is resolved when creating it.
            record = {"schema": self.schema, "values": {"components": [], "config": {}}}
            self.drafts.pages[key] = ProjectPage(self, record, new=True)
            self.show_page(self.drafts.pages[key])
        else:
            self.call("load_project", key, completed=self.loaded_project)

    def show_page(self, page):
        left = self.navigation.focused_key() is not None
        self.page = page
        self.main.vertical_scroll = 0
        self.navigation.positions = [0, 0]
        if self._engine_target and getattr(page, "identifier", None) == self._engine_target[0]:
            _, engine = self._engine_target
            self._engine_target = None
            self.filter("config.parameters.engines." + page.engine_keys.get(engine, engine))
        if self._component_target and getattr(page, "identifier", None) == self._component_target[0]:
            _, name = self._component_target
            self._component_target = None
            self.filter("config.parameters.components." + name)
        if self.view.visible and self.view.settings_open and self.view._dialog is None and not left:
            self.navigation.focus_zone(self.navigation.main_zone)
        get_app().invalidate()

    def loaded_project(self, record):
        if record is not None:
            identifier = record["project"]["id"]
            query = getattr(self.drafts.pages.get(identifier), "query", "")
            page = self.drafts.pages[identifier] = ProjectPage(self, record)
            if query:
                page.filter(query)
            self.selected = identifier
            self.navigation.left_key = identifier
            if self.view.visible and self.navigation.focused_key() is not None:
                get_app().layout.focus(self.navigation.left_control())
            self.show_page(page)

    def saved_project(self, record):
        if record is None:
            return
        self.drafts.select_project(record["project"]["id"])
        self.loaded_project(record)
        identifier, title = record["project"]["id"], record["values"]["title"]
        self.projects = [item for item in self.projects if item["id"] != identifier] + [{"id": identifier, "title": title}]
        self._sidebar()
        self.status = self.t("settings_saved")

    def left_control(self):
        return self.navigation.left_control()

    def finish_edit(self):
        return self.navigation.finish_edit()

    def edit_field(self, field):
        return self.navigation.edit_field(field)

    def switch_panel(self):
        return self.navigation.switch_panel()

    def focus(self, reverse=False):
        return self.navigation.focus(reverse=reverse)

    def move(self, key):
        return self.navigation.move(key)

    def save_and_close(self):
        return self.drafts.save_and_close()

    def discard_and_close(self):
        return self.drafts.discard_and_close()

    def prompt_restart(self, sections):
        return self.drafts.prompt_restart(sections)

    def reload(self, page):
        return self.drafts.reload(page)

    def cancel(self, page):
        return self.drafts.cancel(page)

    def clone(self, page):
        return self.project_actions.clone(page)

    def rename(self, page):
        return self.project_actions.rename(page)

    def delete(self, page):
        return self.project_actions.delete(page)

    def project_action(self, key):
        return self.project_actions.perform(key)

    @property
    def actions_focused(self):
        return self.navigation.actions_focused

    @property
    def editing(self):
        return self.navigation.editing

    @property
    def project_focused(self):
        return self.navigation.project_focused
