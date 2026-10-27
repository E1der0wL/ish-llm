"""One-row icon actions shared by the composer and settings footer."""
from hub.ui.input.registry import ShortcutRegistry


from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import VSplit, Window
from prompt_toolkit.layout.dimension import Dimension

from ..config.theme_style import ThemeSelector, ThemeStyle
from ..ui.input.keys import KeyName, ShortcutAction, ShortcutLabel
from ..asset.icon import IconSet
from .controls import Button
from .actions import bind_button_navigation
from .focus import focus_control


class AuxiliaryButton(Button):
    """Compact icon action with an explicitly transparent terminal background."""

    WIDTH = 3

    def __init__(self, text, handler):
        super().__init__(text, handler=handler, width=self.WIDTH)
        original = self.window.style
        self.window.style = (
            lambda: (original() if callable(original) else original)
            + ThemeStyle.MODIFIER_SEPARATOR
            + ThemeSelector.AUXILIARY.style
        )


class AuxiliaryBar(VSplit):
    NO_PREVIOUS = None
    FLEX_WEIGHT = 1
    PADDING = 1
    HEIGHT = 1
    BUTTON_STRIDE = AuxiliaryButton.WIDTH + PADDING

    def __init__(self, actions, icons, restore):
        self.restore = restore
        self.buttons = {}
        self.previous = self.NO_PREVIOUS
        for name, handler in actions:
            button = AuxiliaryButton(
                lambda name=name: IconSet.action(icons(), name), handler=handler
            )
            self.buttons[name] = button
            registry = ShortcutRegistry.require(button.control)
            registry.add(
                [KeyName.ESCAPE],
                ShortcutLabel.ESCAPE,
                ShortcutAction.RETURN,
                lambda e: self.leave(),
            )
            registry.add(
                [KeyName.TAB, KeyName.SHIFT_TAB],
                ShortcutLabel.HIDDEN,
                ShortcutAction.NONE,
                lambda e: None,
            )
        bind_button_navigation(tuple(self.buttons.values()))
        super().__init__(
            [
                Window(
                    width=Dimension(weight=self.FLEX_WEIGHT),
                    style=ThemeSelector.TRANSPARENT.style,
                ),
                VSplit(
                    list(self.buttons.values()),
                    padding=self.PADDING,
                    style=ThemeSelector.AUXILIARY.style,
                    width=len(self.buttons) * self.BUTTON_STRIDE - self.PADDING,
                ),
            ],
            height=self.HEIGHT,
            style=ThemeSelector.TRANSPARENT.style,
        )

    @property
    def focused(self):
        return get_app().layout.current_control in [
            button.control for button in self.buttons.values()
        ]

    def focus(self):
        app = get_app()
        self.previous = app.layout.current_control
        app.layout.focus(next(iter(self.buttons.values())))
        app.invalidate()

    def leave(self):
        app = get_app()
        if self.previous in list(app.layout.find_all_controls()):
            focus_control(self.previous)
        else:
            self.restore()
        app.invalidate()
