"""Cell-level placement, transparent overlay, mouse routing and width edge cases."""

from types import SimpleNamespace
import unittest

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import set_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.input import DummyInput
from prompt_toolkit.layout import Float, FloatContainer, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen, WritePosition
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import _CHAR_SIZES_CACHE

from dok import DockConfig, Shortcut, Width
from dok.audio import AudioSnapshot
from dok.overlay import DockOverlay
from dok.player import PlaybackSnapshot
from dok.text import clusters, fit, width
from dok.view import DockGeometry, DockView


class RenderingTests(unittest.TestCase):
    def setUp(self):
        self.executed = []
        self.draw_count = 0
        self.owner = SimpleNamespace(visible=True, config=DockConfig(), playback=PlaybackSnapshot(), audio=AudioSnapshot(),
                                     diagnostic="", pty_columns=lambda: 120)
        self.overlay = DockOverlay(self.owner, DockView(self.executed.append))

    def draw(self, columns=120, rows=8):
        # Match ish: background editor, cursor-relative completion, custom Float last.
        self.behind_clicks = []
        def behind(event):
            self.behind_clicks.append(event)
        back = Window(FormattedTextControl([("", "\n".join("EDITOR" for _ in range(rows)), behind)]))
        container = FloatContainer(back, [
            Float(Window(FormattedTextControl("MENU\nMENU\nMENU\nMENU")),
                  xcursor=True, ycursor=True),
            Float(self.overlay, left=0, right=0, top=0, bottom=0, transparent=self.overlay.TRANSPARENT),
        ])
        app = Application(layout=Layout(container), input=DummyInput(), output=DummyOutput())
        self.draw_count += 1
        app.render_counter = self.draw_count
        self.app = app
        app.layout.update_parents_relations()
        screen, mouse = Screen(), MouseHandlers()
        with set_app(app):
            container.write_to_screen(screen, mouse, WritePosition(0, 0, columns, rows), "", True, None)
            screen.draw_all_floats()
        return screen, mouse

    @staticmethod
    def row(screen, y, columns=120):
        return "".join(screen.data_buffer[y][x].char for x in range(columns))

    def test_overlay_is_above_menu_and_only_paints_bottom_strip(self):
        screen, _ = self.draw()
        self.assertTrue(self.row(screen, 0).startswith("EDITOR"))
        for y in range(2, 8):
            self.assertTrue(any(text in self.row(screen, y) for text in ("EDITOR", "MENU")))
        self.assertIn("플레이어 연결 안 됨", self.row(screen, 4))
        self.assertEqual(self.row(screen, 7).strip(), "EDITOR")

    def test_animated_rows_do_not_accumulate_strings_in_terminal_width_cache(self):
        self.draw()
        before = set(_CHAR_SIZES_CACHE)
        base = len(DockView.LEVEL_GLYPHS)
        for frame in range(256):
            self.owner.audio = AudioSnapshot(levels=tuple((frame // base ** band % base) / (base - 1)
                                                         for band in range(len(self.owner.audio.levels))))
            self.draw()
        self.assertTrue(all(len(key) == 1 for key in set(_CHAR_SIZES_CACHE) - before))

    def test_minimum_height_does_not_draw_outside_container(self):
        for rows in (1, 6):
            screen, _ = self.draw(rows=rows)
            self.assertFalse(any("플레이어" in self.row(screen, y) for y in range(rows)))
            self.assertTrue(all(y >= 0 for y in screen.data_buffer))
        screen, _ = self.draw(rows=7)
        self.assertIn("플레이어", self.row(screen, 3))

    def test_shortcuts_center_and_clicks_never_pass_through(self):
        self.owner.config = DockConfig(shortcuts=(Shortcut(1, "rg", emoji="🔎", label="검색"),))
        screen, mouse = self.draw()
        y = 6
        line = self.row(screen, y)
        self.assertIn("[1] 🔎 검색", line)
        x = next(x for x in range(120) if screen.data_buffer[y][x].char == "[")
        click = MouseEvent(Point(x + 1, y), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset())
        with set_app(self.app):
            mouse.mouse_handlers[y][x + 1](click)
        self.assertEqual(self.executed, [1])
        blank = MouseEvent(Point(0, 7), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset())
        with set_app(self.app):
            mouse.mouse_handlers[7][0](blank)
            right_click = MouseEvent(Point(x + 1, y), MouseEventType.MOUSE_UP, MouseButton.RIGHT, frozenset())
            self.assertIsNone(mouse.mouse_handlers[y][x + 1](right_click))
        self.assertEqual(self.executed, [1])
        self.assertEqual(len(self.behind_clicks), 1)

    def test_labels_collapse_to_ten_numbered_slots_without_wrapping(self):
        self.owner.config = DockConfig(width=Width(value=39), shortcuts=tuple(
            Shortcut(slot, "git", emoji="👩‍💻", label="긴 이름" * 10) for slot in Shortcut.slots()
        ))
        screen, _ = self.draw(columns=39)
        self.assertEqual(self.row(screen, 6, 39), " ".join(f"[{slot % 10}]" for slot in range(1, 11)))
        self.assertEqual(self.row(screen, 7, 39).strip(), "EDITOR")

    def test_wide_text_and_diagnostics_are_clipped_on_cluster_boundaries(self):
        self.assertEqual(list(clusters("a👩‍💻🇰🇷é")), ["a", "👩‍💻", "🇰🇷", "é"])
        text = "👩‍💻" * 10
        clipped = fit(text, 7)
        self.assertLessEqual(width(clipped), 7)
        self.assertFalse(clipped.rstrip("…").endswith("\u200d"))
        self.owner.playback = PlaybackSnapshot(title="한글 제목" * 100)
        self.owner.diagnostic = "bad\ntext\x1b[2J"
        screen, _ = self.draw(columns=80)
        self.assertIn("bad text", self.row(screen, 4, 80))

    def test_pty_ratio_uses_pty_width_then_clamps_to_frame(self):
        self.owner.config = DockConfig(width=Width(mode="ratio", value=0.6))
        self.assertEqual(self.owner.config.width.resolve(self.owner.pty_columns(), 118), 72)
        self.assertEqual(self.overlay.preferred_height(120, 24).preferred, 0)
        self.owner.visible = False
        screen, _ = self.draw()
        self.assertIn("EDITOR", self.row(screen, 7))

    def test_extreme_playback_times_stay_within_configured_body(self):
        self.owner.config = DockConfig(width=Width(value=39))
        self.owner.playback = PlaybackSnapshot(position=1e100, duration=1e101)
        screen, _ = self.draw()
        line = self.row(screen, 3)
        self.assertEqual(line[6:40], " " * 34)
        self.assertEqual(line[79:], " " * 41)

    def test_title_shows_output_applications_until_player_metadata_is_available(self):
        self.owner.audio = AudioSnapshot(applications=("Firefox", "VLC"))
        screen, _ = self.draw()
        self.assertIn("재생 중: Firefox · VLC", self.row(screen, 4))
        self.owner.playback = PlaybackSnapshot(title="현재 곡", artist="가수")
        screen, _ = self.draw()
        self.assertIn("가수 — 현재 곡", self.row(screen, 4))
        self.assertNotIn("Firefox", self.row(screen, 4))
