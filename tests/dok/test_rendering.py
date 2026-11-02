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

from dok import (DockConfig, PlaybackAction, ProgressConfig, Separator, Shortcut, ShortcutLayout,
                 TitleConfig, TransportButton, TransportConfig, VisualizerConfig, Width)
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
        self.owner.config = DockConfig(visualizer=VisualizerConfig(colors=("#ff0000", "#0000ff")))
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
        self.owner.config = DockConfig(shortcuts=(Shortcut(1, "rg", emoji="🔎", label="검색", color="#12ab34"),))
        screen, mouse = self.draw()
        y = 6
        line = self.row(screen, y)
        self.assertIn("🔎 검색", line)
        self.assertNotIn("[1]", line)
        self.assertIn("[2]", line)
        self.assertIn("[0]", line)
        x = next(x for x in range(120) if screen.data_buffer[y][x].char == "🔎")
        self.assertIn("#12ab34", screen.data_buffer[y][x].style)
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

    def test_registered_labels_shrink_without_becoming_numbered_slots(self):
        self.owner.config = DockConfig(width=Width(value=39), shortcuts=tuple(
            Shortcut(slot, "git", emoji="👩‍💻", label="긴 이름" * 10) for slot in Shortcut.slots()
        ))
        screen, _ = self.draw(columns=39)
        self.assertNotIn("[", self.row(screen, 6, 39))
        line = self.row(screen, 6, 39)
        self.assertEqual(line.count("…") + line.count("👩‍💻"), Shortcut.COUNT)
        self.assertEqual(self.row(screen, 7, 39).strip(), "EDITOR")

    def test_solid_and_horizontal_gradient_colors_reach_the_screen(self):
        for colors in (("#123456",), ("#ff0000", "#00ff00", "#0000ff")):
            with self.subTest(colors=colors):
                self.owner.config = DockConfig(visualizer=VisualizerConfig(colors=colors))
                self.owner.audio = AudioSnapshot(levels=(1.0,) * len(self.owner.audio.levels))
                screen, _ = self.draw()
                cells = [cell for _, cell in sorted(screen.data_buffer[2].items()) if cell.char == "█"]
                self.assertEqual(len(cells), len(self.owner.audio.levels))
                expected = self.owner.config.visualizer.palette(len(cells))
                for cell, color in zip(cells, expected):
                    self.assertIn(color, cell.style)
                    self.assertIn("bg:default", cell.style)

    def test_visualizer_length_resizes_centered_with_gradient_endpoints(self):
        self.owner.audio = AudioSnapshot(levels=(1.0,) * len(self.owner.audio.levels))
        for length, columns, expected in ((1, 120, 1), (12, 120, 12), (48, 120, 48),
                                          (100, 120, 80), (48, 39, 39), (48, 120, 48)):
            with self.subTest(length=length, columns=columns):
                self.owner.config = DockConfig(visualizer=VisualizerConfig(
                    length=length, colors=("#ff0000", "#0000ff")))
                screen, _ = self.draw(columns=columns)
                cells = [(x, cell) for x, cell in sorted(screen.data_buffer[2].items()) if cell.char == "█"]
                self.assertEqual(len(cells), expected)
                self.assertEqual(cells[0][0], (columns - expected) // 2)
                self.assertIn("#ff0000", cells[0][1].style)
                if expected > 1:
                    self.assertIn("#0000ff", cells[-1][1].style)

    def test_empty_slots_stay_numbered_when_a_registered_text_is_truncated(self):
        self.owner.config = DockConfig(width=Width(value=39), shortcuts=(
            Shortcut(1, "git", label="very long label"),
        ))
        screen, mouse = self.draw(columns=39)
        line = self.row(screen, 6, 39)
        self.assertIn("ve…", line)
        self.assertNotIn("[1]", line)
        for slot in range(2, 11):
            self.assertIn(Shortcut.compact_label(slot), line)
        x = next(x for x, cell in screen.data_buffer[6].items() if cell.char == "v")
        with set_app(self.app):
            mouse.mouse_handlers[6][x](MouseEvent(Point(x, 6), MouseEventType.MOUSE_UP,
                                                 MouseButton.LEFT, frozenset()))
        self.assertEqual(self.executed, [1])

    def test_separator_replaces_number_uses_color_and_consumes_click_without_action(self):
        self.owner.config = DockConfig(shortcuts=(Shortcut(1, "git", label="Git"),
                                                  Separator(2, color="#c084fc"),
                                                  Shortcut(3, "ls", label="Files")))
        screen, mouse = self.draw(columns=39)
        line = self.row(screen, 6, 39)
        self.assertIn("Git │ Files", line)
        self.assertNotIn("[2]", line)
        self.assertIn("[4]", line)
        x = next(x for x, cell in screen.data_buffer[6].items() if cell.char == "│")
        self.assertIn("#c084fc", screen.data_buffer[6][x].style)
        with set_app(self.app):
            mouse.mouse_handlers[6][x](MouseEvent(Point(x, 6), MouseEventType.MOUSE_UP,
                                                 MouseButton.LEFT, frozenset()))
        self.assertEqual(self.executed, [])
        self.assertEqual(self.behind_clicks, [])

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

    def test_title_prefix_color_and_diagnostic_override(self):
        self.owner.config = DockConfig(title=TitleConfig(prefix="NOW: ", color="#abcdef"))
        self.owner.audio = AudioSnapshot(applications=("VLC",))
        screen, _ = self.draw()
        self.assertIn("NOW: VLC", self.row(screen, 4))
        cell = screen.data_buffer[4][self.row(screen, 4).index("NOW: VLC")]
        self.assertIn("#abcdef", cell.style)
        self.owner.config = DockConfig(title=TitleConfig(prefix=""))
        screen, _ = self.draw()
        self.assertIn("VLC", self.row(screen, 4))
        self.assertNotIn("재생 중", self.row(screen, 4))
        self.owner.diagnostic = "diagnostic"
        screen, _ = self.draw()
        self.assertIn("diagnostic", self.row(screen, 4))

    def test_progress_color_length_and_resize(self):
        self.owner.playback = PlaybackSnapshot(position=5, duration=10)
        for length, columns, expected in ((20, 120, 20), (100, 39, 27), (100, 120, 68)):
            with self.subTest(length=length, columns=columns):
                self.owner.config = DockConfig(progress=ProgressConfig(length=length, color="#abcdef"))
                screen, _ = self.draw(columns=columns)
                cells = [cell for cell in screen.data_buffer[3].values() if cell.char in ("━", "─")]
                self.assertEqual(len(cells), expected)
                self.assertEqual(sum(cell.char == "━" for cell in cells), expected // 2)
                self.assertTrue(all("#abcdef" in cell.style for cell in cells))

    def test_transport_button_order_custom_text_color_and_hiding(self):
        self.owner.config = DockConfig(transport=TransportConfig(buttons=(
            TransportButton("stop", text="STOP", color="#ff0000"),
            TransportButton("play", text="GO", color="#00ff00"),
        )))
        screen, mouse = self.draw()
        self.assertIn("STOP   GO", self.row(screen, 5))
        x = next(x for x, cell in screen.data_buffer[5].items() if cell.char == "S")
        self.assertIn("#ff0000", screen.data_buffer[5][x].style)
        with set_app(self.app):
            mouse.mouse_handlers[5][x](MouseEvent(Point(x, 5), MouseEventType.MOUSE_UP,
                                                 MouseButton.LEFT, frozenset()))
        self.assertEqual(self.executed, [])
        self.assertEqual(self.behind_clicks, [])
        self.owner.config = DockConfig(transport=TransportConfig(buttons=()))
        screen, _ = self.draw()
        self.assertNotIn("STOP", self.row(screen, 5))
        self.assertIn("EDITOR", self.row(screen, 5))

    def test_long_transport_labels_stay_inside_one_centered_row(self):
        self.owner.config = DockConfig(transport=TransportConfig(buttons=tuple(
            TransportButton(action, text="버튼" * 100) for action in PlaybackAction)))
        screen, _ = self.draw(columns=39)
        self.assertEqual(self.row(screen, 5, 39).count("…"), len(PlaybackAction))
        self.assertIn("[1]", self.row(screen, 6, 39))

    def test_shortcut_padding_spacing_zero_and_narrow_width(self):
        for padding, columns, expected in ((3, 120, 3), (0, 120, 0), (10**100, 39, 1)):
            with self.subTest(padding=padding, columns=columns):
                self.owner.config = DockConfig(shortcut_layout=ShortcutLayout(padding=padding))
                screen, _ = self.draw(columns=columns)
                text = (" " * expected).join(Shortcut.compact_label(slot) for slot in Shortcut.slots())
                self.assertIn(text, self.row(screen, 6, columns))
