"""Coordinate typed owners without interpreting their persisted dictionaries."""

from __future__ import annotations

from pathlib import Path

from .audio import AudioRuntime, AudioSnapshot
from .config import ConfigStore, DockConfig
from .host import HostAccess
from .keys import KeyRegistration, OwnedBindings
from .overlay import DockOverlay
from .player import PlaybackSnapshot
from .state import DockState
from .submission import ShortcutSubmission
from .view import DockView


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
        self._bindings = OwnedBindings(prompt)
        self._submission = ShortcutSubmission(prompt, self._state)
        self._view = DockView(self.execute)
        self._overlay = DockOverlay(self, self._view)
        self._float_handle = None

    def _entries(self, config: DockConfig):
        entries = [KeyRegistration(config.toggle_key, lambda event: self.toggle(), self._toggle_enabled)]
        for shortcut in config.shortcuts:
            if shortcut.enabled and shortcut.key is not None:
                def execute(event, slot=shortcut.slot):
                    self.execute(slot)
                entries.append(KeyRegistration(shortcut.key, execute, self._shortcuts_enabled))
        return entries

    def _toggle_enabled(self) -> bool:
        app = self._prompt.app
        return not self._state.closed and app.is_running and not app.is_done and self._host.editing_shell()

    def _shortcuts_enabled(self) -> bool:
        return (self.visible and self._overlay.has_room
                and getattr(self._prompt.app.layout.current_control, "buffer", None) is self._prompt.default_buffer)

    def _before_render(self, app):
        self._host.observe_loop()
        self._state.enter_prompt(app.future)

    def _after_render(self, app):
        self._audio.sync(active=self.visible and self._overlay.has_room, config=self._config.audio, app=app)

    def _attach(self):
        if self._state.closed:
            raise RuntimeError("dok has been uninstalled")
        if self._float_handle is not None:
            return
        self._float_handle = self._prompt.set_float(
            self._overlay, left=0, right=0, top=0, bottom=0,
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
        self._config = config
        self._state.report("")
        self._prompt.app.invalidate()

    def _reload(self, store: ConfigStore) -> bool:
        try:
            self._configure(store.load())
        except (ValueError, OSError) as exc:
            self._state.report(exc)
            logger = getattr(self._prompt, "logger", None)
            if logger is not None:
                logger.warning("dok settings unchanged: %s", exc)
            self._prompt.app.invalidate()
            return False
        self._store = store
        return True

    def _close(self):
        if self._state.closed:
            return
        self._state.close()
        self._audio.close()
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
    def playback(self) -> PlaybackSnapshot:
        return self._playback

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
    def visible(self) -> bool:
        app = self._prompt.app
        return self._state.can_render(running=app.is_running, done=app.is_done) and self._host.editing_shell()

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
        def change():
            self._state.toggle()
            self._prompt.app.invalidate()
        self._host.dispatch(change)

    def show(self, visible: bool = True) -> None:
        def change():
            self._state.show(visible)
            self._prompt.app.invalidate()
        self._host.dispatch(change)

    def execute(self, slot: int) -> bool:
        def submit():
            shortcut = self._config.shortcut(slot)
            return shortcut is not None and self._submission.execute(shortcut)
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

    def uninstall(self) -> None:
        self._host.dispatch(self._close)
