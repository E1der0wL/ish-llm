"""Settings navigation: two panels with fields and actions."""

from dataclasses import asdict
import subprocess

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import DynamicContainer, HSplit, Window
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Frame, Label

from ...config.profile import UserProfile
from ...config.general import GeneralSettings
from .pages import GlobalPage, ProjectPage
from ...widget.section import section
from ...config.theme import HubTheme
from ...widget.controls import TextArea
from ...widget.settings_viewport import SettingsPane
from .editor import edit_text
from ...widget.sidebar import SidebarItem, SidebarList
from ..layout import TwoPanelPage
from ...widget.header import HeaderBar


def control(widget):
    return getattr(widget, "control", widget)


class SettingsScreen:
    def __init__(self, view):
        self.view, self.t = view, view.t
        self.request = None
        self.busy = False
        self.status = ""
        self.schema = None
        self.projects = []
        self.preferences = {}
        self.usage = []
        self.global_keys = ("profile", "appearance", "general")
        self._initial_appearance = asdict(view.theme)
        self.pages = {}
        self.selected = "profile"
        self.page = None
        self._main_zone = 0
        self._zone_positions = [0, 0]
        self._left_key = "profile"
        self._pending_selection = None
        self._left = HSplit([])
        self.global_list = SidebarList(
            lambda: [SidebarItem(key, self._caption(key, self.t("settings_" + key))) for key in self.global_keys],
            lambda: self._left_key, self.select_left)
        self.project_list = SidebarList(
            lambda: [SidebarItem(item["id"], self._caption(item["id"], item["title"])) for item in self.projects]
                    or [SidebarItem("new", self.t("settings_empty"))],
            lambda: self._left_key, self.select_left)
        self.main = SettingsPane(DynamicContainer(lambda: self.page.container if self.page else
                                  Window(height=1)), show_scrollbar=True,
                                  width=lambda: max(1, self.main_width() - 2))
        bottom = DynamicContainer(lambda: self.page.bottom if self.page else Label(""))
        self.layout = TwoPanelPage(
            header=HeaderBar(lambda: self.t("settings_title")),
            sidebar=DynamicContainer(lambda: self._left),
            main=HSplit([
                        Frame(self.main, width=self.main_width,
                              height=Dimension(min=3, weight=1)),
                        Frame(bottom, width=self.main_width,
                              height=lambda: 2 + len(self.page.button_rows()) if self.page else 3)], width=self.main_width),
            progress=view.progress, notice=lambda: self.status,
            footer=view.shortcut_bar(),
            sidebar_controls=lambda: (self.global_list, self.project_list),
            focus_sidebar=self.switch_panel, focus_main=lambda: self._focus_zone(self._main_zone))
        self.container = self.layout.container
        self._sidebar()

    def main_width(self):
        return max(1, self.view._columns() - self.view.sidebar_width() - 1)

    def _left_items(self):
        return [*self.global_keys, *([item["id"] for item in self.projects] or ["new"])]

    def left_control(self):
        return self.global_list if self._left_key in self.global_keys else self.project_list

    def _focused_key(self):
        current = get_app().layout.current_control
        if current == self.global_list:
            return self._left_key if self._left_key in self.global_keys else "profile"
        if current == self.project_list:
            return self._left_key if self._left_key not in self.global_keys else self._left_items()[len(self.global_keys)]
        return None

    def _sidebar(self):
        previous = self._focused_key()
        if self._left_key not in self._left_items():
            self._left_key = "profile"
        self._left = HSplit([section(self.t("settings_global")), Window(self.global_list, height=len(self.global_keys)),
                             Window(height=1), section(self.t("settings_projects")),
                             Window(self.project_list)],
                            width=self.view.sidebar_width)
        if previous and self.view.visible and self.view.settings_open:
            get_app().layout.focus(self.left_control())

    def select_left(self, key):
        self._left_key = key
        if self.busy:
            self._pending_selection = key
        elif key != "new":
            self.choose(key)
        get_app().invalidate()

    def _caption(self, key, title):
        page = self.pages.get(key)
        return ("* " if page and page.dirty else "") + title

    def _groups(self):
        return [self.page.body_widgets(), self.page.buttons] if self.page else [[], []]

    def _focus_zone(self, zone):
        widgets = self._groups()[zone]
        if widgets:
            self._main_zone = zone
            get_app().layout.focus(control(widgets[min(self._zone_positions[zone], len(widgets) - 1)]))
        get_app().invalidate()

    def _remember_zone(self):
        current = get_app().layout.current_control
        for zone, widgets in enumerate(self._groups()):
            controls = [control(widget) for widget in widgets]
            if current in controls:
                self._main_zone, self._zone_positions[zone] = zone, controls.index(current)
                break

    @property
    def editing(self):
        current = get_app().layout.current_control
        return bool(self.page and any(field.editing and field.input.control == current for field in self.page.all_fields()))

    @property
    def project_focused(self):
        key = self._focused_key()
        return key is not None and key not in self.global_keys

    def finish_edit(self):
        editing = self.editing
        if self.page:
            for field in self.page.all_fields():
                field.finish()
        return editing

    def edit_field(self, field):
        if self.busy or field.readonly:
            return
        self.busy = field.editing = True
        get_app().create_background_task(self._edit_field(field))

    async def _edit_field(self, field):
        try:
            kind = field.schema.get("type")
            kinds = kind if isinstance(kind, list) else [kind]
            field.input.text = await edit_text(field.input.text, self.view.general,
                                               suffix=".txt" if "string" in kinds else ".json")
            self.status = ""
        except subprocess.CalledProcessError as error:
            self.error(self.t("settings_editor_failed", code=error.returncode))
        except Exception as error:
            self.error(error)
        finally:
            self.busy = False
            field.finish()

    def switch_panel(self):
        if self._focused_key() is not None:
            return
        self.finish_edit()
        self._remember_zone()
        get_app().layout.focus(self.left_control())

    def save_and_close(self):
        if self.page is not None and not self.busy:
            self.page.submit(on_saved=lambda: self.view.toggle_settings() if self.view.settings_open else None)

    def focus(self, reverse=False):
        if self._focused_key() is not None:
            self.layout.focus_main()
            return
        self.finish_edit()
        self._remember_zone()
        step = -1 if reverse else 1
        zone = self._main_zone
        for _ in range(2):
            zone = (zone + step) % 2
            if self._groups()[zone]:
                self._focus_zone(zone)
                break

    def move(self, key):
        current = get_app().layout.current_control
        usage = getattr(self.page, "usage_output", None)
        if usage is not None and current == usage.control and key in ("up", "down"):
            if key == "down" or usage.buffer.document.cursor_position_row:
                usage.buffer.cursor_up() if key == "up" else usage.buffer.cursor_down()
                return
        left_key = self._focused_key()
        if left_key is not None:
            if key in ("left", "right"):
                self.resize(-1 if key == "left" else 1)
                return
            items = self._left_items()
            index = items.index(left_key)
            index = max(0, min(len(items) - 1, index + (-1 if key == "up" else 1)))
            self._left_key = items[index]
            get_app().layout.focus(self.left_control())
            self.select_left(self._left_key)
        else:
            self._remember_zone()
            widgets = self._groups()[self._main_zone]
            if not widgets:
                return
            index = self._zone_positions[self._main_zone] + (-1 if key in ("up", "left") else 1)
            self._zone_positions[self._main_zone] = max(0, min(len(widgets) - 1, index))
            self._focus_zone(self._main_zone)

    def resize(self, delta):
        if self.busy:
            return
        if "appearance" not in self.pages:
            values = self.preferences.get("appearance", asdict(self.view.theme))
            self.pages["appearance"] = GlobalPage(self, "appearance", values)
        page = self.pages["appearance"]
        page.form.fields[("sidebar_width",)].input.text = str(max(18, min(60, self.view.theme.sidebar_width + delta)))
        self.status = self.t("settings_width_preview")

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
        if self.request is None:
            self.preferences = {"profile": asdict(UserProfile()), "appearance": self._initial_appearance.copy(),
                                "general": asdict(GeneralSettings())}
            self.choose("profile")
            self.status = self.t("preview_notice")
            return
        self.call("catalog", completed=self.loaded_catalog)

    def loaded_catalog(self, value):
        if value is None:
            return
        self.schema, self.projects = value["schema"], value["projects"]
        self.preferences = value["preferences"]
        self.usage = value.get("usage", [])
        if "profile" in self.pages:
            page = self.pages["profile"]
            page.usage_output.text = page.usage_text()
        self.preferences.setdefault("general", asdict(GeneralSettings()))
        self.preferences["appearance"] = asdict(HubTheme(**self.preferences.get("appearance", self._initial_appearance)))
        self._sidebar()
        if self.page is None:
            self.choose("profile")

    def choose(self, key):
        if self.busy:
            return
        self.finish_edit()
        self.selected = key
        if key in self._left_items():
            self._left_key = key
        if self.view.visible and self._focused_key() is not None:
            get_app().layout.focus(self.left_control())
        if key in self.pages:
            self.show_page(self.pages[key])
        elif key in self.global_keys:
            self.pages[key] = GlobalPage(self, key, self.preferences[key])
            self.show_page(self.pages[key])
        elif key == "new":
            if self.schema is None:
                self.error(self.t("not_connected"))
                return
            # A fresh project is not a copy of the current project's settings.
            # Missing fields stay blank; storage is resolved when creating it.
            record = {"schema": self.schema, "values": {"components": [], "config": {}}}
            self.pages[key] = ProjectPage(self, record, new=True)
            self.show_page(self.pages[key])
        else:
            self.call("load_project", key, completed=self.loaded_project)

    def show_page(self, page):
        left = self._focused_key() is not None
        self.page = page
        self.main.vertical_scroll = 0
        self._zone_positions = [0, 0]
        if self.view.visible and self.view.settings_open and self.view._dialog is None and not left:
            self._focus_zone(self._main_zone)
        get_app().invalidate()

    def loaded_project(self, record):
        if record is not None:
            identifier = record["project"]["id"]
            page = self.pages[identifier] = ProjectPage(self, record)
            self.selected = identifier
            self._left_key = identifier
            if self.view.visible and self._focused_key() is not None:
                get_app().layout.focus(self.left_control())
            self.show_page(page)

    def saved_project(self, record):
        if record is None:
            return
        self.loaded_project(record)
        identifier, title = record["project"]["id"], record["values"]["title"]
        self.projects = [item for item in self.projects if item["id"] != identifier] + [{"id": identifier, "title": title}]
        self.pages.pop("new", None)
        self._sidebar()
        self.status = self.t("settings_saved")

    def reload(self, page):
        if self.busy:
            return
        self.finish_edit()
        if isinstance(page, GlobalPage):
            def loaded(value):
                if value is not None:
                    self.preferences.update(value["preferences"])
                    self.usage = value.get("usage", [])
                    self.cancel(page)
            if self.request:
                self.call("catalog", completed=loaded)
            else:
                self.cancel(page)
        elif page.new:
            self.pages.pop("new", None)
            self.choose("new")
        else:
            self.call("load_project", page.identifier, completed=self.loaded_project)

    def cancel(self, page):
        if self.busy:
            return
        self.finish_edit()
        if isinstance(page, GlobalPage):
            values = self.preferences.get(page.name, page.original)
            if page.name == "appearance":
                values = asdict(HubTheme(**values))
                self.view.set_theme(HubTheme(**values))
            self.pages[page.name] = GlobalPage(self, page.name, values)
            self.show_page(self.pages[page.name])
        elif page.new:
            self.pages.pop("new", None)
            self.choose("profile")
        else:
            self.loaded_project(page.record)
        self.status = self.t("settings_cancelled")

    def project_action(self, key):
        if self.busy:
            return
        identifier = self._focused_key()
        if key == "c":
            self.choose("new")
            self._main_zone = 0
            self._focus_zone(0)
            return
        if identifier in (None, "new", *self.global_keys):
            return
        def ready(record):
            if record is None:
                return
            page = ProjectPage(self, record)
            {"d": self.delete, "e": self.rename, "r": self.clone}[key](page)
        self.call("load_project", identifier, completed=ready)

    def _name_dialog(self, title, initial, action):
        name = TextArea(text=initial, height=1, multiline=False)
        def validate():
            if name.text.strip():
                return True
            self.error(self.t("settings_title_required"))
            return False
        def accept():
            action(name.text.strip())
        self.view.open_dialog(title, HSplit([Label(self.t("settings_project_name")), name]), accept, name, validate=validate)
        def entered(_):
            if validate():
                self.view.close_dialog()
                accept()
            return True
        name.buffer.accept_handler = entered

    def clone(self, page):
        if not self.busy:
            self._name_dialog(self.t("settings_clone"), page.record["values"]["title"] + " " + self.t("settings_copy_suffix"),
                lambda title: self.call("clone_project", page.identifier, title, completed=self.saved_project))

    def rename(self, page):
        def renamed(record):
            if record is None:
                return
            identifier, title = record["project"]["id"], record["values"]["title"]
            for item in self.projects:
                if item["id"] == identifier:
                    item["title"] = title
            cached = self.pages.get(identifier)
            if cached is not None:
                field = cached.form.fields[("title",)]
                if field.input.text == field.initial:
                    field.input.text = title
                field.initial = field.original = title
                cached.record["values"]["title"] = title
            self._sidebar()
            self.status = self.t("settings_saved")
        self._name_dialog(self.t("settings_rename"), page.record["values"]["title"],
            lambda title: self.call("rename_project", page.identifier, title, completed=renamed))

    def delete(self, page):
        if self.busy:
            return
        def deleted(value):
            if value:
                self.pages.pop(page.identifier, None)
                self.projects = [item for item in self.projects if item["id"] != page.identifier]
                self._sidebar()
                self.choose("profile")
                self.status = self.t("settings_deleted")
        self.view.open_dialog(self.t("settings_delete"), Label(self.t("settings_delete_confirm",
            title=page.record["values"]["title"])),
            lambda: self.call("delete_project", page.identifier, completed=deleted))

    def activate(self, page):
        def activated(value):
            if value is not None:
                self.view.toggle_settings()
        self.call("activate_project", page.identifier, completed=activated)
