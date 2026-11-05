"""Real private session bus: discovery, properties, commands and shutdown."""
import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from dbus_fast import Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, method, dbus_property
from dbus_fast.constants import PropertyAccess

from dok import PlaybackAction, PlaybackStatus
from dok.integrations.media.mpris import MprisBackend


class Service(ServiceInterface):
    def __init__(self):
        super().__init__(MprisBackend.PLAYER)
        self.calls = []

    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> 's':
        return 'Playing'

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> 'a{sv}':
        return {'mpris:trackid': Variant('o', '/track/one'), 'mpris:length': Variant('x', 100_000_000),
                'xesam:title': Variant('s', 'Song'), 'xesam:artist': Variant('as', ['Artist'])}

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> 'x':
        return 10_000_000

    @dbus_property(access=PropertyAccess.READ)
    def CanControl(self) -> 'b':
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanPlay(self) -> 'b':
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanPause(self) -> 'b':
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanSeek(self) -> 'b':
        return True

    @dbus_property(access=PropertyAccess.READ)
    def CanGoNext(self) -> 'b':
        return False

    @method()
    def PlayPause(self):
        self.calls.append('toggle')

    @method()
    def SetPosition(self, track: 'o', position: 'x'):
        self.calls.append((track, position))


@unittest.skipUnless(shutil.which('dbus-daemon'), 'dbus-daemon required')
class MprisBusTests(unittest.IsolatedAsyncioTestCase):
    async def test_unreachable_bus_can_close_after_failed_connect(self):
        backend = MprisBackend(0.1)
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {'DBUS_SESSION_BUS_ADDRESS': f'unix:path={directory}/absent'}):
                try:
                    with self.assertRaises(OSError):
                        await backend.connect()
                finally:
                    await backend.close()

    async def test_private_bus_roundtrip_and_capabilities(self):
        daemon = await asyncio.create_subprocess_exec('dbus-daemon', '--session', '--nofork', '--print-address=1',
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        backend = MprisBackend(1)
        server = None
        try:
            async with asyncio.timeout(3):
                address = (await daemon.stdout.readline()).decode().strip()
            with patch.dict(os.environ, {'DBUS_SESSION_BUS_ADDRESS': address}):
                server = await MessageBus().connect()
                service = Service()
                server.export(MprisBackend.PATH, service)
                name = MprisBackend.PREFIX + 'dok_test'
                await server.request_name(name)
                await backend.connect()
                self.assertIn(name, await backend.discover())
                state = await backend.read(name)
                self.assertEqual(state.owner, server.unique_name)
                self.assertEqual(state.playback.status, PlaybackStatus.PLAYING)
                self.assertEqual((state.playback.position, state.playback.duration), (10, 100))
                self.assertEqual(state.playback.caption, 'Artist — Song')
                self.assertTrue(state.supports(PlaybackAction.PLAY_PAUSE))
                self.assertFalse(state.supports(PlaybackAction.NEXT))
                await backend.control(state, PlaybackAction.PLAY_PAUSE)
                await backend.seek(state, 50)
                self.assertEqual(service.calls, ['toggle', ('/track/one', 50_000_000)])
                await server.release_name(name)
                self.assertNotIn(name, await backend.discover())
        finally:
            await backend.close()
            if server is not None:
                server.disconnect()
                await server.wait_for_disconnect()
            daemon.terminate()
            await daemon.wait()

    async def test_request_timeout_is_bounded(self):
        class HangingBus:
            async def call(self, message):
                await asyncio.Event().wait()
        backend = MprisBackend(0.01)
        backend._bus = HangingBus()
        with self.assertRaises(TimeoutError):
            await backend.discover()


class MprisDecodeTests(unittest.TestCase):
    def test_missing_capabilities_and_invalid_metadata_fail_closed(self):
        state = MprisBackend.decode(MprisBackend.PREFIX + 'test', ':1.1', {
            'PlaybackStatus': Variant('s', 'Paused'), 'CanControl': Variant('b', False),
            'CanSeek': Variant('b', True), 'Metadata': Variant('a{sv}', {
                'mpris:trackid': Variant('o', MprisBackend.NO_TRACK), 'mpris:length': Variant('x', -1)})})
        self.assertFalse(state.can_seek)
        self.assertFalse(state.actions)
        self.assertIsNone(state.playback.duration)
