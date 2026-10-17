"""A reusable tab strip whose pages may be any PTK container or widget."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import DynamicContainer, HSplit
from prompt_toolkit.layout.layout import walk
from prompt_toolkit.layout.containers import to_container

from .actions import ActionBar
from .controls import Button
from ..ui.input.registry import ShortcutRegistry


class TabPage(HSplit):
    def __init__(self, tabs, *, selected=None, width=lambda: 80, on_change=None, on_enter=None, on_leave=None):
        # Each entry is (stable key, label, container).
        self.tabs = list(tabs)
        if not self.tabs or len({key for key, _, _ in self.tabs}) != len(self.tabs):
            raise ValueError("Tabs require unique keys and at least one page")
        self.selected = selected if selected is not None else self.tabs[0][0]
        if self.selected not in {key for key, _, _ in self.tabs}:
            raise ValueError("Unknown tab")
        self.on_change, self.on_enter, self.on_leave = on_change, on_enter, on_leave
        self.buttons = [Button(lambda key=key, label=label: ("• " if key == self.selected else "") + label,
                               handler=lambda key=key: self.enter(key)) for key, label, _ in self.tabs]
        bar = ActionBar(self.buttons, width)
        for button in self.buttons:
            # Replace the generic button hints with the tab-specific contract.
            button.control.hub_shortcuts.shortcuts.clear()
            button.control.hub_shortcuts.add(["left", "right"], "←→", "tabs",
                lambda e: self.move(-1 if e.key_sequence[-1].key == "left" else 1))
            button.control.hub_shortcuts.add(["enter", " "], "Enter/Space", "main", lambda e: self.enter())
        self.shortcuts = ShortcutRegistry()
        self.shortcuts.add(["escape"], "ESC", "tabs", lambda e: self.focus_tabs(), when=lambda: self.content_focused)
        super().__init__([bar, DynamicContainer(self.page)], key_bindings=self.shortcuts.bindings)

    @property
    def tabs_focused(self):
        return get_app().layout.current_control in [button.control for button in self.buttons]

    @property
    def content_focused(self):
        current = get_app().layout.current_control
        return any(getattr(item, "content", None) is current for item in walk(to_container(self.page())))

    def focus_tabs(self):
        if self.on_leave and self.content_focused:
            self.on_leave()
        index = next(i for i, (key, _, _) in enumerate(self.tabs) if key == self.selected)
        get_app().layout.focus(self.buttons[index])
        get_app().invalidate()

    def enter(self, key=None):
        if key is not None and key != self.selected:
            self.select(key)
            return
        if self.on_enter:
            self.on_enter()
        else:
            for item in walk(to_container(self.page())):
                control = getattr(item, "content", None)
                if control is not None and control.is_focusable():
                    get_app().layout.focus(control)
                    break
        get_app().invalidate()

    def move(self, direction):
        index = next(i for i, (key, _, _) in enumerate(self.tabs) if key == self.selected)
        self.select(self.tabs[max(0, min(len(self.tabs) - 1, index + direction))][0])

    def page(self):
        return next(page for key, _, page in self.tabs if key == self.selected)

    def select(self, key):
        index = next((i for i, (name, _, _) in enumerate(self.tabs) if name == key), None)
        if index is None:
            raise ValueError("Unknown tab")
        if self.on_leave and self.content_focused:
            self.on_leave()
        self.selected = key
        if self.on_change:
            self.on_change(key)
        self.focus_tabs()
