"""Keep a full-height Hub float off the shell's normal terminal buffer."""

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition


class HubTerminalScreen:
    def __init__(self, app: Application, view) -> None:
        self.app, self.view = app, view
        self._original: tuple[bool, bool] | None = None
        self._mouse_support = app.mouse_support
        self._renderer_mouse_support = app.renderer.mouse_support
        visible = Condition(lambda: self.view.visible)
        hub_mouse = visible & Condition(lambda: self.view.mouse_enabled)
        self._hub_mouse_support = hub_mouse | (~visible & self._mouse_support)
        self._hub_renderer_mouse_support = hub_mouse | (~visible & self._renderer_mouse_support)
        app.mouse_support = self._hub_mouse_support
        app.renderer.mouse_support = self._hub_renderer_mouse_support
        app.before_render += self._before_render
        app.after_render += self._after_render

    def _before_render(self, app: Application) -> None:
        if self.view.visible:
            if self._original is None and not app.renderer.full_screen:
                # Erase only PTK's current prompt region, keeping the output above
                # it. The next render enters the terminal's alternate buffer.
                app.renderer.erase()
                self._original = app.full_screen, app.renderer.full_screen
                app.full_screen = app.renderer.full_screen = True
        else:
            self._restore()

    def _after_render(self, app: Application) -> None:
        if app.is_done and self._original is not None:
            # A completed PTK render already left the alternate screen. Do not
            # erase the restored shell while releasing our mode ownership.
            self._restore()
            self.view.hide(app)

    def _restore(self) -> None:
        if self._original is None:
            return
        app = self.app
        running = app.is_running and not app.is_done
        if running:
            app.renderer.erase()
        else:
            app.renderer.reset()
        app.full_screen, app.renderer.full_screen = self._original
        self._original = None
        if running:
            app.renderer.request_absolute_cursor_position()

    def close(self) -> None:
        self._restore()
        if self.app.mouse_support is self._hub_mouse_support:
            self.app.mouse_support = self._mouse_support
        if self.app.renderer.mouse_support is self._hub_renderer_mouse_support:
            self.app.renderer.mouse_support = self._renderer_mouse_support
        self.app.before_render -= self._before_render
        self.app.after_render -= self._after_render
