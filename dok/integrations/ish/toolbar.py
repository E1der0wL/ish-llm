"""Own only dok's toolbar registration; preserve later host/plugin changes."""
from prompt_toolkit.filters import Condition

from ...core.tasks import TaskSlot
from ...features.clock.runtime import ClockRuntime
from ...features.toolbar.view import ToolbarView
from ...rendering.toolbar import ToolbarControl
from .status import PromptStatus


class DockToolbar:
    def __init__(self, prompt, controller):
        self._prompt = prompt
        self._controller = controller
        self._previous = None
        self._enabled = False
        self._callback = self._render
        self._status = PromptStatus(prompt)
        self._view = ToolbarView()
        self._clock = ClockRuntime(prompt.app.invalidate)
        self._clock_task = TaskSlot(prompt.app.invalidate)
        self._dock_visible = False
        self._windows = []
        self._hint_default = controller.config.toolbar.hints
        self._hints_visible = self._hint_default

    def _restore_controls(self):
        for window, control, previous_wrap, owned_wrap in self._windows:
            if window.content is control:
                window.content = control.original
            if window.wrap_lines is owned_wrap:
                window.wrap_lines = previous_wrap
        self._windows.clear()

    def prepare(self):
        if not self.owned:
            self._restore_controls()
            return
        windows = [window for window in self._prompt.app.layout.find_all_windows()
                   if window.style == "class:bottom-toolbar"]
        if windows == [entry[0] for entry in self._windows]:
            return
        self._restore_controls()
        for window in windows:
            control = ToolbarControl(window.content, lambda: self.owned)
            previous_wrap = window.wrap_lines
            owned_wrap = Condition(lambda: self.owned) | previous_wrap
            self._windows.append((window, control, previous_wrap, owned_wrap))
            window.content = control
            window.wrap_lines = owned_wrap

    def _render(self):
        owner = self._controller
        return self._view.render(owner.config.toolbar, self._status.read(), owner.system,
            owner.system_history, owner.config.clock.now(), self._dock_visible,
            self._prompt.app.output.get_size().columns, theme=owner.config.theme, locale=owner.locale,
            hints=owner.toolbar_hints(), show_hints=self.hints_visible)

    @property
    def hints_visible(self):
        configured = self._controller.config.toolbar.hints
        if configured != self._hint_default:
            self._hint_default = self._hints_visible = configured
        return self._hints_visible

    def toggle_hints(self, event=None):
        self._hints_visible = not self.hints_visible
        self._prompt.app.invalidate()

    @property
    def owned(self):
        return self._enabled and self._prompt.bottom_toolbar is self._callback

    def configure(self, enabled):
        if enabled == self._enabled:
            return
        if enabled:
            self._previous = self._prompt.bottom_toolbar
            self._prompt.bottom_toolbar = self._callback
        elif self.owned:
            self._prompt.bottom_toolbar = self._previous
        self._enabled = enabled
        self.prepare()

    def sync(self, *, active, dock_visible):
        owner = self._controller
        if dock_visible != self._dock_visible:
            self._dock_visible = dock_visible
            if self.owned and owner.config.toolbar.date.hide_with_dock:
                self._prompt.app.invalidate()
        date = owner.config.toolbar.date
        schedule = date.schedule(owner.config.clock)
        self._clock_task.sync(active=active and self.owned and date.visible(dock_visible), key=schedule,
            run=lambda: self._clock.run(schedule), start=self._prompt.app.create_background_task)

    def close(self):
        self._clock_task.close()
        self.configure(False)
