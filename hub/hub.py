"""ish installation entry point for Hub preview and live LLM sessions."""

PLUGIN_META = {
    "name": "hub",
    "version": "0.1.0",
    "description": "Prompt-toolkit AI sessions backed by the llm plugin.",
    "author": "",
    "requirements": ["llm"],
    "dependencies": ["rich", "ascii-magic|ascii_magic", "pyfiglet", "pathspec"],
}

from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import ConditionalKeyBindings, merge_key_bindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import ConditionalContainer
from prompt_toolkit.styles import DynamicStyle, Style, merge_styles

from .ui.view import HubView
from .ui.input.keys import KeyName
from .ui.installation import InstallationSurface
from .config.environment import ShellEnvironment
from .config.theme import HubTheme
from .backend.runtime import HubConfig
from .config.profile import UserProfile
from .ui.terminal_screen import HubTerminalScreen


class HubInstallation:
    """Own only the float, styles and binding changes made by this installation."""

    ABSENT = None
    OPEN = False
    CLOSED = True
    EAGER_TOGGLE = True
    DEFAULT_PREVIEW = False
    INSTALLATION_ATTRIBUTE = "_hub_ui_preview"
    WRAPPED_ATTRIBUTE = "_hub_run_wrapped"
    WRAPPED = True
    UNWRAPPED = False

    def __init__(
        self, prompt, theme: HubTheme | None = ABSENT, config: HubConfig | None = ABSENT
    ) -> None:
        self.prompt = prompt
        self._launch_config = config
        self._launch_theme = theme
        self.controller = HubInstallation.ABSENT
        if config is not HubInstallation.ABSENT:
            from .ui.live import LiveController, LiveHubView
            from .config.preferences import configured_profile

            config = configured_profile(config)
            self.view = LiveHubView(theme, config.language, config.language_packs)
            self.controller = LiveController(self.view, config)
            self.controller.environment_provider = lambda: ShellEnvironment.from_prompt(
                prompt
            )
        else:
            self.view = HubView(theme=theme)
        # Clipboard helpers need the current shell DISPLAY/PATH as well as the
        # host's inherited terminal information. Never mutate process globals.
        self.view.clipboard_environment = (
            lambda: ShellEnvironment.clipboard_environment(prompt)
        )
        self._closed = HubInstallation.OPEN
        self._terminal_screen = HubTerminalScreen(prompt.app, self.view)
        self._style = prompt.style
        self._bindings = prompt.app.key_bindings
        self._previous_q = [
            binding
            for binding in prompt.key_bindings.bindings
            if binding.keys == (Keys.ControlQ,)
        ]
        prompt.style = merge_styles(
            [self._style or Style([]), DynamicStyle(lambda: self.view.style)]
        )
        self._installed_style = prompt.style
        # Application-level shell editing bindings also need to be inactive while
        # the modal is open. ESC from the sidebar returns to the shell.
        prompt.app.key_bindings = merge_key_bindings(
            [
                ConditionalKeyBindings(
                    self._bindings, filter=Condition(lambda: not self.view.visible)
                ),
                self.view.navigation_keys,
            ]
        )
        self._installed_bindings = prompt.app.key_bindings
        self.float_handle = prompt.set_float(
            self.view.container,
            top=InstallationSurface.EDGE,
            left=InstallationSurface.EDGE,
            right=InstallationSurface.EDGE,
            # ish's input container can be only one line tall. Read the PTY
            # height on every render instead of sizing against that container.
            height=lambda: prompt.app.output.get_size().rows,
            z_index=InstallationSurface.MAIN_LAYER,
        )
        prompt.set_key(
            KeyName.CONTROL_Q,
            handler=self.view.toggle,
            filter=Condition(lambda: not self.view.visible),
            eager=HubInstallation.EAGER_TOGGLE,
        )
        self.alert_handle = prompt.set_float(
            ConditionalContainer(
                self.view.toasts.container,
                Condition(lambda: not self.view.visible and self.view.toasts.active()),
            ),
            bottom=InstallationSurface.TOAST_MARGIN,
            right=InstallationSurface.TOAST_MARGIN,
            width=self.view.toasts.width,
            height=self.view.toasts.height,
            z_index=InstallationSurface.TOAST_LAYER,
        )
        self._bind_loop = lambda app: self.controller and self.controller.bind_app(app)
        prompt.app.before_render += self._bind_loop
        if self.controller:
            from .ui.restart import bind_restart

            bind_restart(self.controller, self._restart)

    async def _restart(self):
        import asyncio
        from .config.restart import restart_config

        config = await asyncio.to_thread(
            restart_config, self._launch_config, self.controller.config
        )
        await self.controller.aclose()
        self.close()
        replacement = install(self.prompt, theme=self._launch_theme, config=config)
        # Preserve the original launch defaults for subsequent restarts.
        replacement._launch_config = self._launch_config
        replacement.controller.bind_app(self.prompt.app)
        replacement.view.show(self.prompt.app)

    def set_theme(self, theme: HubTheme) -> None:
        """Apply UI colors without reinstalling or touching conversation state."""
        self.view.set_theme(theme)
        self.prompt.app.invalidate()

    def close(self) -> None:
        """Detach Hub without clearing other plugins' floats or keys."""
        if self._closed:
            return
        if self.controller:
            self.controller.close()
        else:
            self.view.close()
        self.view.hide(self.prompt.app)
        self._terminal_screen.close()
        self.prompt.set_key(self.view.toggle)
        if not any(
            binding.keys == (Keys.ControlQ,)
            for binding in self.prompt.key_bindings.bindings
        ):
            for binding in self._previous_q:
                self.prompt.key_bindings.add(
                    *binding.keys,
                    filter=binding.filter,
                    eager=binding.eager,
                    is_global=binding.is_global,
                    save_before=binding.save_before,
                    record_in_macro=binding.record_in_macro,
                )(binding.handler)
        self.prompt.set_float(target_float=self.float_handle)
        self.prompt.set_float(target_float=self.alert_handle)
        self.prompt.app.before_render -= self._bind_loop
        if self.prompt.app.key_bindings is self._installed_bindings:
            self.prompt.app.key_bindings = self._bindings
        if self.prompt.style is self._installed_style:
            self.prompt.style = self._style
        self._closed = HubInstallation.CLOSED


