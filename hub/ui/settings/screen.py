"""Settings navigation: two panels with fields and actions."""

from dataclasses import asdict, replace

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import DynamicContainer, HSplit, Window
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label

from ...config.profile import UserProfile
from ...config.general import GeneralSettings
from .pages import GlobalPage, ProjectPage
from ...config.theme import HubTheme
from ...widget.controls import TextArea
from ...widget.forms.settings_viewport import SettingsPane
from .navigation import SettingsNavigation
from .drafts import SettingsDrafts
from ...widget.sidebar import SidebarHeading, SidebarItem, SidebarList
from ..layout import TwoPanelPage
from ...widget.header import HeaderBar
from ...widget.actions import ActionFrame
from ...widget.content_inset import ContentInset


class SettingsScreen:
    def __init__(self, view):
        self.view, self.t = view, view.t
        self.navigation = SettingsNavigation(self)
        self.drafts = SettingsDrafts(self)
        from .json_editor import SettingsJsonEditor
        self.json_editor = SettingsJsonEditor(self)
        self.request = None
        self.busy = False
        self.status = ""
        self.query = ""
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
            self.project_items,
            lambda: self.navigation.left_key, self.select_left)
        self.main = SettingsPane(DynamicContainer(lambda: self.page.container if self.page else
                                  Window(height=1)), show_scrollbar=True,
                                  width=lambda: Dimension(min=1, preferred=max(1, self.main_width() - 1)))
        bottom = DynamicContainer(lambda: self.page.bottom if self.page else Label(""))
        self.actions = ActionFrame(bottom, width=self.main_dimension, title=self.action_title)
        from ...widget.auxiliary import AuxiliaryBar
        self.auxiliary = AuxiliaryBar([("filter", self.search), ("json", self.show_json)],
                                      lambda: view.icons, lambda: self.navigation.focus_zone(1))
        self.layout = TwoPanelPage(
            header=HeaderBar(lambda: self.t("settings_title"), icons=lambda: view.icons),
            sidebar=DynamicContainer(lambda: self._left),
            main=HSplit([
                        ContentInset(self.main, width=self.main_dimension,
                                     height=Dimension(min=3, weight=1)),
                        self.auxiliary, self.actions], width=self.main_dimension),
            progress=view.progress, notice=self.status_text, status_annotation=self.filter_label,
            footer=view.shortcut_bar(),
            sidebar_controls=lambda: (self.global_list, self.project_list, *view.profile_bar.controls),
            categories=(self.global_list, self.project_list),
            focus_sidebar=self.navigation.switch_panel, focus_main=lambda: self.navigation.focus_zone(self.navigation.main_zone),
            sidebar_width=view.sidebar_width, resize_sidebar=self.set_sidebar_width)
        self.layout.main_region.on_click = self.layout.focus_main
        self.container = self.layout.container
        self._sidebar()

    def main_width(self):
        return max(1, self.view._columns() - self.view.sidebar_width() - 1)

    def main_dimension(self):
        # The host may inset Hub's Float. The terminal width is a preference,
        # never a minimum that can make an otherwise usable page disappear.
        return Dimension(min=1, preferred=self.main_width())

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
        from ...widget.sidebar_panel import SidebarPanel
        self._left = SidebarPanel([HSplit([SidebarHeading(lambda: self.view.icons.settings + " " + self.t("settings_global")),
                             Window(self.global_list, height=len(self.global_keys)),
                             Window(height=1)]),
                             HSplit([SidebarHeading(lambda: self.view.icons.project + " " + self.t("settings_projects")),
                                     Window(self.project_list)])], self.view.profile_bar, width=self.view.sidebar_width,
                                     controls=(self.global_list, self.project_list))
        if previous and self.view.visible and self.view.settings_open:
            get_app().layout.focus(self.navigation.left_control())

    def select_left(self, key):
        self.navigation.left_key = key
        if self.busy:
            self._pending_selection = key
        else:
            self.choose(key)
        get_app().invalidate()

    def _caption(self, key, title):
        page = self.drafts.pages.get(key)
        return ("* " if page and page.dirty else "") + title

    def project_items(self):
        items = [SidebarItem("project:general", self.t("settings_general"))]
        page = self.drafts.pages.get(self.drafts.current_project_id)
        if isinstance(page, ProjectPage):
            items.extend(SidebarItem("engine:" + key, self.t("settings_engine_item", name=key)) for key in page.engine_forms)
            items.extend(SidebarItem("component:" + key, self.t("settings_component_item", name=key)) for key in page.component_forms)
        return items

    def project_changed(self, identifier):
        changed = identifier != self.drafts.current_project_id
        self.drafts.select_project(identifier)
        if changed:
            self._engine_target = self._component_target = None
        if isinstance(self.page, ProjectPage) and self.page.identifier != identifier:
            self.page = None
            self.selected = self.navigation.left_key = "project:general"

    def filter(self, query):
        if self.page is None:
            return
        self.navigation.finish_edit()
        self.query = query.strip()
        self.page.filter(self.query)
        self.main.vertical_scroll = 0
        self.navigation.positions = [0, 0]
        self.status = ""
        self.navigation.focus_zone(0)

    def search(self):
        if self.page is None or self.busy:
            return
        field = TextArea(text=self.query, height=1, multiline=False)
        def accept():
            self.filter(field.text)
        self.view.open_dialog(self.t("settings_search"), HSplit([
            Label(self.t("settings_search_hint")), field]), accept, field)
        def entered(_):
            self.view.close_dialog()
            accept()
            return True
        field.buffer.accept_handler = entered

    def status_text(self):
        return " · ".join(part for part in (self.filter_label(), self.status) if part)

    def filter_label(self):
        return self.t("settings_search_active", query=self.query) if self.query else ""

    def show_json(self):
        self.json_editor.open()

    def open_engine(self, engine=None):
        project = getattr(self.view, "project_id", "")
        if not project:
            self.view.toggle_settings()
            return
        self.project_changed(project)
        self._engine_target = (project, engine or self.view.engine)
        if not self.view.settings_open:
            self.view.toggle_settings()
        if self.schema is not None and not self.busy:
            self.choose(project)

    def open_component(self, name):
        project = getattr(self.view, "project_id", "")
        if not project:
            return
        self.project_changed(project)
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
        minimum = self.view.profile_bar.minimum_width
        width = max(minimum, min(60, max(minimum, self.view._columns() // 2), width))
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
        self.project_changed(getattr(self.view, "project_id", "") or self.drafts.current_project_id)
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
        if self.drafts.current_project_id and self.drafts.current_project_id not in self.drafts.pages and not self.busy:
            def loaded(record):
                if record is not None:
                    self.drafts.pages[record["project"]["id"]] = ProjectPage(self, record)
                    self._sidebar()
            self.call("load_project", self.drafts.current_project_id, completed=loaded)

    def choose(self, key):
        if self.busy:
            return
        self.navigation.finish_edit()
        if key == self.drafts.current_project_id:
            key = "project:general"
        if key not in (*self.global_keys, *[item.key for item in self.project_items()]):
            return
        self.selected = key
        if key in self.navigation.left_items():
            self.navigation.left_key = key
        if self.view.visible and self.navigation.focused_key() is not None:
            get_app().layout.focus(self.navigation.left_control())
        if key in self.global_keys and key in self.drafts.pages:
            self.show_page(self.drafts.pages[key])
        elif key in self.global_keys:
            self.drafts.pages[key] = GlobalPage(self, key, self.drafts.preferences[key])
            self.show_page(self.drafts.pages[key])
        else:
            page = self.drafts.pages.get(self.drafts.current_project_id)
            if page is not None:
                page.set_section(key)
                self.show_page(page)
            elif self.drafts.current_project_id:
                self.call("load_project", self.drafts.current_project_id, completed=self.loaded_project)

    def show_page(self, page):
        left = self.navigation.focused_key() is not None
        self.page = page
        page.filter(self.query)
        self.main.vertical_scroll = 0
        self.navigation.positions = [0, 0]
        if self._engine_target and getattr(page, "identifier", None) == self._engine_target[0]:
            _, engine = self._engine_target
            self._engine_target = None
            section = "engine:" + page.engine_keys.get(engine, engine)
            if section in {item.key for item in self.project_items()}:
                page.set_section(section)
                self.selected = self.navigation.left_key = section
                self.filter("config.parameters.engines." + page.engine_keys.get(engine, engine))
        if self._component_target and getattr(page, "identifier", None) == self._component_target[0]:
            _, name = self._component_target
            self._component_target = None
            section = "component:" + name
            if section in {item.key for item in self.project_items()}:
                page.set_section(section)
                self.selected = self.navigation.left_key = section
                self.filter("config.parameters.components." + name)
        if self.view.visible and self.view.settings_open and self.view._dialog is None and not left:
            self.navigation.focus_zone(self.navigation.main_zone)
        get_app().layout.update_parents_relations()
        get_app().invalidate()

    def loaded_project(self, record):
        if record is not None:
            identifier = record["project"]["id"]
            query = getattr(self.drafts.pages.get(identifier), "query", "")
            section = getattr(self.drafts.pages.get(identifier), "section", self.selected)
            page = self.drafts.pages[identifier] = ProjectPage(self, record)
            section = section if section in {item.key for item in self.project_items()} else "project:general"
            page.set_section(section)
            if query:
                page.filter(query)
            self.selected = self.navigation.left_key = section
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

    def save(self):
        return self.drafts.save()

    def discard(self):
        return self.drafts.discard()

    def reload(self, page):
        return self.drafts.reload(page)

    def cancel(self, page):
        return self.drafts.cancel(page)

    @property
    def actions_focused(self):
        return self.navigation.actions_focused

    @property
    def editing(self):
        return self.navigation.editing

    @property
    def project_focused(self):
        return self.navigation.project_focused
