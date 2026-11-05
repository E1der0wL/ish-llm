"""Cell-level media interaction and application mouse ownership."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from prompt_toolkit.application.current import set_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.mouse_events import MouseEvent, MouseButton, MouseEventType

from dok import PlaybackAction, DockConfig
from dok.dashboard.view import DockView
from dok.features.media.input import MediaInput
from dok.integrations.ish.mouse import DockMouse
from tests.dok.features.test_media import player
from tests.dok.rendering import test_rendering


class MediaMouseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_rendering.RenderingTests()
        self.fixture.setUp()
        self.calls = []
        self.runtime = SimpleNamespace(state=player(), busy=False,
            request=lambda expected, **kwargs: self.calls.append((expected, kwargs)))
        self.input = MediaInput(self.runtime, lambda coroutine: None, lambda: None)
        self.fixture.owner.playback = player().playback
        self.fixture.overlay._view = DockView(lambda slot: None, self.input)

    def event(self, x, y, kind, *, button=MouseButton.LEFT):
        return MouseEvent(Point(x, y), kind, button, frozenset())

    def draw(self):
        screen, handlers = self.fixture.draw()
        self.fixture.app.renderer.mouse_handlers = handlers
        self.mouse = DockMouse(self.fixture.app, lambda: True,
                              lambda: self.fixture.overlay.mouse_regions, self.input.cancel)
        self.addCleanup(self.mouse.close)
        self.mouse.sync()
        return screen

    def send(self, x, y, kind):
        with set_app(self.fixture.app):
            self.fixture.app.renderer.mouse_handlers.mouse_handlers[y][x](self.event(x, y, kind))

    def test_progress_drag_only_commits_on_release_and_can_cancel_outside(self):
        screen = self.draw()
        cells = [x for x, cell in sorted(screen.data_buffer[3].items()) if cell.char in ('━', '─')]
        self.assertEqual(len(cells), self.fixture.owner.config.widgets.progress.width_columns)
        self.send(cells[0], 3, MouseEventType.MOUSE_DOWN)
        self.send(cells[-1], 3, MouseEventType.MOUSE_MOVE)
        self.assertEqual(self.input.preview(player().playback).position, 100)
        self.assertEqual(self.calls, [])
        self.send(cells[-1], 3, MouseEventType.MOUSE_UP)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1]['fraction'], 1)
        self.assertFalse(self.input.dragging)
        self.send(cells[0], 3, MouseEventType.MOUSE_DOWN)
        self.send(0, 0, MouseEventType.MOUSE_UP)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.input.dragging)
        self.assertEqual(self.fixture.behind_clicks, [])

    def test_stale_track_and_unsupported_buttons_never_dispatch(self):
        self.runtime.state = replace(player(), actions=frozenset({PlaybackAction.PLAY_PAUSE}))
        screen = self.draw()
        row = screen.data_buffer[5]
        previous = next(x for x, cell in row.items() if cell.char == PlaybackAction.PREVIOUS.symbol)
        toggle = next(x for x, cell in row.items() if cell.char == PlaybackAction.PLAY_PAUSE.symbol)
        self.assertIn('dim', row[previous].style)
        self.send(previous, 5, MouseEventType.MOUSE_UP)
        self.assertEqual(self.calls, [])
        self.send(toggle, 5, MouseEventType.MOUSE_UP)
        self.assertEqual(self.calls[-1][1]['action'], PlaybackAction.PLAY_PAUSE)
        cells = [x for x, cell in sorted(screen.data_buffer[3].items()) if cell.char in ('━', '─')]
        self.send(cells[0], 3, MouseEventType.MOUSE_DOWN)
        self.runtime.state = replace(player(), track='/track/new')
        self.input.preview(self.runtime.state.playback)
        self.send(cells[-1], 3, MouseEventType.MOUSE_UP)
        self.assertEqual(len(self.calls), 1)

    def test_weather_title_and_prompt_are_inert_and_filter_restores_exactly(self):
        self.draw()
        original_filter = self.mouse._previous
        source = self.mouse._source
        for y in (0, 2, 4, 8):
            self.send(60, y, MouseEventType.MOUSE_UP)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.fixture.behind_clicks, [])
        self.assertTrue(self.fixture.app.mouse_support())
        self.mouse.close()
        self.assertIs(self.fixture.app.mouse_support, original_filter)
        self.assertIs(self.fixture.app.renderer.mouse_handlers, source)
        # A later plugin's change remains owned by that plugin.
        replacement = Condition(lambda: True)
        self.fixture.app.mouse_support = replacement
        self.mouse.close()
        self.assertIs(self.fixture.app.mouse_support, replacement)

    def test_disabled_mouse_rejects_even_previously_rendered_handlers(self):
        enabled = True
        interaction = MediaInput(self.runtime, lambda coroutine: None, lambda: None, lambda: enabled)
        button = interaction.button(PlaybackAction.PLAY_PAUSE)
        seek = interaction.seek(0.5)
        enabled = False
        button(self.event(0, 0, MouseEventType.MOUSE_UP))
        seek(self.event(0, 0, MouseEventType.MOUSE_DOWN))
        seek(self.event(0, 0, MouseEventType.MOUSE_UP))
        self.assertEqual(self.calls, [])
        self.assertFalse(interaction.dragging)

    def test_weather_icons_do_not_dispatch_media_or_shortcut_commands(self):
        from dok import DockMode
        self.fixture.owner.requested_mode = DockMode.MAXIMIZED
        screen, handlers = self.fixture.draw(rows=36)
        self.fixture.app.renderer.mouse_handlers = handlers
        scope = DockMouse(self.fixture.app, lambda: True,
                         lambda: self.fixture.overlay.mouse_regions, self.input.cancel)
        try:
            scope.sync()
            with set_app(self.fixture.app):
                for y, row in screen.data_buffer.items():
                    for x, cell in row.items():
                        if cell.char == self.fixture.owner.config.widgets.weather.icons.unknown:
                            self.fixture.app.renderer.mouse_handlers.mouse_handlers[y][x](
                                self.event(x, y, MouseEventType.MOUSE_UP))
            self.assertEqual(self.calls, [])
            self.assertEqual(self.fixture.executed, [])
        finally:
            scope.close()
