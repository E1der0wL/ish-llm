"""Connect popup focus and visible-time notifications to an ish prompt."""
import asyncio

from ...popups.models import Notification, NotificationQueue, PopupContent, PopupPage
from ...rendering.popups import PopupLayer
from ...core.theme import Theme
from ...rendering.interactive import InteractivePopup
from ...locales import Locale


class PopupHost:
    def __init__(self, prompt, config, theme=Theme, *, locale=Locale):
        self._prompt, self._config = prompt, config
        self._theme = theme
        self._locale = locale
        self._page: PopupPage | None = None
        self._panel = None
        self._enabled = None
        self._previous = None
        self._session = None
        self._timer = None
        self._closed = False
        self._input_timing = {}
        self.notifications = NotificationQueue()
        self.layer = PopupLayer(self)

    def _editing(self):
        control = self._prompt.app.layout.current_control
        return getattr(control, "buffer", None) in (self._prompt.default_buffer, self._prompt.search_buffer)

    def _cancel_timer(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _wake(self):
        self._timer = None
        self.invalidate()

    def _sync_input_timing(self):
        app = self._prompt.app
        if self.modal:
            value = self.config.input.escape_s
            for name in ("ttimeoutlen", "timeoutlen"):
                current = getattr(app, name)
                previous = self._input_timing.get(name)
                if previous is None or current != previous[1]:
                    previous = (current, value)
                self._input_timing[name] = (previous[0], value)
                setattr(app, name, value)
        else:
            for name, (original, applied) in self._input_timing.items():
                if getattr(app, name) == applied:
                    setattr(app, name, original)
            self._input_timing.clear()

    @property
    def config(self):
        return self._config()

    @property
    def theme(self):
        return self._theme()

    @property
    def locale(self):
        return self._locale()

    @property
    def page(self):
        return self._page

    @property
    def modal(self):
        app = self._prompt.app
        return (self._page is not None and app.is_running and not app.is_done
                and app.layout.current_control is self.layer.general.control)

    @property
    def panel(self):
        return (self._panel() if self._panel else None) or self.config.general

    @property
    def notification_visible(self):
        app = self._prompt.app
        return (not self._closed and self.config.notifications.enabled and app.is_running and not app.is_done
                and self._editing() and self._page is None and self.notifications.current is not None)

    @property
    def visible(self):
        return self.modal or self.notification_visible

    def invalidate(self):
        self._prompt.app.invalidate()

    def open(self, page: PopupPage, *, panel=None, enabled=None):
        if self._closed:
            return False
        if not callable(getattr(page, "content", None)):
            raise TypeError("Popup page must provide content()")
        if isinstance(self._page, InteractivePopup) and not self._page.request_close():
            self.invalidate()
            return False
        if isinstance(self._page, InteractivePopup):
            self._page.dispose()
        self._page, self._panel, self._enabled = page, panel, enabled
        self.layer.general.page = page
        self.layer.general.reset()
        self.prepare()
        self.invalidate()
        return True

    def dismiss(self, *, force=False):
        if not force and isinstance(self._page, InteractivePopup) and not self._page.request_close():
            self.invalidate()
            return
        if isinstance(self._page, InteractivePopup):
            self._page.dispose()
        app = self._prompt.app
        if app.layout.current_control is self.layer.general.control:
            controls = list(app.layout.find_all_controls())
            target = self._previous if self._previous in controls else self._prompt.default_buffer
            app.layout.focus(target)
        self._page = self._panel = self._enabled = self._previous = self._session = None
        self.layer.general.page = None
        self._sync_input_timing()
        self.invalidate()

    def notify(self, title: str, text: str, *, details: PopupPage | None = None):
        if self._closed or not self.config.notifications.enabled:
            return False
        if details is not None and not callable(getattr(details, "content", None)):
            raise TypeError("Notification details must provide content()")
        self.notifications.add(Notification(PopupContent.plain(title, text), self.config.notifications.duration_s, details))
        self.invalidate()
        return True

    def dismiss_notification(self):
        self.notifications.dismiss()
        self.layer.notification.reset()
        self._cancel_timer()
        self.invalidate()

    def open_details(self):
        current = self.notifications.current
        if self.notification_visible and current.details is not None:
            details = current.details
            self.dismiss_notification()
            self.open(details)

    def prepare(self):
        app = self._prompt.app
        if self._closed:
            return
        if self._session is not None and (app.is_done or self._session is not app.future):
            self.dismiss(force=True)
        if self._enabled is not None and not self._enabled():
            self.dismiss(force=True)
        if self._page is not None and app.is_running and not app.is_done and self._editing():
            self._previous = app.layout.current_control
            self._session = app.future
            app.layout.focus(self.layer.general.control)
        self._sync_input_timing()
        if not self.config.notifications.enabled:
            self.notifications.clear()
        active = (app.is_running and not app.is_done and self._editing() and self._page is None
                  and self.config.notifications.enabled)
        previous = self.notifications.current
        if app.is_running:
            self.notifications.sync(active, asyncio.get_running_loop().time())
        if previous is not self.notifications.current:
            self.layer.notification.reset()
        self._cancel_timer()

    def after_render(self):
        if self._closed:
            return
        app = self._prompt.app
        if app.is_running:
            now = asyncio.get_running_loop().time()
            previous = self.notifications.current
            self.notifications.sync(self.notification_visible and self.layer.notification_painted, now)
            if previous is not self.notifications.current:
                self.layer.notification.reset()
                self.invalidate()
                return
            remaining = self.notifications.remaining
            if self.notification_visible and self.layer.notification_painted and remaining is not None:
                self._timer = asyncio.get_running_loop().call_later(remaining, self._wake)

    def close(self):
        self.dismiss(force=True)
        self._closed = True
        self._cancel_timer()
        self.notifications.clear()
