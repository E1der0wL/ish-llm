"""Shared sidebar identity and page navigation, independent of backend handles."""
from hub.ui.input.registry import ShortcutRegistry


from hub.locales.keys import MessageKey

from getpass import getuser

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl

from .controls import Button
from ..config.theme_style import ThemeSelector
from ..ui.input.keys import KeyName, ShortcutAction, ShortcutLabel
from .focus_region import FocusRegion
from .actions import ActionBar
from .dropdown import DropdownBox


class ProfileBar(HSplit):
    # Page button, preset dropdown, gap and horizontal padding.
    minimum_width = 25
    PAGE_WIDTH = 8
    PRESET_WIDTH = 14
    HEIGHT = 4
    LINE_HEIGHT = 1
    CLICK_TINT = False
    NEWLINE = "\n"
    SPACE = " "
    IDENTITY_STYLE = ""
    IDENTITY_TEXT = " {name} • {account}"

    def __init__(self, view):
        self.view = view
        self.account = getuser()
        self.page = Button(
            lambda: view.t(
                MessageKey.PROFILE_HOME
                if view.settings_open
                else MessageKey.PROFILE_SETTINGS
            ),
            handler=lambda: view.open_page(not view.settings_open),
            width=self.PAGE_WIDTH,
        )
        from ..ui.settings.presets import PresetUI

        self.preset_ui = PresetUI(view)
        self.presets = DropdownBox(
            self.preset_ui.items,
            value=self.preset_ui.selected,
            placeholder=view.t(MessageKey.PRESET_TITLE),
            on_change=self.preset_ui.choose,
            width=self.PRESET_WIDTH,
            icons=lambda: view.icons,
        )
        self.buttons = (self.page, self.presets)
        heading = Window(
            FormattedTextControl(self.identity),
            height=self.LINE_HEIGHT,
            style=ThemeSelector.PROFILE.style,
        )
        padding = [Window(height=self.LINE_HEIGHT), Window(height=self.LINE_HEIGHT)]
        for window in (heading, *padding):
            window.hub_click_focus = self.focus
        content = HSplit(
            [
                heading,
                padding[0],
                ActionBar(self.buttons, view.sidebar_width),
                padding[1],
            ],
            height=self.HEIGHT,
        )
        # Register the entire rectangle for clicks, without adding another tint layer.
        super().__init__(
            [
                FocusRegion(
                    content,
                    active=lambda: self.focused,
                    on_click=self.focus,
                    tint=self.CLICK_TINT,
                )
            ],
            height=self.HEIGHT,
        )
        for button in self.buttons:
            registry = ShortcutRegistry.require(button.control)
            registry.add(
                [KeyName.TAB, KeyName.SHIFT_TAB],
                ShortcutLabel.HIDDEN,
                ShortcutAction.NONE,
                lambda e: None,
            )
            registry.add(
                [KeyName.ESCAPE],
                ShortcutLabel.ESCAPE,
                ShortcutAction.SIDEBAR,
                lambda e: view.focus_sidebar(),
            )

    @property
    def controls(self):
        return self.page.control, *self.presets.controls

    @property
    def focused(self):
        return get_app().layout.current_control in self.controls

    def identity(self):
        name = self.view.user_profile.display_name.replace(self.NEWLINE, self.SPACE)
        return [
            (
                self.IDENTITY_STYLE,
                self.IDENTITY_TEXT.format(name=name, account=self.account),
            )
        ]

    def focus(self):
        self.view.settings.finish_edit()
        self.view.mark_sidebar_focus()
        get_app().layout.focus(self.page)
        get_app().invalidate()
