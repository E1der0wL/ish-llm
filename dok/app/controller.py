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
from ..locales import Locale, Message as M
from ..integrations.ish.language import HostLanguage
from ..integrations.ish.completion import CompletionHost
from ..integrations.ish.completion_menu import CompletionAppearance
from ..integrations.completion_agent import CompletionAgent
from ..features.completion.runtime import CompletionRuntime, Phase
from ..features.completion.progress import CompletionProgress
from ..features.completion.view import CompletionPage
from ..features.completion.management import CompletionManager
from ..connections import ConnectionRegistry
from ..features.calendar.service import CalendarService
from ..features.calendar.contracts import CalendarWindow
from .composition import connections


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
        self._hub = HubAgents(prompt, config=lambda: self._config.hub)
        self._popups = PopupHost(prompt, lambda: self._config.popups, lambda: self._config.theme, locale=lambda: self.locale,
            shortcuts=lambda: tuple(entry for entry in self._bindings.entries if entry.label == M.HINT_HELP))
        self._bindings = OwnedBindings(prompt, suspended=lambda: self._popups.modal)
        self._submission = ShortcutSubmission(prompt, self._state, locale=lambda: self.locale)
        self._completion_host = CompletionHost(prompt, self._submission)
        self._completion_appearance = CompletionAppearance(prompt)
        self._completion = CompletionRuntime(store.path.parent,
            lambda catalog: setattr(self._completion_host, "catalog", catalog), prompt.app.invalidate,
            CompletionAgent(self._hub, locale=lambda: self.locale), self._completion_finished,
            progressed=self._completion_progress)
        self._calendar = CalendarRuntime(store.path.parent, prompt.app.invalidate, self._popups.notify,
                                         submit=self._submission.execute_command, locale=lambda: self.locale)
        self._connections = ConnectionRegistry()
        self._calendars = CalendarService(self._calendar, self._connections, prompt.app.invalidate)
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
        entries = [KeyRegistration(config.dock.keys.toggle, lambda event: self.toggle(), self._toggle_enabled, M.HINT_DOCK)]
        if config.toolbar.hint_key is not None:
            entries.append(KeyRegistration(config.toolbar.hint_key, self._toolbar.toggle_hints, self._help_enabled, M.HINT_HELP))
        if config.completion.enabled and config.completion.key is not None:
            entries.append(KeyRegistration(config.completion.key, lambda event: self.open_completions(), self._completion_keys_enabled, M.HINT_REGISTER))
        if config.completion.enabled and config.completion.manage.key is not None:
            entries.append(KeyRegistration(config.completion.manage.key, lambda event: self.manage_completions(), self._toggle_enabled, M.HINT_MANAGE))
        for owner, handler in ((config.popups.calendar, self.open_calendar), (config.popups.weather, self.open_weather)):
            if owner.enabled and owner.key is not None:
                entries.append(KeyRegistration(owner.key, lambda event, handler=handler: handler(), self._toggle_enabled,
                                               M.CALENDAR if owner is config.popups.calendar else M.WEATHER))
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

    def _help_enabled(self) -> bool:
        return self._toolbar.owned and (self._toggle_enabled() or self._popups.modal)

    def _completion_keys_enabled(self) -> bool:
        return self._toggle_enabled() and getattr(self._prompt.app.layout.current_control, "buffer", None) is self._prompt.default_buffer

    def toolbar_hints(self):
        if not self._toolbar.hints_visible:
            return ()
        if self._popups.modal:
            return (self._popups.status_text(),)
        locale = self.locale
        result = []
        if (key := self._hub.toggle_key()) is not None:
            result.append(f"{key.label()} {locale.text(M.HINT_HUB)}")
        # The bindings own current keys and activation rules; hints never duplicate them.
        labels = {entry.label: entry for entry in self._bindings.entries if entry.label is not None and entry.enabled()}
        for label in (M.HINT_DOCK, M.CALENDAR, M.WEATHER, M.HINT_REGISTER, M.HINT_MANAGE, M.HINT_HELP):
            if (entry := labels.get(label)) is not None:
                result.append(f"{entry.sequence.label()} {locale.text(label)}")
        return tuple(result)

    def _dock_keys_enabled(self) -> bool:
        return (self.visible and self._overlay.has_room
                and getattr(self._prompt.app.layout.current_control, "buffer", None) is self._prompt.default_buffer)

    def _before_render(self, app):
        self._host.observe_loop()
        self._state.enter_prompt(app.future)
        self._popups.prepare()
        result = self._completion_host.observe()
        if result is not None and self._config.completion.enabled:
            self._completion.receive(result, self._config.completion)
        self._toolbar.prepare()

    def _after_render(self, app):
        self._popups.after_render()
        active = app.is_running and not app.is_done
        if active:
            self._hub.sync()
            if self._config.completion.enabled:
                self._completion.ensure_loaded()
            self._calendar.sync(self._config.popups.calendar, lambda: self._config.clock,
                                lambda: self._config.popups.notifications.enabled,
                                visible=self._popups.modal and isinstance(self._popups.page, CalendarPage))
        page = self._popups.page
        calendar_visible = active and self._popups.modal and isinstance(page, CalendarPage) and self._config.popups.calendar.enabled
        window = CalendarWindow(page.selected.year, page.selected.month, self._config.clock.timezone) if calendar_visible else None
        self._calendars.sync(self._config.popups.calendar.sources if self._config.popups.calendar.enabled else (), window,
                             start_task=app.create_background_task)
        dock_active = self.visible and self.display_mode != DockMode.HIDDEN and not self._popups.modal
        self._toolbar.sync(active=active, dock_visible=self.display_mode != DockMode.HIDDEN)
        self._system.sync(active=active and self._toolbar.owned and self._config.toolbar.needs_system,
                          config=self._config.system, start_task=app.create_background_task, idle=not dock_active)
        self._media.sync(active=dock_active,
                         config=self._config.media, start_task=app.create_background_task)
        if self._mouse is not None:
            self._mouse.sync()
        self._audio.sync(active=dock_active
                         and (self._config.widgets.visualizer.enabled or self._config.widgets.title.enabled),
                         config=self._config.audio, start_task=app.create_background_task)
        self._dashboard.sync(active=dock_active and self.display_mode == DockMode.MAXIMIZED,
                             config=self._config, app=app,
                             dock_active=dock_active,
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
        self._connections.configure(connections(config.connections, self._store.path.parent))
        self._completion_host.configure(config.completion.enabled)
        self._completion_appearance.configure(config.completion.menu, config.theme, enabled=config.completion.enabled)
        if not config.completion.enabled:
            self._completion.cancel_analysis()
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
        self._connections.configure(connections(self._config.connections, store.path.parent))
        self._hub.retry_failed()
        if self._config.completion.enabled and self._prompt.app.is_running:
            self._completion.load()
        return True

    def _close(self):
        if self._state.closed:
            return
        self._state.close()
        self._popups.close()
        self._calendar.close()
        self._calendars.close()
        self._completion.close()
        self._completion_host.close()
        self._completion_appearance.close()
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
                             self._prompt.app.invalidate, self._popups.dismiss, locale=lambda: self.locale,
                             calendar=self._calendars),
                panel=lambda: config().panel, enabled=lambda: config().enabled, opener=lambda: config().key)
        return self._host.dispatch(show)

    def open_weather(self, day: date | None = None) -> bool:
        """Show cached daily details, sharing the dashboard weather runtime."""
        def show():
            config = lambda: self._config.popups.weather
            return config().enabled and self._popups.open(
                WeatherPage(lambda: self.weather, lambda: self._config.widgets.weather,
                            lambda: self._config.clock, day, config, self._prompt.app.invalidate, locale=lambda: self.locale),
                panel=lambda: config().panel, enabled=lambda: config().enabled, opener=lambda: config().key)
        return self._host.dispatch(show)

    def open_completions(self) -> bool:
        """Open a cursor-anchored form; opening it never runs a lookup."""
        def show():
            if not self._config.completion.enabled or not self._completion_keys_enabled() or self._popups.modal:
                return False
            buffer = self._prompt.default_buffer
            document = buffer.document
            buffer.cancel_completion()
            buffer.set_document(document)
            self._completion.ensure_loaded()
            return self._popups.open(CompletionPage(self._completion, self._completion_host,
                self._run_completion_lookup, self._prompt.app.invalidate,
                command=document.text, locale=lambda: self.locale),
                panel=lambda: self._config.completion.panel, enabled=lambda: self._config.completion.enabled, cursor=True,
                opener=lambda: self._config.completion.key)
        return self._host.dispatch(show)

    def manage_completions(self) -> bool:
        def show():
            if not self._config.completion.enabled or not self._toggle_enabled() or self._popups.modal:
                return False
            self._completion.ensure_loaded()
            return self._popups.open(CompletionManager(self._completion, self._completion_host,
                self._prompt.app.invalidate, self._popups.dismiss, locale=lambda: self.locale),
                panel=lambda: self._config.completion.manage.panel,
                enabled=lambda: self._config.completion.enabled, opener=lambda: self._config.completion.manage.key)
        return self._host.dispatch(show)

    def _run_completion_lookup(self, request):
        self._completion_host.validate(request.command)
        self._completion.begin(request)
        self._popups.dismiss(force=True)
        try:
            self._completion_host.submit(request.command)
        except ValueError as exc:
            self._completion.failed(str(exc))

    def _completion_finished(self, result):
        content = CompletionProgress.finished(result, self.locale, self._completion_host.label(result.prefix))
        self._popups.update_notification(CompletionProgress.KEY, content)

    def _completion_progress(self, progress):
        if progress.phase == Phase.IDLE:
            self._popups.remove_notification(CompletionProgress.KEY)
        else:
            content = CompletionProgress.content(progress, self.locale, self._completion_host.label(progress.prefix))
            self._popups.update_notification(CompletionProgress.KEY, content, ongoing=True)

    def uninstall(self) -> None:
        self._host.dispatch(self._close)
