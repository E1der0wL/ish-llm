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
        return self.view.visible and self.view._dialog is None and self.view.output.modal_container is None

    @property
    def dialog(self):
        return self.view.visible and self.view._dialog is not None

    @property
    def settings(self):
        return self.available and self.view.settings_open and not self.profile and not self.auxiliary

    @property
    def chat(self):
        return self.available and not self.view.settings_open and not self.profile and not self.auxiliary

    @property
    def auxiliary(self):
        return (self.view.settings.auxiliary if self.view.settings_open else self.view.auxiliary).focused

    @property
    def sidebar(self):
        return self.available and self.view.active_page.sidebar_focused and not self.profile

    @property
    def profile(self):
        return self.available and self.view.profile_bar.focused

    @property
    def composing(self):
        return self.chat and self.control is self.view.composer.control
