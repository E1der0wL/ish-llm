"""Coordinate typed owners without interpreting their persisted dictionaries."""

from __future__ import annotations

from pathlib import Path
from datetime import date

from ..core.state import DockMode, DockState
from ..dashboard.view import DockView
from ..features.audio.models import AudioSnapshot
from ..features.audio.runtime import AudioRuntime
from ..features.media.models import PlaybackSnapshot
from ..features.media.runtime import MediaRuntime
from ..features.media.input import MediaInput
from ..features.system.runtime import SystemRuntime
from ..features.system.models import SystemSnapshot
from ..features.calendar.view import CalendarPage
from ..features.calendar.runtime import CalendarRuntime
from ..features.weather.popup import WeatherPage
from ..popups.models import PopupPage
from ..integrations.hub import HubAgents, RegistrationStatus
from ..integrations.ish.toolbar import DockToolbar
from ..integrations.ish.mouse import DockMouse
from ..integrations.ish.host import HostAccess
from ..integrations.ish.keys import KeyRegistration, OwnedBindings
from ..integrations.ish.submission import ShortcutSubmission
from ..integrations.ish.popups import PopupHost
from ..rendering.overlay import DockOverlay, OverlayLayers
from ..settings.config import DockConfig
from ..settings.store import ConfigStore
from .lifecycle import DashboardRuntime
from ..locales import Locale
from ..integrations.ish.language import HostLanguage


