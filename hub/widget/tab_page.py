"""A reusable tab strip whose pages may be any PTK container or widget."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import DynamicContainer, HSplit
from prompt_toolkit.layout.layout import walk
from prompt_toolkit.layout.containers import to_container

from .actions import ActionBar
from .controls import Button
from .input_contract import ContainerControl
from ..ui.input.keys import KeyName, ShortcutAction, ShortcutLabel
from ..ui.input.registry import ShortcutRegistry


class TabPage(HSplit):
    KEY_POSITION = 0
    NO_SELECTION = None
    NO_INDEX = None
    NO_CALLBACK = None
    DEFAULT_WIDTH = 80
    INVALID_TABS = "Tabs require unique keys and at least one page"
    UNKNOWN_TAB = "Unknown tab"
    SELECTED_PREFIX = "• "
    UNSELECTED_PREFIX = ""

    def __init__(
        self,
        tabs,
        *,
        selected=NO_SELECTION,
        width=lambda: TabPage.DEFAULT_WIDTH,
        on_change=NO_CALLBACK,
        on_enter=NO_CALLBACK,
        on_leave=NO_CALLBACK
    ):
        # Each entry is (stable key, label, container).
        self.tabs = list(tabs)
        if not self.tabs or len({key for key, _, _ in self.tabs}) != len(self.tabs):
            raise ValueError(self.INVALID_TABS)
        self.selected = (
            selected if selected is not self.NO_SELECTION else self.key_at(0)
        )
        if self.selected not in {key for key, _, _ in self.tabs}:
            raise ValueError(self.UNKNOWN_TAB)
        self.on_change, self.on_enter, self.on_leave = on_change, on_enter, on_leave
        self.buttons = [
            Button(
                lambda key=key, label=label: (
                    self.SELECTED_PREFIX
                    if key == self.selected
                    else self.UNSELECTED_PREFIX
                )
                + label,
                handler=lambda key=key: self.enter(key),
            )
            for key, label, _ in self.tabs
        ]
        bar = ActionBar(self.buttons, width)
        for button in self.buttons:
            # Replace the generic button hints with the tab-specific contract.
            ShortcutRegistry.require(button.control).shortcuts.clear()
            ShortcutRegistry.require(button.control).add(
                [KeyName.LEFT, KeyName.RIGHT],
                ShortcutLabel.HORIZONTAL,
                ShortcutAction.TABS,
                lambda e: self.move(
                    -1 if e.key_sequence[-1].key == KeyName.LEFT else 1
                ),
            )
            ShortcutRegistry.require(button.control).add(
                [KeyName.ENTER, KeyName.SPACE],
                ShortcutLabel.ENTER_SPACE,
                ShortcutAction.MAIN,
                lambda e: self.enter(),
            )
        self.shortcuts = ShortcutRegistry()
        self.shortcuts.add(
            [KeyName.ESCAPE],
            ShortcutLabel.ESCAPE,
            ShortcutAction.TABS,
            lambda e: self.focus_tabs(),
            when=lambda: self.content_focused,
        )
        super().__init__(
            [bar, DynamicContainer(self.page)], key_bindings=self.shortcuts.bindings
        )

    @property
    def tabs_focused(self):
        return get_app().layout.current_control in [
            button.control for button in self.buttons
        ]

    @property
    def content_focused(self):
        current = get_app().layout.current_control
        return any(
            ContainerControl.resolve(item) is current
            for item in walk(to_container(self.page()))
        )

    def focus_tabs(self):
        if self.on_leave and self.content_focused:
            self.on_leave()
        index = next(
            i for i, (key, _, _) in enumerate(self.tabs) if key == self.selected
        )
        get_app().layout.focus(self.buttons[index])
        get_app().invalidate()

    def enter(self, key=NO_SELECTION):
        if key is not self.NO_SELECTION and key != self.selected:
            self.select(key)
            return
        if self.on_enter:
            self.on_enter()
        else:
            for item in walk(to_container(self.page())):
                control = ContainerControl.resolve(item)
                if control is not ContainerControl.ABSENT and control.is_focusable():
                    get_app().layout.focus(control)
                    break
        get_app().invalidate()

    def move(self, direction):
        index = next(
            i for i, (key, _, _) in enumerate(self.tabs) if key == self.selected
        )
        self.select(self.key_at(max(0, min(len(self.tabs) - 1, index + direction))))

    def key_at(self, index):
        """Interpret the key position only at the owned public tuple boundary."""
        return self.tabs[index][self.KEY_POSITION]

    def page(self):
        return next(page for key, _, page in self.tabs if key == self.selected)

    def select(self, key):
        index = next(
            (i for i, (name, _, _) in enumerate(self.tabs) if name == key), self.NO_INDEX
        )
        if index is self.NO_INDEX:
            raise ValueError(self.UNKNOWN_TAB)
        if self.on_leave and self.content_focused:
            self.on_leave()
        self.selected = key
        if self.on_change:
            self.on_change(key)
        self.focus_tabs()
