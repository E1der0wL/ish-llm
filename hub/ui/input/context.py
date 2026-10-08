"""Read the actual focus rather than remembering a potentially stale region."""

from prompt_toolkit.application.current import get_app


class InputContext:
    def __init__(self, view):
        self.view = view

    @property
    def control(self):
        return get_app().layout.current_control

    @property
    def available(self):
        return self.view.visible and self.view._dialog is None and not self.view.tags.visible

    @property
    def dialog(self):
        return self.view.visible and self.view._dialog is not None

    @property
    def settings(self):
        return self.available and self.view.settings_open

    @property
    def chat(self):
        return self.available and not self.view.settings_open

    @property
    def sidebar(self):
        return self.available and self.view.active_page.sidebar_focused

    @property
    def composing(self):
        return self.chat and self.control is self.view.composer.control
