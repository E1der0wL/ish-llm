"""Selection, action capability, stale-track guards and connection lifetime."""
import asyncio
from dataclasses import replace
import tempfile
import unittest

import jsonschema

from dok import DockConfig, ConfigStore, MediaConfig, PlayerState, PlaybackSnapshot, PlaybackStatus, PlaybackAction
from dok.features.media.runtime import MediaRuntime


def player(name='org.mpris.MediaPlayer2.test', *, status=PlaybackStatus.PLAYING, track='/track/one'):
    return PlayerState(name, ':1.12', track, PlaybackSnapshot(status, 'Song', 'Artist', 10, 100),
                       frozenset(PlaybackAction), True)


class Backend:
    def __init__(self, players=()):
        self.players = {p.name: p for p in players}
        self.connected = self.closed = 0
        self.calls = []
        self.fail_connect = False
        self.fail_command = False

    async def connect(self):
        self.connected += 1
        if self.fail_connect:
            raise OSError('No session bus')

    async def close(self):
        self.closed += 1

    async def discover(self):
        return tuple(self.players)

    async def read(self, name):
        return self.players[name]

    async def control(self, state, action):
        self.calls.append((state.token, action))
        if self.fail_command:
            raise OSError('uncertain delivery')

    async def seek(self, state, position):
        self.calls.append((state.token, position))


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


class MediaSettingsTests(unittest.TestCase):
    def test_existing_positional_settings_constructors_keep_their_meaning(self):
        from dok import DockSettings, DockKeys, Width, AudioConfig, ClockConfig, WidgetsConfig
        dock = DockSettings(DockKeys(toggle=('f8',)), Width(value=90))
        config = DockConfig(dock, AudioConfig(enabled=False), ClockConfig(timezone='UTC'), WidgetsConfig())
        self.assertEqual(config.dock.width.value, 90)
        self.assertEqual(config.clock.timezone, 'UTC')
        self.assertIsInstance(config.widgets, WidgetsConfig)
        self.assertEqual(config.media, MediaConfig())

    def test_settings_schema_save_load_and_partial_defaults(self):
        self.assertFalse(DockConfig().dock.mouse.enabled)
        self.assertFalse(DockConfig.initial().dock.mouse.enabled)
        config = DockConfig(media={'player': 'org.mpris.MediaPlayer2.test', 'refresh_seconds': 0.5},
                            dock={'mouse': {'enabled': True}})
        jsonschema.validate(config.to_dict(), DockConfig.schema())
        with tempfile.TemporaryDirectory() as directory:
            store = ConfigStore.for_home(directory)
            store.save(config)
            self.assertEqual(store.load(), config)
        for data in ({'refresh_seconds': 0}, {'timeout_seconds': False}, {'player': ''}, {'enabled': 1}):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    MediaConfig.from_dict(data)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(data, MediaConfig.schema())


class MediaRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.backend = Backend((player(),))
        self.runtime = MediaRuntime(lambda: None, lambda timeout: self.backend)
        self.config = MediaConfig(refresh_seconds=0.5)

    async def asyncTearDown(self):
        self.runtime.close()
        await until(lambda: not self.runtime._poll.running and not self.runtime.busy)

    def sync(self, active=True, config=None):
        self.runtime.sync(active=active, config=config or self.config, start_task=asyncio.create_task)

    async def test_auto_selection_remains_on_paused_player_and_reselects_on_exit(self):
        second = player('org.mpris.MediaPlayer2.second', status=PlaybackStatus.PAUSED)
        self.backend.players = {second.name: second, player().name: player()}
        self.sync()
        await until(lambda: self.runtime.state.connected)
        self.assertEqual(self.runtime.state.name, player().name)
        paused = replace(player(), playback=replace(player().playback, status=PlaybackStatus.PAUSED))
        self.backend.players[paused.name] = paused
        self.backend.players[second.name] = replace(second, playback=player().playback)
        await self.runtime._refresh(self.backend, self.config)
        self.assertEqual(self.runtime.state.name, paused.name)
        del self.backend.players[paused.name]
        await self.runtime._refresh(self.backend, self.config)
        self.assertEqual(self.runtime.state.name, second.name)

    async def test_explicit_missing_player_does_not_fall_back(self):
        self.sync(config=replace(self.config, player='org.mpris.MediaPlayer2.absent'))
        await until(lambda: self.backend.connected == 1)
        await asyncio.sleep(0)
        self.assertFalse(self.runtime.state.connected)

    async def test_command_rechecks_track_owner_capabilities_and_never_retries(self):
        self.sync()
        await until(lambda: self.runtime.state.connected)
        original = self.runtime.state
        self.backend.players[original.name] = replace(original, track='/track/new')
        self.assertTrue(self.runtime.request(original, fraction=0.5, start_task=asyncio.create_task))
        await until(lambda: not self.runtime.busy)
        self.assertEqual(self.backend.calls, [])
        self.backend.players[original.name] = replace(original, owner=':1.99')
        self.runtime.request(original, action=PlaybackAction.PAUSE, start_task=asyncio.create_task)
        await until(lambda: not self.runtime.busy)
        self.assertEqual(self.backend.calls, [])
        self.backend.players[original.name] = replace(original, actions=frozenset(), seekable=False)
        self.runtime.request(original, fraction=0.5, start_task=asyncio.create_task)
        await until(lambda: not self.runtime.busy)
        self.assertEqual(self.backend.calls, [])
        self.backend.players[original.name] = original
        self.runtime.request(original, fraction=0.5, start_task=asyncio.create_task)
        await until(lambda: not self.runtime.busy)
        self.assertEqual(self.backend.calls[-1], (original.token, 50))
        self.backend.fail_command = True
        self.runtime.request(original, action=PlaybackAction.PLAY_PAUSE, start_task=asyncio.create_task)
        await until(lambda: not self.runtime.busy)
        self.assertIn('uncertain delivery', self.runtime.diagnostic)
        for _ in range(3):
            self.sync()
            await asyncio.sleep(0)
        self.assertEqual(len(self.backend.calls), 2)

    async def test_failure_retries_only_new_activation_and_hidden_closes_bus(self):
        self.backend.fail_connect = True
        self.sync()
        await until(lambda: self.backend.closed == 1)
        for _ in range(3):
            self.sync()
            await asyncio.sleep(0)
        self.assertEqual(self.backend.connected, 1)
        self.assertIn('No session bus', self.runtime.diagnostic)
        self.sync(False)
        self.backend.fail_connect = False
        self.sync()
        await until(lambda: self.runtime.state.connected)
        self.sync(False)
        await until(lambda: self.backend.closed == 2)
        self.assertFalse(self.runtime.state.connected)
        self.assertFalse(self.runtime.request(player(), fraction=0.5, start_task=asyncio.create_task))

    async def test_hide_cancels_inflight_command_before_effect_and_config_replaces_connection(self):
        self.sync()
        await until(lambda: self.runtime.state.connected)
        read = self.backend.read
        entered = asyncio.Event()
        async def slow(name):
            entered.set()
            await asyncio.Event().wait()
        self.backend.read = slow
        self.runtime.request(self.runtime.state, fraction=0.5, start_task=asyncio.create_task)
        await entered.wait()
        self.sync(False)
        await until(lambda: not self.runtime.busy and self.backend.closed == 1)
        self.assertEqual(self.backend.calls, [])
        self.backend.read = read
        changed = replace(self.config, player=player().name)
        self.sync(config=changed)
        await until(lambda: self.runtime.state.connected)
        self.assertEqual(self.backend.connected, 2)
