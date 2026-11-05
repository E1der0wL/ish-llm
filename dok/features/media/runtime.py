"""Visible-only discovery and commands, with stable selection and no command replay."""
from __future__ import annotations

import asyncio
from collections.abc import Callable

from ...core.tasks import TaskSlot
from ...integrations.media.mpris import MprisBackend
from .connection import MediaConfig, PlayerState, PlayerBackend


class MediaRuntime:
    def __init__(self, changed: Callable[[], None], factory: Callable[[float], PlayerBackend] = MprisBackend):
        self._changed, self._factory = changed, factory
        self._poll = TaskSlot(changed)
        self._command = TaskSlot(changed)
        self._backend = None
        self._active = False
        self._config = None
        self._failed = False
        self._retained = None
        self._state = PlayerState()
        self._players = ()
        self._diagnostic = ""
        self._generation = 0

    def _publish(self, state):
        if self._state != state:
            self._state = state
            self._changed()

    async def _refresh(self, backend, config):
        names = (config.player,) if config.player else await backend.discover()
        self._players = names
        preferred = config.player or self._retained
        if preferred in names:
            try:
                state = await backend.read(preferred)
            except Exception:
                state = PlayerState()
            if state.connected:
                self._retained = state.name
                self._publish(state)
                return
        async def read(name):
            try:
                return await backend.read(name)
            except Exception:
                return None  # A player may exit between discovery and reading.
        states = await asyncio.gather(*(read(name) for name in names if name != preferred))
        state = PlayerState.select(tuple(p for p in states if p is not None), preferred)
        self._retained = state.name or None
        self._publish(state)

    async def _run(self, config):
        backend = self._factory(config.timeout_seconds)
        try:
            await backend.connect()
            self._backend = backend
            while True:
                await self._refresh(backend, config)
                await asyncio.sleep(config.refresh_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failed = True
            self._diagnostic = f"MPRIS connection failed: {exc}"
            self._publish(PlayerState())
        finally:
            if self._backend is backend:
                self._backend = None
            self._publish(PlayerState())
            try:
                await backend.close()
            except Exception as exc:
                self._diagnostic = f"MPRIS disconnect failed: {exc}"

    async def _perform(self, expected, action, fraction, generation):
        backend = self._backend
        try:
            fresh = await backend.read(expected.name)
            if not self._active or generation != self._generation or fresh.token != expected.token:
                return
            if action is not None and fresh.supports(action):
                await backend.control(fresh, action)
            elif fraction is not None and fresh.can_seek:
                await backend.seek(fresh, fresh.seek_position(fraction))
            else:
                return
            if self._active and generation == self._generation:
                updated = await backend.read(expected.name)
                if self._active and generation == self._generation:
                    self._publish(updated)
                    self._diagnostic = ""
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._diagnostic = f"MPRIS command failed: {exc}"
            self._changed()

    @property
    def state(self) -> PlayerState:
        return self._state

    @property
    def players(self) -> tuple[str, ...]:
        return self._players

    @property
    def diagnostic(self) -> str:
        return self._diagnostic

    @property
    def busy(self) -> bool:
        return self._command.running

    def sync(self, *, active, config: MediaConfig, start_task):
        active = active and config.enabled and not self._poll.closed
        if config != self._config or active != self._active:
            self._generation += 1
            self._command.sync(active=False, key=None, run=lambda: asyncio.sleep(0), start=start_task)
            if config != self._config or active:
                self._failed = False
                self._diagnostic = ""
            self._publish(PlayerState())
        self._active, self._config = active, config
        self._poll.sync(active=active and not self._failed, key=config,
                        run=lambda: self._run(config), start=start_task)

    def request(self, expected: PlayerState, *, action=None, fraction=None, start_task) -> bool:
        if (not self._active or self._backend is None or self.busy or expected.token != self._state.token
                or not expected.connected):
            return False
        if action is not None:
            if not expected.supports(action):
                return False
        elif not expected.accepts_seek(fraction):
            return False
        return self._command.sync(active=True, key=object(),
            run=lambda: self._perform(expected, action, fraction, self._generation), start=start_task)

    def close(self):
        self._active = False
        self._generation += 1
        self._command.close()
        self._poll.close()
        self._publish(PlayerState())
