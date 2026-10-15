"""Shared sidebar identity and page navigation, independent of backend handles."""

from getpass import getuser

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl

from .controls import Button
from .focus_region import FocusRegion


class ProfileBar(HSplit):
    # Two eight-cell buttons, their gap and horizontal padding.
    minimum_width = 19

    def __init__(self, view):
        self.view = view
        self.account = getuser()
        self.settings = Button(view.t("profile_settings"), handler=lambda: view.open_page(True), width=8)
        self.home = Button(view.t("profile_home"), handler=lambda: view.open_page(False), width=8)
        self.buttons = (self.settings, self.home)
        heading = Window(FormattedTextControl(self.identity), height=1, style="class:hub.profile")
        padding = [Window(height=1), Window(height=1)]
        for window in (heading, *padding):
            window.hub_click_focus = self.focus
        content = HSplit([heading, padding[0], VSplit([
            Window(width=1), self.settings, Window(width=1), self.home, Window()], height=1), padding[1]], height=4)
        # Register the entire rectangle for clicks, without adding another tint layer.
        super().__init__([FocusRegion(content, active=lambda: self.focused, on_click=self.focus, tint=False)], height=4)
        for button in self.buttons:
            registry = button.control.hub_shortcuts
            registry.add(["left", "right", "tab", "s-tab"], "←→/Tab", "select", self.move)
            registry.add(["escape", "up"], "ESC/↑", "sidebar", lambda e: view.focus_sidebar())

    @property
    def controls(self):
        return tuple(button.control for button in self.buttons)

    @property
    def focused(self):
        return get_app().layout.current_control in self.controls

    def identity(self):
        name = self.view.user_profile.display_name.replace("\n", " ")
        return [("", f" {name} • {self.account}")]

    def focus(self):
        self.view.settings.finish_edit()
        self.view._session_focus = True
        get_app().layout.focus(self.home if self.view.settings_open else self.settings)
        get_app().invalidate()

    def move(self, event):
        current = self.controls.index(event.app.layout.current_control)
        step = -1 if event.key_sequence[-1].key in ("left", "s-tab") else 1
        event.app.layout.focus(self.buttons[(current + step) % len(self.buttons)])
