"""A single-selection dropdown with explicit commit and cancel semantics."""
from hub.ui.input.registry import ShortcutRegistry


from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, Float, FloatContainer
from prompt_toolkit.widgets import Frame

from .controls import Button, RadioList
from ..asset.icon import NERD


class DropdownBox(FloatContainer):
    def __init__(self, items, *, value=None, placeholder="", on_change=None, width=16, icons=lambda: NERD):
        self.items, self.value, self.placeholder = items, value, placeholder
        self.icons = icons
        self.on_change, self.expanded = on_change, False
        self.width = width
        self.button = Button(self.caption, handler=self.open, width=width)
        self.choices = RadioList([(None, "")])
        self.choices.window.height = 8
        keys = ShortcutRegistry.require(self.choices.control)
        keys.add(["enter", " "], "Enter/Space", "confirm", lambda e: self.commit())
        keys.add(["escape"], "ESC", "close", lambda e: self.close())
        keys.add(["tab", "s-tab"], "", "", lambda e: None)
        self.choices._handle_enter = self.commit
        super().__init__(self.button, floats=[Float(
            ConditionalContainer(Frame(self.choices), Condition(self.is_open)),
            # Explicit height lets the menu extend above a one-row button;
            # preferred height alone is clipped to the button's own row.
            bottom=1, left=0, width=max(width, 24),
            height=lambda: min(10, len(self.choices.values) + 2,
                               max(1, get_app().output.get_size().rows - 5)),
            z_index=400)], z_index=0)

    @property
    def control(self):
        return self.button.control

    @property
    def controls(self):
        return self.button.control, self.choices.control

    def values(self):
        return list(self.items() if callable(self.items) else self.items)

    def is_open(self):
        if self.expanded and get_app().layout.current_control not in self.controls:
            self.expanded = False
        return self.expanded

    def caption(self):
        from ..ui.output.labels import ellipsize
        label = next((label for key, label in self.values() if key == self.value), self.placeholder)
        return ellipsize(label, max(1, self.width - 4)) + " " + self.icons().dropdown

    def open(self):
        values = self.values()
        if not values:
            return
        self.choices.values = values
        self.choices._selected_index = next((i for i, (key, _) in enumerate(values) if key == self.value), 0)
        self.choices.current_value = self.value
        self.choices.window.height = min(8, len(values))
        self.expanded = True
        get_app().layout.focus(self.choices)
        get_app().invalidate()

    def close(self):
        self.expanded = False
        get_app().layout.focus(self.button)
        get_app().invalidate()

    def commit(self):
        value = self.choices.values[self.choices._selected_index][0]
        self.close()
        if self.on_change:
            self.on_change(value)
        else:
            self.value = value