def install(
    prompt,
    *,
    theme: HubTheme | None = HubInstallation.ABSENT,
    config: HubConfig | None = HubInstallation.ABSENT,
    preview: bool = HubInstallation.DEFAULT_PREVIEW
) -> HubInstallation:
    """Install live Hub with default storage; preview=True opts out of the backend."""
    if not preview and config is HubInstallation.ABSENT:
        config = HubConfig()
    if not preview and config.postprocess is None:
        from dataclasses import replace
        config = replace(config, postprocess=True)
    previous = getattr(
        prompt, HubInstallation.INSTALLATION_ATTRIBUTE, HubInstallation.ABSENT
    )
    if previous is not HubInstallation.ABSENT:
        previous.close()
    installation = HubInstallation(prompt, theme, config)
    setattr(prompt, HubInstallation.INSTALLATION_ATTRIBUTE, installation)
    if config is not HubInstallation.ABSENT and not getattr(
        prompt, HubInstallation.WRAPPED_ATTRIBUTE, HubInstallation.UNWRAPPED
    ):
        original_run = prompt.run

        def run_with_hub_cleanup():
            try:
                return original_run()
            finally:
                current = getattr(
                    prompt,
                    HubInstallation.INSTALLATION_ATTRIBUTE,
                    HubInstallation.ABSENT,
                )
                if current is not HubInstallation.ABSENT:
                    current.close()

        # The reference CLI calls prompt.run() after loading .ishrc. Keep one
        # wrapper across reloads and close the current installation, not an old one.
        prompt.run = run_with_hub_cleanup
        setattr(prompt, HubInstallation.WRAPPED_ATTRIBUTE, HubInstallation.WRAPPED)
    return installation
