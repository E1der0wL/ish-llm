"""Modal startup observation; recovery remains an explicit owner operation."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import (
    ConditionalContainer, DynamicContainer, HSplit, Window, WindowAlign, UIContent, UIControl,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label

from ...backend.progress import BackendProgress
from ...config.theme_style import ThemeSelector
from ...locales.keys import MessageKey as K
from ...ui.output.registry import literal_lines
from ..controls import Button
from ..dialog import Dialog, PopupSize
from ..feedback.progress import TaskProgress
from ..welcome import logo


class StartupProgress(TaskProgress):
    """Use the common Rich renderer, only while this popup is being painted."""

    def __init__(self, view):
        super().__init__(view, bar_only=True)
        self.running = True

    @property
    def active(self):
        return self.running and super().active


class StartupMessageControl(UIControl):
    """Wrap the startup message before applying the current state's alignment."""

    def __init__(self, owner):
        self.owner = owner
        self._key, self._lines = None, []

    def lines(self, width):
        paragraphs = tuple(self.owner.message())
        key = (width, paragraphs)
        if self._key != key:
            self._key = key
            self._lines = [row for style, text in paragraphs
                           for row in literal_lines(text, width, style)]
        return self._lines

    def preferred_height(self, width, max_available_height, wrap_lines, get_line_prefix):
        return min(max_available_height, len(self.lines(width)))

    def create_content(self, width, height):
        lines = self.lines(width)
        return UIContent(get_line=lambda i: lines[i], line_count=len(lines), show_cursor=False)


class StartupLoadingDialog(Dialog):
    CONTENT_COLUMNS = 56
    blocks_restart = True

    def __init__(self, view, retry):
        self.view, self.retry = view, retry
        self.issue = None
        self.busy, self.status, self.recovery_error = False, "", ""
        self.update = BackendProgress(K.PROGRESS_BACKEND_PREPARE)
        self.progress = StartupProgress(view)
        self.token = self.progress.start("")
        # Escape must not expose an uninitialized Home. Minimize preserves this
        # popup and the worker, using the normal Hub suspend/resume path.
        self.on_close = lambda: None
        self.retry_button = Button(view.t(K.STARTUP_RETRY),
            enabled=Condition(self.can_retry), handler=lambda: self.run(False))
        self.reset_button = Button(view.t(K.STARTUP_RESET),
            enabled=Condition(lambda: self.can_retry() and self.issue.reset is not None),
            handler=self.confirm_reset)
        self.minimize_button = Button(view.t(K.PROFILE_MINIMIZE), handler=view.minimize)
        self.first_button = self.minimize_button
        bar = self.progress.container(surface=self)
        bar.align = WindowAlign.CENTER
        loading = Condition(lambda: not self.showing_failure)
        status = view.progress.container(lambda: self.status, surface=self)
        empty_status = Window(height=1)
        super().__init__(
            title=view.t(K.STARTUP_LOADING), size=PopupSize.CUSTOM, modal=True,
            body=HSplit([
                Window(StartupMessageControl(self),
                       align=lambda: WindowAlign.LEFT if self.showing_failure else WindowAlign.CENTER,
                       wrap_lines=False,
                       dont_extend_height=True),
                ConditionalContainer(HSplit([
                    bar,
                    Window(FormattedTextControl(self.stage, show_cursor=False),
                           align=WindowAlign.CENTER, wrap_lines=True,
                           dont_extend_height=True),
                ]), loading),
            ], width=Dimension(preferred=self.CONTENT_COLUMNS, max=self.CONTENT_COLUMNS)),
            buttons=[self.retry_button, self.reset_button, self.minimize_button],
            status_bar=DynamicContainer(lambda: empty_status if self.showing_failure else status),
        )

    @property
    def showing_failure(self):
        return self.issue is not None and not self.busy

    def can_retry(self):
        return bool(self.issue and self.issue.retry_allowed and self.retry and not self.busy)

    def message(self):
        if not self.showing_failure:
            return [(ThemeSelector.ACCENT.style, "\n".join(logo(self.CONTENT_COLUMNS)))]
        issue, t = self.issue, self.view.t
        cause = self.recovery_error or issue.error.detail or issue.error.message
        return [
            (ThemeSelector.ACCENT.style, t(K.STARTUP_FAILURE)),
            ("", cause),
            (ThemeSelector.MUTED.style, t(K.STARTUP_TARGET) + ": " + issue.target + "\n\n" + t(issue.hint)),
        ]

    def stage(self):
        update = self.update
        percent = (f"{min(100, max(0, update.completed / update.total * 100)):.0f}%"
                   if update.total else "—%")
        return self.view.t(K.STARTUP_PROGRESS, stage=self.view.t(update.key), percent=percent)

    def observe(self, update):
        self.update = update
        self.progress.observe(self.token, update)
        get_app().invalidate()

    def failed(self, issue):
        self.issue = issue
        self.progress.running = False
        if issue.stage != self.update.key:
            self.observe(BackendProgress(issue.stage))
        self.progress.close()
        self.progress.progress.stop_task(self.token)
        get_app().invalidate()

    def confirm_reset(self):
        if not self.can_retry() or self.issue.reset is None:
            return
        self.view.open_dialog(
            self.view.t(K.STARTUP_RESET),
            Label(self.view.t(K.STARTUP_RESET_CONFIRM, target=self.issue.target)),
            lambda: self.run(True), accept_text=self.view.t(K.STARTUP_RESET),
            size=PopupSize.MEDIUM, parent=self,
        )

    def run(self, reset):
        if not self.can_retry() or (reset and self.issue.reset is None):
            return
        self.busy, self.recovery_error, self.status = True, "", ""
        self.progress.running = True
        self.progress.progress.start_task(self.token)
        self.observe(BackendProgress(K.STARTUP_RESETTING if reset else K.PROGRESS_BACKEND_PREPARE))
        if self.view.visible:
            get_app().layout.focus(self.minimize_button)
        get_app().create_background_task(self._run(reset))

    async def _run(self, reset):
        try:
            await self.retry(self.issue, reset)
        except Exception as error:
            from ...errors.adapters import from_exception
            self.view.operation_diagnostics.record_view(self.view, from_exception(error))
            self.recovery_error = str(error) + "\n" + "\n".join(getattr(error, "__notes__", ()))
            self.failed(self.issue)
        finally:
            self.busy = False
            get_app().invalidate()

    def dispose(self):
        self.progress.running = False
        self.progress.close()
