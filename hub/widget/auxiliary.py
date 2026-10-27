"""One-row icon actions shared by the composer and settings footer."""
import asyncio
from time import monotonic

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
    ACTIVATION_FEEDBACK_SECONDS = 0.15

    def __init__(self, text, handler):
        self._pressed_until = 0.0

        def activate():
            self._feedback()
            if handler:
                return handler()

        super().__init__(text, handler=activate, width=self.WIDTH)
        original = self.window.style
        self.window.style = (
            lambda: (original() if callable(original) else original)
            + ThemeStyle.MODIFIER_SEPARATOR
            + ThemeSelector.AUXILIARY.style
        )

    def _feedback(self):
        self._pressed_until = monotonic() + self.ACTIVATION_FEEDBACK_SECONDS
        app = get_app()
        app.invalidate()
        # Terminals do not report keyboard release; show the same short
        # activation feedback for Enter, Space and a mouse click.
        asyncio.get_running_loop().call_later(self.ACTIVATION_FEEDBACK_SECONDS, app.invalidate)

    def _get_text_fragments(self):
        from prompt_toolkit.mouse_events import MouseButton, MouseEventType

        fragments = super()._get_text_fragments()

        def mouse(event):
            if event.button == MouseButton.LEFT:
                if event.event_type == MouseEventType.MOUSE_DOWN:
                    self._feedback()
                elif event.event_type == MouseEventType.MOUSE_UP:
                    self.handler()

        pressed = monotonic() < self._pressed_until
        result = []
        for fragment in fragments:
            if len(fragment) != 3:
                result.append(fragment)
                continue
            style, text, _ = fragment
            if pressed and style == 'class:button.text':
                style += ' ' + ThemeSelector.AUXILIARY_PRESSED.style
            result.append((style, text, mouse))
        return result


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
