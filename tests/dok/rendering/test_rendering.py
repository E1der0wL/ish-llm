"""Cell-level placement, transparent overlay, mouse routing and width edge cases."""
from dok import DockConfig, WidgetsConfig, ClockConfig, ShortcutsConfig
from types import SimpleNamespace
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
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
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import _CHAR_SIZES_CACHE
from dok import PlaybackAction, ProgressConfig, Separator, Shortcut, TitleConfig, TransportButton, TransportConfig, VisualizerConfig, Width
from dok.features.audio.models import AudioSnapshot, AudioStatus
from dok.rendering.overlay import DockOverlay
from dok.features.media.models import PlaybackSnapshot
from dok.rendering.text import clusters, fit, width
from dok.dashboard.layout import DockGeometry
from dok.dashboard.view import DockView
from dok.features.audio.view import VisualizerView
from dok import DockMode, WeatherSnapshot, WeatherConfig
from dok.dashboard.view import DashboardView
from dok.features.clock.view import WeekdayRenderer
from dok.features.weather.models import ForecastDay, WeatherKind
from dok.features.weather.config import WeatherLocation

class RenderingTests(unittest.TestCase):

    def setUp(self):
        self.executed = []
        self.draw_count = 0
        self.owner = SimpleNamespace(
            visible=True,
            config=DockConfig(widgets={'shortcuts': {'items': ()}}),
            playback=PlaybackSnapshot(),
            audio=AudioSnapshot(),
            diagnostic='',
            pty_columns=lambda: 120,
            requested_mode=DockMode.BASIC,
            weather=WeatherSnapshot()
        )
        self.overlay = DockOverlay(self.owner, DockView(self.executed.append))

    def draw(self, columns=120, rows=9):
        self.behind_clicks = []

        def behind(event):
            self.behind_clicks.append(event)
        back = Window(FormattedTextControl([('', '\n'.join(('EDITOR' for _ in range(rows))), behind)]))
        container = FloatContainer(
            back,
            [
                Float(Window(FormattedTextControl('MENU\nMENU\nMENU\nMENU')), xcursor=True, ycursor=True),
                Float(self.overlay, left=0, right=0, top=0, bottom=0, transparent=self.overlay.TRANSPARENT)
            ]
        )
        app = Application(layout=Layout(container), input=DummyInput(), output=DummyOutput())
        self.draw_count += 1
        app.render_counter = self.draw_count
        self.app = app
        app.layout.update_parents_relations()
        screen, mouse = (Screen(), MouseHandlers())
        with set_app(app):
            container.write_to_screen(screen, mouse, WritePosition(0, 0, columns, rows), '', True, None)
            screen.draw_all_floats()
        return (screen, mouse)

    @staticmethod
    def row(screen, y, columns=120):
        return ''.join((screen.data_buffer[y][x].char for x in range(columns)))

    def test_overlay_is_above_menu_and_only_paints_bottom_strip(self):
        screen, _ = self.draw()
        self.assertTrue(self.row(screen, 0).startswith('EDITOR'))
        for y in range(2, 9):
            self.assertTrue(any((text in self.row(screen, y) for text in ('EDITOR', 'MENU'))))
        self.assertIn('Player disconnected', self.row(screen, 4))
        self.assertEqual(self.row(screen, 8).strip(), 'EDITOR')

    def test_animated_rows_do_not_accumulate_strings_in_terminal_width_cache(self):
        self.owner.config = DockConfig(widgets={'visualizer': VisualizerConfig(style={'colors': ('#ff0000', '#0000ff')})})
        self.draw()
        before = set(_CHAR_SIZES_CACHE)
        base = len(VisualizerView.LEVEL_GLYPHS)
        for frame in range(256):
            self.owner.audio = AudioSnapshot(levels=tuple((frame // base ** band % base / (base - 1) for band in range(len(self.owner.audio.levels)))))
            self.draw()
        self.assertTrue(all((len(key) == 1 for key in set(_CHAR_SIZES_CACHE) - before)))

    def test_shortcut_vertical_padding_is_transparent_and_preserves_clicks(self):
        screen, mouse = self.draw(rows=8)
        self.assertIn(PlaybackAction.PREVIOUS.symbol, self.row(screen, 4))
        self.assertEqual(self.row(screen, 5).strip(), 'EDITOR')
        self.assertIn('[1]', self.row(screen, 6))
        self.assertEqual(self.row(screen, 7).strip(), 'EDITOR')
        with set_app(self.app):
            for y in (5, 7):
                mouse.mouse_handlers[y][0](MouseEvent(
                    Point(0, y),
                    MouseEventType.MOUSE_UP,
                    MouseButton.LEFT,
                    frozenset()
                ))
        self.assertEqual(len(self.behind_clicks), 2)
        self.assertEqual(self.executed, [])

    def test_maximized_rows_stay_centered_and_basic_rows_keep_bottom_positions(self):
        self.owner.config = DockConfig(widgets=WidgetsConfig(
            shortcuts=ShortcutsConfig(items=()),
            weather=WeatherConfig(location=None)
        ))
        self.owner.requested_mode = DockMode.MAXIMIZED
        extra = DashboardView().rows(self.owner.config, self.owner.weather, 80)
        height = DockGeometry.height(DockMode.MAXIMIZED, len(extra)) + 1
        screen, mouse = self.draw(rows=height)
        self.assertEqual(self.overlay.display_mode, DockMode.MAXIMIZED)
        self.assertIn('--° --°', self.row(screen, 3))
        self.assertIn('[1]', self.row(screen, height - 2))
        for index, content in enumerate(extra):
            text = content.text
            if text:
                left = (120 - 80) // 2 + (80 - width(text)) // 2
                self.assertEqual(self.row(screen, index + 1)[left:left + len(text)], text)
        gap = max((index for index, content in enumerate(extra) if not content.text)) + 1
        self.assertEqual(self.row(screen, gap).strip(), 'EDITOR')
        with set_app(self.app):
            mouse.mouse_handlers[gap][0](MouseEvent(
                Point(0, gap),
                MouseEventType.MOUSE_UP,
                MouseButton.LEFT,
                frozenset()
            ))
        self.assertEqual(len(self.behind_clicks), 1)
        screen, _ = self.draw(rows=height - 1)
        self.assertEqual(self.overlay.display_mode, DockMode.BASIC)
        self.assertNotIn(
            '--° --°',
            '\n'.join((self.row(screen, y) for y in range(height - 1)))
        )
        self.assertEqual(self.owner.requested_mode, DockMode.MAXIMIZED)
        self.draw(rows=height)
        self.assertEqual(self.overlay.display_mode, DockMode.MAXIMIZED)

    def test_maximized_preview_styles_weather_art_and_exact_vertical_gaps(self):
        self.owner.config = DockConfig(widgets=WidgetsConfig(
            shortcuts=ShortcutsConfig(items=()),
            weather=WeatherConfig(location=WeatherLocation(0, 0))
        ))
        self.owner.requested_mode = DockMode.MAXIMIZED
        self.owner.weather = WeatherSnapshot(tuple((ForecastDay(
            date(2026, 11, 3) + timedelta(days=index),
            WeatherKind.CLEAR,
            16,
            -2
        ) for index in range(7))))
        self.owner.playback = PlaybackSnapshot(title='VLC')
        now = datetime(2026, 11, 3, 6, 29, tzinfo=timezone.utc)
        with patch.object(ClockConfig, 'now', return_value=now):
            extra = DashboardView().rows(self.owner.config, self.owner.weather, 80)
            height = DockGeometry.height(DockMode.MAXIMIZED, len(extra)) + 1
            self.owner.visible = False
            behind, _ = self.draw(rows=height)
            self.owner.visible = True
            screen, mouse = self.draw(rows=height)
        text_rows = [self.row(screen, y) for y in range(height)]
        self.assertNotIn('화성 동탄', '\n'.join(text_rows))
        self.assertNotIn('Open-Meteo', '\n'.join(text_rows))
        self.assertIn('16° -2°', text_rows[3])
        low_x = text_rows[3].index('-2°')
        self.assertIn(
            self.owner.config.theme.resolve(self.owner.config.widgets.weather.style.low_color),
            screen.data_buffer[3][low_x].style
        )
        high_x = text_rows[3].index('16°')
        self.assertNotIn(
            self.owner.config.theme.resolve(self.owner.config.widgets.weather.style.low_color),
            screen.data_buffer[3][high_x].style
        )
        art = WeekdayRenderer().render(self.owner.config.widgets.weekday, now.weekday(), 80)
        self.assertEqual(text_rows[4], self.row(behind, 4))
        for index, line in enumerate(art):
            self.assertIn(line, text_rows[5 + index])
        date_y = next((y for y, text in enumerate(text_rows) if '03 NOVEMBER, 2026.' in text))
        date_x = text_rows[date_y].index('03 NOVEMBER')
        self.assertIn('bold', screen.data_buffer[date_y][date_x].style)
        self.assertIn('-06:29-', text_rows[date_y + 1])
        visualizer_y = next((y for y, text in enumerate(text_rows) if '▁' in text))
        title_y = next((y for y, text in enumerate(text_rows) if 'VLC' in text))
        theme = Style.from_dict({"": "#223344"})
        for y, x in ((3, high_x), (date_y, date_x),
                     (date_y + 1, text_rows[date_y + 1].index('-06:29-')), (title_y, text_rows[title_y].index('VLC'))):
            self.assertEqual(theme.get_attrs_for_style_str(screen.data_buffer[y][x].style).color, 'default')
        self.assertEqual(theme.get_attrs_for_style_str(screen.data_buffer[3][low_x].style).color, '77818d')
        buttons_y = next((y for y, text in enumerate(text_rows) if PlaybackAction.PREVIOUS.symbol in text))
        shortcuts_y = next((y for y, text in enumerate(text_rows) if '[1]' in text))
        self.assertEqual(visualizer_y - (date_y + 1), 3)
        self.assertEqual(title_y, visualizer_y + 2)
        self.assertEqual(buttons_y - title_y, 2)
        self.assertEqual(shortcuts_y - buttons_y, 3)
        gaps = [4] + list(range(date_y + 2, visualizer_y)) + [title_y + 1, buttons_y + 1, buttons_y + 2, height - 1]
        with set_app(self.app):
            for y in gaps:
                self.assertEqual(text_rows[y], self.row(behind, y))
                mouse.mouse_handlers[y][4](MouseEvent(
                    Point(4, y),
                    MouseEventType.MOUSE_UP,
                    MouseButton.LEFT,
                    frozenset()
                ))
        self.assertEqual(len(self.behind_clicks), len(gaps))

    def test_disabled_elements_and_custom_gaps_render_at_calculated_height(self):
        self.owner.config = DockConfig.from_dict({'widgets': {
            'shortcuts': {'items': [], 'spacing': {'after': 2}},
            'visualizer': {'enabled': False},
            'progress': {'enabled': False},
            'title': {'spacing': {'after': 3, 'basic_after': None}},
            'controls': {'enabled': False}
        }})
        screen, mouse = self.draw(rows=12)
        self.assertEqual(self.overlay.display_mode, DockMode.BASIC)
        self.assertIn('Player disconnected', self.row(screen, 5))
        self.assertIn('[1]', self.row(screen, 9))
        text = '\n'.join((self.row(screen, y) for y in range(12)))
        self.assertNotIn('▁', text)
        self.assertNotIn(PlaybackAction.PREVIOUS.symbol, text)
        self.assertNotIn('--:--', text)
        with set_app(self.app):
            for y in (6, 7, 8, 10, 11):
                self.assertEqual(self.row(screen, y).strip(), 'EDITOR')
                mouse.mouse_handlers[y][0](MouseEvent(
                    Point(0, y),
                    MouseEventType.MOUSE_UP,
                    MouseButton.LEFT,
                    frozenset()
                ))
        self.assertEqual(len(self.behind_clicks), 5)
        self.draw(rows=7)
        self.assertEqual(self.overlay.display_mode, DockMode.HIDDEN)
        self.draw(rows=8)
        self.assertEqual(self.overlay.display_mode, DockMode.BASIC)

    def test_audio_failure_uses_title_and_keeps_visualizer(self):
        self.owner.audio = AudioSnapshot(status=AudioStatus.UNAVAILABLE, message='Audio unavailable')
        self.owner.playback = PlaybackSnapshot(title='Track')
        self.owner.config = DockConfig()
        screen, _ = self.draw()
        self.assertIn('Audio connection failed', self.row(screen, 4))
        self.assertNotIn('Audio unavailable', self.row(screen, 4))
        self.assertNotIn('Audio unavailable', self.row(screen, 2))
        self.assertIn('▁' * self.owner.config.widgets.visualizer.width, self.row(screen, 2))
        self.assertEqual(self.owner.audio.diagnostic, 'Audio unavailable')
        self.owner.diagnostic = 'Settings error'
        screen, _ = self.draw()
        self.assertIn('Settings error', self.row(screen, 4))

    def test_minimum_height_does_not_draw_outside_container(self):
        for rows in (1, 7):
            screen, _ = self.draw(rows=rows)
            self.assertFalse(any(('플레이어' in self.row(screen, y) for y in range(rows))))
            self.assertTrue(all((y >= 0 for y in screen.data_buffer)))
        screen, _ = self.draw(rows=8)
        self.assertIn('Player', self.row(screen, 3))

    def test_shortcuts_center_and_clicks_never_pass_through(self):
        self.owner.config = DockConfig(widgets={'shortcuts': {'items': (
            Shortcut(1, 'rg', icon='🔎', label='검색', style={'color': '#12ab34'}),
        )}})
        screen, mouse = self.draw()
        y = 7
        line = self.row(screen, y)
        self.assertIn('🔎 검색', line)
        self.assertNotIn('[1]', line)
        self.assertIn('[2]', line)
        self.assertIn('[0]', line)
        x = next((x for x in range(120) if screen.data_buffer[y][x].char == '🔎'))
        self.assertIn('#12ab34', screen.data_buffer[y][x].style)
        click = MouseEvent(Point(x + 1, y), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset())
        with set_app(self.app):
            mouse.mouse_handlers[y][x + 1](click)
        self.assertEqual(self.executed, [1])
        blank = MouseEvent(Point(0, 8), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset())
        with set_app(self.app):
            mouse.mouse_handlers[8][0](blank)
            right_click = MouseEvent(Point(x + 1, y), MouseEventType.MOUSE_UP, MouseButton.RIGHT, frozenset())
            self.assertIsNone(mouse.mouse_handlers[y][x + 1](right_click))
        self.assertEqual(self.executed, [1])
        self.assertEqual(len(self.behind_clicks), 1)

    def test_registered_labels_shrink_without_becoming_numbered_slots(self):
        self.owner.config = DockConfig(
            dock={'width': Width(value=39)},
            widgets={'shortcuts': {'items': tuple((Shortcut(slot, 'git', icon='👩\u200d💻', label='긴 이름' * 10) for slot in Shortcut.slots()))}}
        )
        screen, _ = self.draw(columns=39)
        self.assertNotIn('[', self.row(screen, 7, 39))
        line = self.row(screen, 7, 39)
        self.assertEqual(line.count('…') + line.count('👩\u200d💻'), Shortcut.COUNT)
        self.assertEqual(self.row(screen, 8, 39).strip(), 'EDITOR')

    def test_solid_and_horizontal_gradient_colors_reach_the_screen(self):
        for colors in (('#123456',), ('#ff0000', '#00ff00', '#0000ff')):
            with self.subTest(colors=colors):
                self.owner.config = DockConfig(widgets={'visualizer': VisualizerConfig(style={'colors': colors})})
                self.owner.audio = AudioSnapshot(levels=(1.0,) * len(self.owner.audio.levels))
                screen, _ = self.draw()
                cells = [cell for _, cell in sorted(screen.data_buffer[2].items()) if cell.char == '█']
                self.assertEqual(len(cells), self.owner.config.widgets.visualizer.width)
                expected = self.owner.config.widgets.visualizer.style.palette(len(cells))
                for cell, color in zip(cells, expected):
                    self.assertIn(color, cell.style)
                    self.assertIn('bg:default', cell.style)

    def test_visualizer_length_resizes_centered_with_gradient_endpoints(self):
        self.owner.audio = AudioSnapshot(levels=(1.0,) * len(self.owner.audio.levels))
        for length, columns, expected in (
            (1, 120, 1),
            (12, 120, 12),
            (48, 120, 48),
            (100, 120, 80),
            (48, 39, 39),
            (48, 120, 48)
        ):
            with self.subTest(length=length, columns=columns):
                self.owner.config = DockConfig(widgets={'visualizer': VisualizerConfig(
                    width=length,
                    style={'colors': ('#ff0000', '#0000ff')}
                )})
                screen, _ = self.draw(columns=columns)
                cells = [(x, cell) for x, cell in sorted(screen.data_buffer[2].items()) if cell.char == '█']
                self.assertEqual(len(cells), expected)
                self.assertEqual(cells[0][0], (columns - expected) // 2)
                self.assertIn('#ff0000', cells[0][1].style)
                if expected > 1:
                    self.assertIn('#0000ff', cells[-1][1].style)

    def test_empty_slots_stay_numbered_when_a_registered_text_is_truncated(self):
        self.owner.config = DockConfig(
            dock={'width': Width(value=39)},
            widgets={'shortcuts': {'items': (Shortcut(1, 'git', label='very long label'),)}}
        )
        screen, mouse = self.draw(columns=39)
        line = self.row(screen, 7, 39)
        self.assertIn('ve…', line)
        self.assertNotIn('[1]', line)
        for slot in range(2, 11):
            self.assertIn(Shortcut.compact_label(slot), line)
        x = next((x for x, cell in screen.data_buffer[7].items() if cell.char == 'v'))
        with set_app(self.app):
            mouse.mouse_handlers[7][x](MouseEvent(
                Point(x, 7),
                MouseEventType.MOUSE_UP,
                MouseButton.LEFT,
                frozenset()
            ))
        self.assertEqual(self.executed, [1])

    def test_separator_replaces_number_uses_color_and_consumes_click_without_action(self):
        self.owner.config = DockConfig(widgets={'shortcuts': {'items': (
            Shortcut(1, 'git', label='Git'),
            Separator(2, style={'color': '#c084fc'}),
            Shortcut(3, 'ls', label='Files')
        )}})
        screen, mouse = self.draw(columns=39)
        line = self.row(screen, 7, 39)
        self.assertIn('Git │ Files', line)
        self.assertNotIn('[2]', line)
        self.assertIn('[4]', line)
        x = next((x for x, cell in screen.data_buffer[7].items() if cell.char == '│'))
        self.assertIn('#c084fc', screen.data_buffer[7][x].style)
        with set_app(self.app):
            mouse.mouse_handlers[7][x](MouseEvent(
                Point(x, 7),
                MouseEventType.MOUSE_UP,
                MouseButton.LEFT,
                frozenset()
            ))
        self.assertEqual(self.executed, [])
        self.assertEqual(self.behind_clicks, [])

    def test_wide_text_and_diagnostics_are_clipped_on_cluster_boundaries(self):
        self.assertEqual(list(clusters('a👩\u200d💻🇰🇷é')), ['a', '👩\u200d💻', '🇰🇷', 'é'])
        text = '👩\u200d💻' * 10
        clipped = fit(text, 7)
        self.assertLessEqual(width(clipped), 7)
        self.assertFalse(clipped.rstrip('…').endswith('\u200d'))
        self.owner.playback = PlaybackSnapshot(title='한글 제목' * 100)
        self.owner.diagnostic = 'bad\ntext\x1b[2J'
        screen, _ = self.draw(columns=80)
        self.assertIn('bad text', self.row(screen, 4, 80))

    def test_pty_ratio_uses_pty_width_then_clamps_to_frame(self):
        self.owner.config = DockConfig(dock={'width': Width(mode='ratio', value=0.6)})
        self.assertEqual(self.owner.config.dock.width.resolve(self.owner.pty_columns(), 118), 72)
        self.assertEqual(self.overlay.preferred_height(120, 24).preferred, 0)
        self.owner.visible = False
        screen, _ = self.draw()
        self.assertIn('EDITOR', self.row(screen, 8))

    def test_extreme_playback_times_stay_within_configured_body(self):
        self.owner.config = DockConfig(dock={'width': Width(value=39)})
        self.owner.playback = PlaybackSnapshot(position=1e+100, duration=1e+101)
        screen, _ = self.draw()
        line = self.row(screen, 3)
        self.assertEqual(line[6:40], ' ' * 34)
        self.assertEqual(line[79:], ' ' * 41)

    def test_title_shows_output_applications_until_player_metadata_is_available(self):
        self.owner.audio = AudioSnapshot(applications=('Firefox', 'VLC'))
        screen, _ = self.draw()
        self.assertIn('Now playing: Firefox · VLC', self.row(screen, 4))
        self.owner.playback = PlaybackSnapshot(title='현재 곡', artist='가수')
        screen, _ = self.draw()
        self.assertIn('가수 — 현재 곡', self.row(screen, 4))
        self.assertNotIn('Firefox', self.row(screen, 4))

    def test_title_prefix_color_and_diagnostic_override(self):
        self.owner.config = DockConfig(theme={'colors': {'cyan': '#abcdef'}}, widgets={'title': TitleConfig(style={'color': 'cyan'})})
        self.owner.audio = AudioSnapshot(applications=('VLC',))
        screen, _ = self.draw()
        self.assertIn('Now playing: VLC', self.row(screen, 4))
        cell = screen.data_buffer[4][self.row(screen, 4).index('Now playing: VLC')]
        self.assertIn('#abcdef', cell.style)
        self.owner.diagnostic = 'diagnostic'
        screen, _ = self.draw()
        self.assertIn('diagnostic', self.row(screen, 4))

    def test_progress_color_length_and_resize(self):
        self.owner.playback = PlaybackSnapshot(position=5, duration=10)
        for length, columns, expected in ((20, 120, 20), (100, 39, 27), (100, 120, 68)):
            with self.subTest(length=length, columns=columns):
                self.owner.config = DockConfig(theme={'colors': {'cyan': '#abcdef'}}, widgets={'progress': ProgressConfig(width=length, style={'color': 'cyan'})})
                screen, _ = self.draw(columns=columns)
                cells = [cell for _, cell in sorted(screen.data_buffer[3].items()) if cell.char in ('━', '●', '─')]
                self.assertEqual(''.join(cell.char for cell in cells),
                                 '━' * (expected // 2) + '●' + '─' * ((expected - 1) // 2))
                theme = Style.from_dict({"": "#223344"})
                for cell in cells:
                    self.assertEqual(theme.get_attrs_for_style_str(cell.style).color,
                                     'abcdef' if cell.char == '─' else 'default')

    def test_progress_handle_fits_at_start_end_and_in_one_cell(self):
        for length, position, duration, expected in ((5, 0, None, "●────"), (5, 5, 5, "━━━━●"),
                                                    (1, 5, 5, "●")):
            with self.subTest(expected=expected):
                self.owner.config = DockConfig(widgets={"progress": {"width": length}})
                self.owner.playback = PlaybackSnapshot(position=position, duration=duration)
                screen, _ = self.draw()
                self.assertEqual("".join(c for c in self.row(screen, 3) if c in "━●─"), expected)

    def test_large_vertical_gaps_fit_or_fall_back_without_allocating_hidden_rows(self):
        self.owner.requested_mode = DockMode.MAXIMIZED
        for widget, rows, mode in (("weather", 10**100, DockMode.BASIC),
                                   ("visualizer", 10**100, DockMode.HIDDEN),
                                   ("visualizer", 21, DockMode.MAXIMIZED)):
            with self.subTest(widget=widget, rows=rows):
                self.owner.config = DockConfig(widgets={widget: {"spacing": {"after": rows}}})
                self.draw(rows=70)
                self.assertEqual(self.overlay.display_mode, mode)

    def test_unset_button_color_resolves_to_terminal_foreground_on_screen(self):
        terminal_style = Style.from_dict({"": "#abcdef"})
        for button_style in ({}, {"color": "foreground"}, {"color": "cyan"}):
            self.owner.config = DockConfig(theme={"colors": {"cyan": "#112233"}}, widgets={"controls": {"buttons": [
                {"action": "play", "text": "PLAY", "style": button_style}]}})
            screen, _ = self.draw()
            cell = next(cell for cell in screen.data_buffer[5].values() if cell.char == "P")
            expected = "112233" if button_style.get("color") == "cyan" else "default"
            attrs = terminal_style.get_attrs_for_style_str(cell.style)
            self.assertEqual(attrs.color, expected)
            if expected == "default":
                self.assertFalse(attrs.dim)

    def test_transport_button_order_custom_text_color_and_hiding(self):
        self.owner.config = DockConfig(theme={'colors': {'red': '#ff0000', 'green': '#00ff00'}}, widgets={'controls': TransportConfig(buttons=(
            TransportButton('stop', text='STOP', style={'color': 'red'}),
            TransportButton('play', text='GO', style={'color': 'green'})
        ))})
        screen, mouse = self.draw()
        self.assertIn('STOP   GO', self.row(screen, 5))
        x = next((x for x, cell in screen.data_buffer[5].items() if cell.char == 'S'))
        self.assertIn('#ff0000', screen.data_buffer[5][x].style)
        with set_app(self.app):
            mouse.mouse_handlers[5][x](MouseEvent(
                Point(x, 5),
                MouseEventType.MOUSE_UP,
                MouseButton.LEFT,
                frozenset()
            ))
        self.assertEqual(self.executed, [])
        self.assertEqual(self.behind_clicks, [])
        self.owner.config = DockConfig(widgets={'controls': TransportConfig(buttons=())})
        screen, _ = self.draw()
        self.assertNotIn('STOP', self.row(screen, 5))
        self.assertIn('EDITOR', self.row(screen, 5))

    def test_long_transport_labels_stay_inside_one_centered_row(self):
        self.owner.config = DockConfig(widgets={
            'shortcuts': {'items': ()},
            'controls': TransportConfig(buttons=tuple((TransportButton(action, text='버튼' * 100) for action in PlaybackAction)))
        })
        screen, _ = self.draw(columns=39)
        self.assertEqual(self.row(screen, 5, 39).count('…'), len(PlaybackAction))
        self.assertIn('[1]', self.row(screen, 7, 39))

    def test_shortcut_padding_spacing_zero_and_narrow_width(self):
        for padding, columns, expected in ((3, 120, 3), (0, 120, 0), (10 ** 100, 39, 1)):
            with self.subTest(padding=padding, columns=columns):
                self.owner.config = DockConfig(widgets={'shortcuts': ShortcutsConfig(items=(), spacing={'gap': padding})})
                screen, _ = self.draw(columns=columns)
                text = (' ' * expected).join((Shortcut.compact_label(slot) for slot in Shortcut.slots()))
                self.assertIn(text, self.row(screen, 7, columns))
