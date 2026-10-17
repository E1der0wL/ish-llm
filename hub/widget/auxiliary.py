"""One-row icon actions shared by the composer and settings footer."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import VSplit, Window
from prompt_toolkit.layout.dimension import Dimension

from .controls import Button
from .actions import bind_button_navigation
from .focus import focus_control


class AuxiliaryBar(VSplit):
    def __init__(self, actions, icons, restore):
        self.restore = restore
        self.buttons = {}
        self.previous = None
        for name, handler in actions:
            button = Button(lambda name=name: icons().auxiliary[name], handler=handler, width=3)
            self.buttons[name] = button
            registry = button.control.hub_shortcuts
            registry.add(["escape"], "ESC", "return", lambda e: self.leave())
            registry.add(["tab", "s-tab"], "", "", lambda e: None)
        bind_button_navigation(tuple(self.buttons.values()))
        super().__init__([Window(width=Dimension(weight=1), style="class:hub.transparent"),
                         VSplit(list(self.buttons.values()), padding=1,
                                style="class:hub.auxiliary", width=len(self.buttons) * 4 - 1)],
                         height=1, style="class:hub.transparent")

    @property
    def focused(self):
        return get_app().layout.current_control in [button.control for button in self.buttons.values()]

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
