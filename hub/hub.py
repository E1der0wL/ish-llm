"""ish installation entry point for Hub preview and live LLM sessions."""

PLUGIN_META = {
    "name": "hub",
    "version": "0.1.0",
    "description": "Prompt-toolkit AI sessions backed by the llm plugin.",
    "author": "",
    "requirements": ["llm"],
    "dependencies": ["rich", "ascii-magic|ascii_magic", "pyfiglet"],
}

from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import ConditionalKeyBindings, merge_key_bindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import ConditionalContainer
from prompt_toolkit.styles import DynamicStyle, Style, merge_styles

from .ui.mockup import HubMockup
from .config.theme import HubTheme
from .backend.runtime import HubConfig
from .config.profile import UserProfile
from .ui.terminal_screen import HubTerminalScreen


class HubInstallation:
    """Own only the float, styles and binding changes made by this installation."""

    def __init__(self, prompt, theme: HubTheme | None = None, config: HubConfig | None = None) -> None:
        self.prompt = prompt
        self.controller = None
        if config is not None:
            from .ui.live import LiveController, LiveHubView
            from .config.preferences import configured_profile
            config = configured_profile(config)
            self.view = LiveHubView(theme, config.language, config.language_packs)
            self.controller = LiveController(self.view, config)
        else:
            self.view = HubMockup(theme)
        self._closed = False
        self._terminal_screen = HubTerminalScreen(prompt.app, self.view)
        self._style = prompt.style
        self._bindings = prompt.app.key_bindings
        self._previous_q = [binding for binding in prompt.key_bindings.bindings
                            if binding.keys == (Keys.ControlQ,)]
        prompt.style = merge_styles([self._style or Style([]), DynamicStyle(lambda: self.view.style)])
        self._installed_style = prompt.style
        # Application-level shell editing bindings also need to be inactive while
        # the modal is open. ESC from the sidebar returns to the shell.
        prompt.app.key_bindings = merge_key_bindings([
            ConditionalKeyBindings(self._bindings, filter=Condition(lambda: not self.view.visible)),
            self.view.navigation_keys,
        ])
        self._installed_bindings = prompt.app.key_bindings
        self.float_handle = prompt.set_float(
            self.view.container, top=0, left=0, right=0,
            # ish's input container can be only one line tall. Read the PTY
            # height on every render instead of sizing against that container.
            height=lambda: prompt.app.output.get_size().rows,
            z_index=100,
        )
        prompt.set_key("c-q", handler=self.view.toggle, filter=Condition(lambda: not self.view.visible), eager=True)
        self.alert_handle = prompt.set_float(
            ConditionalContainer(self.view.toasts.container,
                Condition(lambda: not self.view.visible and self.view.toasts.active())),
            bottom=1, right=1, width=self.view.toasts.width, height=self.view.toasts.height, z_index=110)
        self._bind_loop = lambda app: self.controller and self.controller.bind_app(app)
        prompt.app.before_render += self._bind_loop

    def set_theme(self, theme: HubTheme) -> None:
        """Apply UI colors without reinstalling or touching conversation state."""
        self.view.set_theme(theme)
        self.prompt.app.invalidate()

    def close(self) -> None:
        """Detach this mockup without clearing other plugins' floats or keys."""
        if self._closed:
            return
        self.view.output_renderers.close()
        if self.controller:
            self.controller.close()
        self.view.hide(self.prompt.app)
        self._terminal_screen.close()
        self.prompt.set_key(self.view.toggle)
        if not any(binding.keys == (Keys.ControlQ,)
                   for binding in self.prompt.key_bindings.bindings):
            for binding in self._previous_q:
                self.prompt.key_bindings.add(
                    *binding.keys, filter=binding.filter, eager=binding.eager,
                    is_global=binding.is_global, save_before=binding.save_before,
                    record_in_macro=binding.record_in_macro,
                )(binding.handler)
        self.prompt.set_float(target_float=self.float_handle)
        self.prompt.set_float(target_float=self.alert_handle)
        self.prompt.app.before_render -= self._bind_loop
        if self.prompt.app.key_bindings is self._installed_bindings:
            self.prompt.app.key_bindings = self._bindings
        if self.prompt.style is self._installed_style:
            self.prompt.style = self._style
        self._closed = True


def install(prompt, *, theme: HubTheme | None = None, config: HubConfig | None = None,
            preview: bool = False) -> HubInstallation:
    """Install live Hub with default storage; preview=True opts out of the backend."""
    if not preview and config is None:
        config = HubConfig()
    previous = getattr(prompt, "_hub_ui_preview", None)
    if previous is not None:
        previous.close()
    installation = HubInstallation(prompt, theme, config)
    prompt._hub_ui_preview = installation
    if config is not None and not getattr(prompt, "_hub_run_wrapped", False):
        original_run = prompt.run

        def run_with_hub_cleanup():
            try:
                return original_run()
            finally:
                current = getattr(prompt, "_hub_ui_preview", None)
                if current is not None:
                    current.close()

        # The reference CLI calls prompt.run() after loading .ishrc. Keep one
        # wrapper across reloads and close the current installation, not an old one.
        prompt.run = run_with_hub_cleanup
        prompt._hub_run_wrapped = True
    return installation
