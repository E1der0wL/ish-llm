"""Shared sidebar identity and page navigation, independent of backend handles."""

from getpass import getuser

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl

from .controls import Button
from .focus_region import FocusRegion
from .actions import ActionBar
from .dropdown import DropdownBox


class ProfileBar(HSplit):
    # Page button, preset dropdown, gap and horizontal padding.
    minimum_width = 25

    def __init__(self, view):
        self.view = view
        self.account = getuser()
        self.page = Button(lambda: view.t("profile_home" if view.settings_open else "profile_settings"),
                           handler=lambda: view.open_page(not view.settings_open), width=8)
        from ..ui.settings.presets import PresetUI
        self.preset_ui = PresetUI(view)
        self.presets = DropdownBox(self.preset_ui.items, value="", placeholder=view.t("preset_title"),
                                   on_change=self.preset_ui.choose, width=14, icons=lambda: view.icons)
        self.buttons = (self.page, self.presets)
        heading = Window(FormattedTextControl(self.identity), height=1, style="class:hub.profile")
        padding = [Window(height=1), Window(height=1)]
        for window in (heading, *padding):
            window.hub_click_focus = self.focus
        content = HSplit([heading, padding[0], ActionBar(self.buttons, view.sidebar_width), padding[1]], height=4)
        # Register the entire rectangle for clicks, without adding another tint layer.
        super().__init__([FocusRegion(content, active=lambda: self.focused, on_click=self.focus, tint=False)], height=4)
        for button in self.buttons:
            registry = button.control.hub_shortcuts
            registry.add(["tab", "s-tab"], "", "", lambda e: None)
            registry.add(["escape"], "ESC", "sidebar", lambda e: view.focus_sidebar())

    @property
    def controls(self):
        return self.page.control, *self.presets.controls

    @property
    def focused(self):
        return get_app().layout.current_control in self.controls

    def identity(self):
        name = self.view.user_profile.display_name.replace("\n", " ")
        return [("", f" {name} • {self.account}")]

    def focus(self):
        self.view.settings.finish_edit()
        self.view._session_focus = True
        get_app().layout.focus(self.page)
        get_app().invalidate()