class DockController:
    INSTALLATION_ATTRIBUTE = "_dok_installation"

    def __init__(self, prompt, store: ConfigStore):
        self._prompt = prompt
        self._host = HostAccess(prompt)
        self._store = store
        self._state = DockState()
        self._config = DockConfig()
        self._playback = PlaybackSnapshot()
        self._audio = AudioRuntime(prompt.app.invalidate)
        self._media = MediaRuntime(prompt.app.invalidate)
        self._system = SystemRuntime(prompt.app.invalidate)
        self._toolbar = DockToolbar(prompt, self)
        self._media_input = MediaInput(self._media, prompt.app.create_background_task, prompt.app.invalidate,
                                      lambda: self._config.dock.mouse.enabled and self.visible)
        self._dashboard = DashboardRuntime(prompt.app.invalidate)
        self._hub = HubAgents(prompt)
        self._popups = PopupHost(prompt, lambda: self._config.popups, lambda: self._config.theme, locale=lambda: self.locale)
        self._calendar = CalendarRuntime(store.path.parent, prompt.app.invalidate, self._popups.notify)
        self._bindings = OwnedBindings(prompt, suspended=lambda: self._popups.modal)
        self._submission = ShortcutSubmission(prompt, self._state, locale=lambda: self.locale)
        self._view = DockView(self._mouse_execute, self._media_input)
        self._overlay = DockOverlay(self, self._view, locale=lambda: self.locale)
        self._surface = OverlayLayers(self._overlay, self._popups.layer)
        self._mouse = None
        self._float_handle = None

    @property
    def locale(self):
        return Locale.resolve(self._config.language or HostLanguage.current())

    def _mouse_execute(self, slot):
        if self._config.dock.mouse.enabled and self.visible and not self._media_input.dragging:
            return self.execute(slot)
        return False

    def _entries(self, config: DockConfig):
        entries = [KeyRegistration(config.dock.keys.toggle, lambda event: self.toggle(), self._toggle_enabled)]
        for owner, handler in ((config.popups.calendar, self.open_calendar), (config.popups.weather, self.open_weather)):
            if owner.enabled and owner.key is not None:
                entries.append(KeyRegistration(owner.key, lambda event, handler=handler: handler(), self._toggle_enabled))
        notifications = config.popups.notifications
        if notifications.enabled:
            for key, handler in ((notifications.dismiss_key, self._popups.dismiss_notification),
                                 (notifications.details_key, self._popups.open_details)):
                if key is not None:
                    entries.append(KeyRegistration(key, lambda event, handler=handler: handler(),
                                                   lambda: self._popups.notification_visible))
        if config.media.enabled and config.widgets.controls.enabled:
            for button in config.widgets.controls.keyed_buttons():
                def control(event, action=button.action):
                    if self._media_input.available(action):
                        self._media.request(self._media.state, action=action,
                                            start_task=self._prompt.app.create_background_task)
                entries.append(KeyRegistration(button.key, control, self._dock_keys_enabled))
        for shortcut in config.widgets.shortcuts.items:
            if shortcut.enabled and shortcut.key is not None:
                def execute(event, slot=shortcut.slot):
                    self.execute(slot)
                entries.append(KeyRegistration(shortcut.key, execute, self._shortcuts_enabled))
        return entries

    def _toggle_enabled(self) -> bool:
        app = self._prompt.app
        return not self._state.closed and app.is_running and not app.is_done and self._host.editing_shell()

    def _shortcuts_enabled(self) -> bool:
        return self._config.widgets.shortcuts.enabled and self._dock_keys_enabled()

    def _dock_keys_enabled(self) -> bool:
        return (self.visible and self._overlay.has_room
                and getattr(self._prompt.app.layout.current_control, "buffer", None) is self._prompt.default_buffer)

    def _before_render(self, app):
        self._host.observe_loop()
        self._state.enter_prompt(app.future)
        self._popups.prepare()
        self._toolbar.prepare()

    def _after_render(self, app):
        self._popups.after_render()
        active = app.is_running and not app.is_done
        if active:
            self._hub.sync()
            self._calendar.sync(self._config.popups.calendar, lambda: self._config.clock,
                                lambda: self._config.popups.notifications.enabled)
        self._toolbar.sync(active=active, dock_visible=self.display_mode != DockMode.HIDDEN)
        self._system.sync(active=active and self._toolbar.owned and self._config.toolbar.needs_system,
                          config=self._config.system, start_task=app.create_background_task)
        self._media.sync(active=self.visible and self.display_mode != DockMode.HIDDEN,
                         config=self._config.media, start_task=app.create_background_task)
        if self._mouse is not None:
            self._mouse.sync()
        self._audio.sync(active=self.visible and self.display_mode != DockMode.HIDDEN
                         and (self._config.widgets.visualizer.enabled or self._config.widgets.title.enabled),
                         config=self._config.audio, start_task=app.create_background_task)
        self._dashboard.sync(active=self.visible and self.display_mode == DockMode.MAXIMIZED,
                             config=self._config, app=app,
                             dock_active=self.visible,
                             weather_active=self._popups.modal and isinstance(self._popups.page, WeatherPage),
                             clock_active=self._popups.modal and isinstance(self._popups.page, (WeatherPage, CalendarPage)))

    def _mouse_active(self):
        return self._popups.modal or (
            self._config.dock.mouse.enabled and (
                self._popups.visible or self.visible and self._overlay.has_room))

    def _mouse_regions(self):
        popup = self._popups.layer.mouse_regions
        return popup if self._popups.modal else [*self._overlay.mouse_regions, *popup]

    def _change_mode(self, change):
        was_visible = self._state.requested_visible
        change()
        self._media_input.cancel()
        if not self.visible:
            self._media.sync(active=False, config=self._config.media,
                             start_task=self._prompt.app.create_background_task)
        if self._state.requested_visible and not was_visible:
            self._audio.begin_activation()
            self._dashboard.begin_activation()
        self._prompt.app.invalidate()

    def _attach(self):
        if self._state.closed:
            raise RuntimeError("dok has been uninstalled")
        if self._float_handle is not None:
            return
        self._mouse = DockMouse(self._prompt.app,
            self._mouse_active, self._mouse_regions, self._media_input.cancel)
        self._float_handle = self._prompt.set_float(
            self._surface, left=0, right=0, top=0, bottom=0,
            xcursor=False, ycursor=False, transparent=self._overlay.TRANSPARENT, hide_when_covering_content=False,
        )
        self._prompt.app.before_render += self._before_render
        self._prompt.app.after_render += self._after_render

    def _configure(self, config: DockConfig) -> None:
        if self._state.closed:
            raise RuntimeError("dok has been uninstalled")
        if not isinstance(config, DockConfig):
            raise TypeError("configure expects a DockConfig")
        self._bindings.replace(self._entries(config))
        self._media_input.cancel()
        if config.media != self._config.media:
            self._media.sync(active=False, config=config.media,
                             start_task=self._prompt.app.create_background_task)
        self._config = config
        self._toolbar.configure(config.toolbar.enabled)
        self._system.sync(active=self._prompt.app.is_running and not self._prompt.app.is_done
                          and self._toolbar.owned and config.toolbar.needs_system,
                          config=config.system, start_task=self._prompt.app.create_background_task)
        self._state.report("")
        self._prompt.app.invalidate()

    def _reload(self, store: ConfigStore) -> bool:
        try:
            self._configure(store.load_or_create())
        except (ValueError, OSError) as exc:
            self._state.report(exc)
            logger = getattr(self._prompt, "logger", None)
            if logger is not None:
                logger.warning("dok settings unchanged: %s", exc)
            self._prompt.app.invalidate()
            return False
        self._store = store
        self._hub.retry_failed()
        return True

    def _close(self):
        if self._state.closed:
            return
        self._state.close()
        self._popups.close()
        self._calendar.close()
        self._hub.close()
        self._audio.close()
        self._media.close()
        self._system.close()
        self._toolbar.close()
        if self._mouse is not None:
            self._mouse.close()
        self._dashboard.close()
        self._bindings.clear()
        self._prompt.app.before_render -= self._before_render
        self._prompt.app.after_render -= self._after_render
        if self._float_handle is not None:
            self._prompt.set_float(target_float=self._float_handle)
            self._float_handle = None
        if getattr(self._prompt, self.INSTALLATION_ATTRIBUTE, None) is self:
            delattr(self._prompt, self.INSTALLATION_ATTRIBUTE)
        self._prompt.app.invalidate()

    @property
    def config(self) -> DockConfig:
        return self._config

    @property
    def hub_registration(self) -> RegistrationStatus:
        """Last observed Hub registration state and any registration failure."""
        return self._hub.status

    @property
    def playback(self) -> PlaybackSnapshot:
        current = self._media.state
        return self._media_input.preview(current.playback if current.connected else self._playback)

    @property
    def media_players(self) -> tuple[str, ...]:
        return self._media.players

    @property
    def media_diagnostic(self) -> str:
        return self._media.diagnostic

    @property
    def system(self) -> SystemSnapshot:
        """Latest immutable Linux system snapshot; no I/O on access."""
        return self._system.snapshot

    @property
    def system_history(self) -> tuple[SystemSnapshot, ...]:
        """Bounded recent samples, oldest first."""
        return self._system.history

    @property
    def audio(self) -> AudioSnapshot:
        return self._audio.snapshot

    @property
    def diagnostic(self) -> str:
        return self._state.diagnostic

    @property
    def requested_visible(self) -> bool:
        return self._state.requested_visible

    @property
    def requested_mode(self) -> DockMode:
        return self._state.mode

    @property
    def display_mode(self) -> DockMode:
        return self._overlay.display_mode if self.visible else DockMode.HIDDEN

    @property
    def weather(self):
        return self._dashboard.snapshot_for(self._config)

    @property
    def visible(self) -> bool:
        app = self._prompt.app
        return (self._state.can_render(running=app.is_running, done=app.is_done)
                and (self._host.editing_shell() or self._popups.modal))

    @property
    def config_path(self) -> Path:
        return self._store.path

    def pty_columns(self) -> int:
        return self._host.pty_columns()

    def attach(self) -> DockController:
        self._host.dispatch(self._attach)
        return self

    def reload(self, store: ConfigStore | None = None) -> bool:
        return self._host.dispatch(lambda: self._reload(store or self._store))

    def configure(self, config: DockConfig) -> None:
        self._host.dispatch(lambda: self._configure(config))

    def save(self) -> None:
        """Persist only the already validated current configuration."""
        self._host.dispatch(lambda: self._store.save(self._config))

    def toggle(self) -> None:
        self._host.dispatch(lambda: self._change_mode(self._state.toggle))

    def toggle_size(self) -> None:
        self._host.dispatch(lambda: self._change_mode(self._state.toggle_size))

    def set_mode(self, mode: DockMode | str) -> None:
        mode = DockMode(mode)
        self._host.dispatch(lambda: self._change_mode(lambda: self._state.set_mode(mode)))

    def show(self, visible: bool = True) -> None:
        self._host.dispatch(lambda: self._change_mode(lambda: self._state.show(visible)))

    def execute(self, slot: int) -> bool:
        def submit():
            shortcut = self._config.widgets.shortcuts.shortcut(slot)
            return self._config.widgets.shortcuts.enabled and shortcut is not None and self._submission.execute(shortcut)
        return self._host.dispatch(submit)

    def set_playback(self, snapshot: PlaybackSnapshot) -> None:
        """Publish a cached snapshot; this does not connect to or control a player."""
        if not isinstance(snapshot, PlaybackSnapshot):
            raise TypeError("Expected PlaybackSnapshot")
        def update():
            if not self._state.closed:
                self._playback = snapshot
                self._prompt.app.invalidate()
        self._host.dispatch(update)

    def open_popup(self, page: PopupPage) -> bool:
        """Show one centered page; opening another replaces its contents."""
        return self._host.dispatch(lambda: self._popups.open(page))

    def close_popup(self) -> None:
        self._host.dispatch(self._popups.dismiss)

    def notify(self, title: str, text: str, *, details: PopupPage | None = None) -> bool:
        """Queue a non-focusing notification for the next available prompt."""
        return self._host.dispatch(lambda: self._popups.notify(title, text, details=details))

    def dismiss_notification(self) -> None:
        self._host.dispatch(self._popups.dismiss_notification)

    @property
    def pending_notifications(self) -> int:
        return self._popups.notifications.pending

    def open_calendar(self) -> bool:
        def show():
            config = lambda: self._config.popups.calendar
            return config().enabled and self._popups.open(
                CalendarPage(lambda: self._config.clock, config, self._calendar,
                             self._prompt.app.invalidate, self._popups.dismiss, locale=lambda: self.locale),
                panel=lambda: config().panel, enabled=lambda: config().enabled)
        return self._host.dispatch(show)

    def open_weather(self, day: date | None = None) -> bool:
        """Show cached daily details, sharing the dashboard weather runtime."""
        def show():
            config = lambda: self._config.popups.weather
            return config().enabled and self._popups.open(
                WeatherPage(lambda: self.weather, lambda: self._config.widgets.weather,
                            lambda: self._config.clock, day, config, self._prompt.app.invalidate, locale=lambda: self.locale),
                panel=lambda: config().panel, enabled=lambda: config().enabled)
        return self._host.dispatch(show)

    def uninstall(self) -> None:
        self._host.dispatch(self._close)
