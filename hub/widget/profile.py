"""Shared sidebar identity and page navigation, independent of backend handles."""

from getpass import getuser

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl

from .controls import Button
from .focus_region import FocusRegion
from .actions import ActionBar


class ProfileBar(HSplit):
    # Two eight-cell buttons, their gap and horizontal padding.
    minimum_width = 19

    def __init__(self, view):
        self.view = view
        self.account = getuser()
        self.page = Button(lambda: view.t("profile_home" if view.settings_open else "profile_settings"),
                           handler=lambda: view.open_page(not view.settings_open), width=8)
        self.minimize = Button(view.t("profile_minimize"), handler=view.minimize, width=8)
        self.buttons = (self.page, self.minimize)
        heading = Window(FormattedTextControl(self.identity), height=1, style="class:hub.profile")
        padding = [Window(height=1), Window(height=1)]
        for window in (heading, *padding):
            window.hub_click_focus = self.focus
        content = HSplit([heading, padding[0], ActionBar(self.buttons, view.sidebar_width), padding[1]], height=4)
        # Register the entire rectangle for clicks, without adding another tint layer.
        super().__init__([FocusRegion(content, active=lambda: self.focused, on_click=self.focus, tint=False)], height=4)
        for button in self.buttons:
            registry = button.control.hub_shortcuts
            registry.add(["left", "right"], "←→", "select", self.move)
            registry.add(["tab", "s-tab", "up", "down"], "", "", lambda e: None)
            registry.add(["escape"], "ESC", "sidebar", lambda e: view.focus_sidebar())

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
        get_app().layout.focus(self.page)
        get_app().invalidate()

    def move(self, event):
        current = self.controls.index(event.app.layout.current_control)
        step = -1 if event.key_sequence[-1].key == "left" else 1
        event.app.layout.focus(self.buttons[(current + step) % len(self.buttons)])
