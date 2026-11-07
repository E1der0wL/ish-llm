"""Toolbar owners, decimal arithmetic, narrow layouts and graphs."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import unittest

import jsonschema

from dok import DockConfig, ToolbarConfig
from dok.features.system.models import SystemSnapshot
from dok.features.toolbar.editor import EditorSnapshot, NumberCalculator, NumberScope
from dok.features.toolbar.view import MetricFormat, ToolbarView
from dok.features.toolbar.config import Metric
from dok.rendering.text import width
from dok.settings.jsonc import Jsonc


NOW = datetime(2026, 11, 6, 13, 30, tzinfo=timezone.utc)
SAMPLE = SystemSnapshot(1, 25, 4 * 1024**3, 16 * 1024**3, 1024**2, 1024)


class NumberTests(unittest.TestCase):
    def test_selected_numbers_and_decimal_rounding_are_exact(self):
        text = "ignore999 apple100 pear-20.5 grape1,200.50 .5 1, 2, 3 0.1 0.2"
        editor = EditorSnapshot(text=text, selection=((10, len(text)),))
        calc = NumberCalculator()
        result = calc.calculate(editor, NumberScope.SELECTION)
        self.assertEqual(result.count, 9)
        self.assertEqual(result.total, Decimal("1286.8"))
        self.assertIs(result, calc.calculate(replace(editor, column=20), NumberScope.SELECTION))
        self.assertEqual(calc.format(Decimal("2.345"), 2), "2.35")
        self.assertEqual(calc.format(Decimal("-0.001"), 2), "0")
        self.assertEqual(calc.format(Decimal("100"), 0), "100")

    def test_empty_selection_whole_input_and_disjoint_block_ranges(self):
        calc = NumberCalculator()
        editor = EditorSnapshot(text="10 900\n20 800")
        self.assertEqual(calc.calculate(editor, NumberScope.SELECTION).count, 0)
        self.assertEqual(calc.calculate(editor, NumberScope.INPUT).total, 1730)
        result = calc.calculate(replace(editor, selection=((0, 2), (7, 9))), NumberScope.SELECTION)
        self.assertEqual((result.total, result.average), (30, 15))

    def test_malformed_runs_are_not_partially_counted_and_limits_are_explicit(self):
        calc = NumberCalculator()
        result = calc.calculate(EditorSnapshot(text="127.0.0.1 12,34 1e3 10"), NumberScope.INPUT)
        self.assertEqual((result.count, result.total), (1, 10))
        for text in ("1" * (calc.MAX_DIGITS + 1), " " * (calc.MAX_CHARACTERS + 1), "1 " * (calc.MAX_NUMBERS + 1)):
            result = calc.calculate(EditorSnapshot(text=text), NumberScope.INPUT)
            self.assertTrue(result.limited)
            self.assertEqual(result.count, 0)


class ToolbarTests(unittest.TestCase):
    def test_owner_schema_and_jsonc_share_nested_defaults_and_validation(self):
        config = DockConfig(toolbar={"date": {"weekdays": ["M", "T", "W", "T", "F", "S", "S"]},
                                   "system": {"order": ["network", "cpu"], "cpu": {"graph": {"width": 20}}}})
        self.assertEqual(config.toolbar.system.cpu.label, ToolbarConfig().system.cpu.label)
        self.assertEqual(DockConfig.from_dict(Jsonc.loads(Jsonc.dumps(config))), config)
        jsonschema.validate(config.to_dict(), DockConfig.schema())
        for data in ({"system": {"order": ["cpu", "cpu"]}}, {"numbers": {"decimals": -1}},
                     {"system": {"cpu": {"value_width": 0}}},
                     {"system": {"network": {"value_width": 2.5}}},
                     {"date": {"weekdays": ["Mon"]}}, {"date": {"format": "%Q"}}):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    ToolbarConfig.from_dict(data)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(data, ToolbarConfig.schema())

    def test_large_layout_values_roundtrip_and_paint_only_the_available_cells(self):
        huge = 10 ** 100
        view = ToolbarView()
        for layout in ({"width": huge}, {"gap": huge},
                       {"pad_left": huge}, {"pad_right": huge}):
            config = ToolbarConfig(gap=huge, numbers={"decimals": huge},
                regions={name: layout for name in ("size", "cursor", "numbers", "cpu", "network")},
                input={"items": [{"field": "line"}] * 20},
                system={"cpu": {"graph": {"width": huge}, "value_width": huge}})
            with self.subTest(layout=layout):
                jsonschema.validate(config.to_dict(), ToolbarConfig.schema())
                self.assertEqual(ToolbarConfig.from_dict(Jsonc.loads(Jsonc.dumps(config))), config)
                for columns in (1, 40, 300):
                    parts = view.render(config, EditorSnapshot(text="1.123456789876"), SAMPLE, (), NOW, False, columns)
                    self.assertEqual(sum(width(text) for _, text in parts), columns)
        config = ToolbarConfig(date={"enabled": False}, system={"enabled": False}, numbers={"enabled": False},
            regions={"size": {"gap": 72}}, input={"items": [
                {"field": "length", "label": "L", "value_width": 1},
                {"field": "lines", "label": "N", "value_width": 1}]})
        text = "".join(s for _, s in view.render(config, EditorSnapshot(), SAMPLE, (), NOW, False, 85))
        self.assertTrue(text.startswith("L0" + " " * 72 + "N1"))

    def test_date_shared_timezone_format_and_actual_visibility(self):
        date = ToolbarConfig().date
        self.assertEqual(date.render(NOW), "2026-11-06(Fri)")
        self.assertTrue(date.visible(False))
        self.assertFalse(date.visible(True))
        self.assertTrue(replace(date, hide_with_dock=False).visible(True))
        self.assertEqual(replace(date, format="%%a %a").render(NOW), "%a Fri")
        self.assertEqual(DockConfig(clock={"timezone": "Asia/Seoul"}).clock.now().utcoffset().total_seconds(), 9 * 3600)

    def test_toolbar_and_dashboard_share_clock_cadence_with_escaped_percent(self):
        config = DockConfig()
        for fmt, seconds in (("%H:%M", False), ("%H:%M:%S", True), ("%%S", False), ("%%%S", True)):
            with self.subTest(fmt=fmt):
                toolbar = replace(config.toolbar.date, format=fmt).schedule(config.clock)
                dashboard = config.clock.schedule(replace(config.widgets.date, format=fmt),
                                                  replace(config.widgets.time, enabled=False))
                self.assertEqual(toolbar, dashboard)
                self.assertEqual(toolbar.seconds, seconds)

    def test_all_widths_are_one_row_and_system_stays_at_the_right_edge(self):
        view, config = ToolbarView(), ToolbarConfig()
        for columns in (1, 2, 5, 10, 20, 40, 80, 120, 200):
            with self.subTest(columns=columns):
                fragments = view.render(config, EditorSnapshot(text="한글🙂"), SAMPLE, (SAMPLE,), NOW, False, columns)
                text = "".join(part for _, part in fragments)
                self.assertEqual(width(text), columns)
                self.assertNotIn("\n", text)
                self.assertFalse(text.endswith(" "))
                self.assertTrue(all("noreverse" in style and "bg:default" in style for style, _ in fragments))
        wide = "".join(text for _, text in view.render(config, EditorSnapshot(), SAMPLE, (SAMPLE,), NOW, False, 220))
        shown = "".join(text for _, text in view.render(config, EditorSnapshot(), SAMPLE, (SAMPLE,), NOW, True, 220))
        self.assertTrue(wide.startswith("2026-11-06(Fri)    Len:"))
        self.assertTrue(shown.startswith("Len:"))
        self.assertTrue(wide.endswith("↓1 MiB/s ↑1 KiB/s"))
        self.assertTrue(shown.endswith("↓1 MiB/s ↑1 KiB/s"))

    def test_graphs_are_bounded_and_unknown_samples_leave_gaps(self):
        graph = MetricFormat.line((0, 100, None, 50), 2)
        self.assertEqual(width(graph), 2)
        self.assertEqual(ord(graph[0]), 0x2800 + 0x40 + 0x08)
        self.assertEqual(MetricFormat.line((), 0), "")
        self.assertEqual(MetricFormat.bar(50, 4), "━━──")
        self.assertEqual(MetricFormat.bytes(1024, rate=True), "1 KiB/s")

    def test_cpu_graph_fills_startup_history_without_fabricating_measurements(self):
        unknown = SystemSnapshot()
        for history in ((), (unknown,)):
            self.assertEqual(MetricFormat.graph(Metric.CPU, unknown, history, 4), '▁' * 4)
            self.assertEqual(MetricFormat.percent(unknown.cpu_percent), '—')
        history = (replace(SAMPLE, cpu_percent=100), unknown)
        graph = MetricFormat.graph(Metric.CPU, unknown, history, 4)
        self.assertEqual(graph, '▁▁█ ')
        self.assertEqual(MetricFormat.graph(Metric.NETWORK, unknown, (), 4), '\u2800' * 4)
        for columns in (0, 1, 40):
            self.assertEqual(width(MetricFormat.graph(Metric.CPU, SAMPLE, (SAMPLE,), columns)), columns)

    def test_cpu_graph_distinguishes_eight_levels_and_keeps_recent_samples(self):
        samples = tuple(replace(SAMPLE, cpu_percent=value)
                        for value in (0, 12.5, 25, 37.5, 50, 62.5, 75, 87.5))
        self.assertEqual(MetricFormat.graph(Metric.CPU, samples[-1], samples, 8), '▁▂▃▄▅▆▇█')
        self.assertEqual(MetricFormat.graph(Metric.CPU, samples[-1], samples, 3), '▆▇█')
        self.assertEqual(MetricFormat.cpu_history((-5, 12.49, 12.5, 99.9, 100, 120), 6), '▁▁▂███')
        self.assertEqual(MetricFormat.cpu_history((None, 25, None, 50), 5), '▁▁▃ ▅')

    def test_metric_positions_stay_fixed_as_values_and_units_change(self):
        view = ToolbarView()
        config = ToolbarConfig(system={"cpu": {"label": "CPU"}, "memory": {"label": "MEM"}})
        samples = (
            (SystemSnapshot(), "—"),
            (replace(SAMPLE, cpu_percent=1, memory_used=0, received_per_second=0), "0%"),
            (SAMPLE, "25%"),
            (replace(SAMPLE, cpu_percent=100, memory_used=SAMPLE.memory_total,
                     received_per_second=1023.9 * 1024**2, sent_per_second=1023.9 * 1024**3), "100%"),
        )
        for columns in (61, 200):
            positions = []
            for sample, memory_percent in samples:
                fragments = view.render(config, EditorSnapshot(), sample, (sample,), NOW, False, columns)
                line = "".join(text for _, text in fragments)
                positions.append(tuple(width(line[:line.index(label)]) for label in ("CPU", "MEM", config.system.network.label)))
                memory = next(text for _, text in fragments if text.startswith("MEM "))
                self.assertEqual(memory.split("─")[0].split("━")[0].rstrip(), f"MEM {memory_percent:>4}")
                self.assertEqual(width(line), columns)
            self.assertEqual(len(set(positions)), 1)

    def test_generated_toolbar_has_equal_metric_widths_and_four_cell_group_gaps(self):
        config = DockConfig.from_dict(Jsonc.loads(Jsonc.dumps(DockConfig.initial()))).toolbar
        self.assertEqual(config.gap, 4)
        self.assertEqual([group.gap for group in
                          (config.regions.size, config.regions.cursor, config.regions.numbers)], [2, 2, 2])
        view = ToolbarView()
        for snapshot in (SystemSnapshot(), SAMPLE, replace(SAMPLE, received_per_second=1023.9 * 1024**2,
                                                           sent_per_second=1023.9 * 1024**3)):
            fragments = view.render(config, EditorSnapshot(), snapshot, (snapshot,), NOW, False, 240)
            line = "".join(text for _, text in fragments)
            self.assertTrue(line.startswith(f" 2026-11-06(Fri)    Len:{0:<5}  Lines:{1:<5}"
                                            f"    Ln:{1:<5}  Col:{1:<5}  Pos:{1:<5}    Total:  —"))
            starts = [width(line[:line.index(item.label)]) for _, item in config.system.items()]
            boundary = width(config.separator)
            self.assertEqual([starts[1] - starts[0] - boundary, starts[2] - starts[1] - boundary,
                              width(line) - starts[2] - config.pad_right], [19, 19, 19])
            net = line[line.index(config.system.network.label):]
            self.assertIn("↓", net)
            self.assertIn("↑", net)
            self.assertNotIn("…", net)

    def test_custom_value_widths_align_clip_and_fit_a_narrow_terminal(self):
        config = ToolbarConfig(system={"order": ["memory", "network"],
            "memory": {"label": "MEM", "value_width": 6, "graph": {"enabled": False}},
            "network": {"label": "NET", "value_width": 7}, "download_label": "수신"})
        view = ToolbarView()
        fragments = view.render(config, EditorSnapshot(), SAMPLE, (), NOW, False, 100)
        self.assertIn("MEM    25%", [text for _, text in fragments])
        network = next(text for _, text in fragments if text.startswith("NET "))
        self.assertEqual(width(network), width("NET ") + 7)
        self.assertTrue(network.endswith("…"))
        fragments = view.render(config, EditorSnapshot(), SAMPLE, (), NOW, False, 7)
        self.assertEqual("".join(text for _, text in fragments), "MEM 25%")

    def test_outer_padding_defaults_initial_file_and_validation(self):
        defaults = ToolbarConfig()
        self.assertEqual((defaults.pad_left, defaults.pad_right, defaults.background),
                         (0, 0, 'background'))
        initial = DockConfig.from_dict(Jsonc.loads(Jsonc.dumps(DockConfig.initial()))).toolbar
        self.assertEqual((initial.pad_left, initial.pad_right, initial.background),
                         (1, 1, '#1d2035'))
        for field in ('pad_left', 'pad_right'):
            for value in (-1, True, 1.5, None):
                raw = {field: value}
                with self.subTest(raw=raw):
                    with self.assertRaises(ValueError):
                        ToolbarConfig.from_dict(raw)
                    with self.assertRaises(jsonschema.ValidationError):
                        jsonschema.validate(raw, ToolbarConfig.schema())
        huge = ToolbarConfig(pad_left=10**100, pad_right=10**100)
        jsonschema.validate(huge.to_dict(), ToolbarConfig.schema())
        self.assertEqual(huge.split_width(3), (3, 0, 0))
        self.assertEqual(ToolbarConfig.from_dict(huge.to_dict()), huge)

    def test_outer_padding_preserves_inner_layout_right_alignment_and_background(self):
        from dok import Theme
        theme = Theme(colors={'blue': '#112233'})
        view = ToolbarView()
        base = ToolbarConfig(background='#1D2035', regions={'cpu': {'style': {'background': 'blue'}}})
        for padding_left, padding_right in ((0,0), (1,1), (2,3), (10**100,10**100)):
            config = replace(base, pad_left=padding_left, pad_right=padding_right)
            for columns in (0,1,2,3,7,40,240):
                with self.subTest(left=padding_left, right=padding_right, columns=columns):
                    parts = view.render(config, EditorSnapshot(), SAMPLE, (SAMPLE,), NOW, False, columns, theme=theme)
                    text = ''.join(s for _, s in parts)
                    left, content, right = config.split_width(columns)
                    inner = ''.join(s for _, s in view.render(base, EditorSnapshot(), SAMPLE, (SAMPLE,), NOW, False, content, theme=theme))
                    self.assertEqual(text, ' ' * left + inner + ' ' * right)
                    self.assertEqual(width(text), columns)
                    if columns:
                        self.assertIn('bg:#1d2035', parts[0][0])
                        self.assertIn('bg:#1d2035', parts[-1][0])
        parts = view.render(replace(base, pad_left=2, pad_right=3),
                            EditorSnapshot(), SAMPLE, (), NOW, False, 240, theme=theme)
        self.assertTrue(any('bg:#112233' in style and text for style, text in parts))
        self.assertTrue(''.join(s for _, s in parts).endswith('↓1 MiB/s ↑1 KiB/s   '))

    def test_disabled_sections_do_not_leave_separators(self):
        view = ToolbarView()
        config = ToolbarConfig(date={"enabled": False}, input={"enabled": False}, numbers={"enabled": False}, system={"enabled": False})
        result = "".join(text for _, text in view.render(config, EditorSnapshot(), SAMPLE, (), NOW, False, 40))
        self.assertEqual(result, " " * 40)

    def test_numeric_region_defaults_roundtrip_and_show_long_values(self):
        for config in (DockConfig(), DockConfig.from_dict(Jsonc.loads(Jsonc.dumps(DockConfig.initial())))):
            toolbar = config.toolbar
            self.assertIsNone(toolbar.numbers.decimals)
            for group in (toolbar.regions.size, toolbar.regions.cursor, toolbar.regions.numbers):
                self.assertEqual(group.align, "left")
                self.assertIsNone(group.digits)
            editor = EditorSnapshot(text="12345678.987654", length=1234567, lines=2, position=234567)
            text = "".join(v for _, v in ToolbarView().render(toolbar, editor, SAMPLE, (), NOW, False, 250))
            self.assertIn("Len:1234567  Lines:2", text)
            self.assertIn("Pos:234567", text)
            self.assertIn("Total:  12,345,678.987654(12,345,678.987654)", text)

    def test_numeric_policy_is_independent_for_each_group(self):
        toolbar = ToolbarConfig(date={"enabled": False}, system={"enabled": False}, regions={
            "size": {"align": "right", "digits": 3},
            "cursor": {"align": "left", "digits": 2},
            "numbers": {"align": "right", "digits": 3}})
        editor = EditorSnapshot(text="-12345 -12345", length=12345, lines=2, line=4, column=5, position=123456)
        text = "".join(v for _, v in ToolbarView().render(toolbar, editor, SAMPLE, (), NOW, False, 150))
        self.assertIn("Len:12.3K  Lines:    2", text)
        self.assertIn("Ln:4      Col:5      Pos:12...", text)
        self.assertIn("Total:  " + "-24.7K(-12.3K)".rjust(15), text)
        self.assertEqual(width(text), 150)
        options = {"regions": {"numbers": {"align": "left", "digits": 2}}}
        summary = NumberCalculator().calculate(EditorSnapshot(text="-123456 0"), NumberScope.INPUT)
        toolbar = ToolbarConfig.from_dict(options)
        self.assertEqual(toolbar.numbers.render(summary, layout=toolbar.regions.numbers).strip(), "Total: -12...(-62K)")
        for invalid in ({"align": "center"}, {"digits": 0}, {"digits": True}):
            data = {"regions": {"cursor": invalid}}
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                ToolbarConfig.from_dict(data)
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(data, ToolbarConfig.schema())

    def test_long_counts_and_summary_fit_without_losing_the_sign_or_parentheses(self):
        config = ToolbarConfig(date={"enabled": False}, system={"enabled": False},
            input={"items": [{"field": "length", "label": "Len:", "value_width": 5}]},
            numbers={"value_width": 11})
        editor = EditorSnapshot(text="-1234567 0", length=1234567)
        line = "".join(text for _, text in ToolbarView().render(config, editor, SAMPLE, (), NOW, False, 60))
        self.assertTrue(line.startswith("Len:1.23M    Total: "), line)
        exact = ToolbarView().render(config, replace(editor, length=12345), SAMPLE, (), NOW, False, 60)
        self.assertTrue("".join(text for _, text in exact).startswith("Len:12345    Total: "))
        self.assertIn("Total:  -1M(-617K)", line)
        self.assertEqual(width(line), 60)

    def test_region_width_padding_internal_gap_and_background_cover_exact_cells(self):
        from dok import Theme
        theme = Theme(colors={"black": "#101010", "blue": "#111111", "purple": "#222222", "red": "#ff0000"})
        config = ToolbarConfig(background="black", date={"enabled": False}, numbers={"enabled": False},
            input={"items": [{"field": "length", "label": "L", "value_width": 1},
                             {"field": "lines", "label": "N", "value_width": 1}]},
            system={"order": ["cpu"], "cpu": {"label": "C", "value_width": 4}},
            regions={"size": {"width": 10, "gap": 3, "pad_left": 2,
                              "pad_right": 1, "style": {"background": "blue", "color": "red"}},
                     "cpu": {"width": 12, "gap": 2, "pad_left": 1,
                             "pad_right": 2, "style": {"background": "purple"}}})
        view = ToolbarView()
        for columns in (1, 5, 12, 20, 40, 100):
            fragments = view.render(config, EditorSnapshot(), SAMPLE, (SAMPLE,), NOW, False, columns, theme=theme)
            self.assertEqual(sum(width(text) for _, text in fragments), columns)
        left = "".join(text for style, text in fragments if "bg:#111111" in style)
        right = "".join(text for style, text in fragments if "bg:#222222" in style)
        self.assertEqual(left, "  L0   N1    ")
        self.assertEqual(width(right), 15)
        self.assertTrue(right.startswith(" C   25%  "))
        self.assertTrue(right.endswith("  "))
        self.assertTrue(all("#ff0000" in style for style, _ in fragments if "bg:#111111" in style))
        self.assertEqual(sum(width(text) for style, text in fragments if "bg:#101010" in style), 72)

    def test_empty_optional_text_does_not_displace_input_on_a_narrow_toolbar(self):
        config = ToolbarConfig(date={"format": "%a", "weekdays": [""] * 7},
            input={"items": [{"field": "length", "label": "Len:"}]},
            numbers={"label": "", "empty": ""}, system={"enabled": False})
        fragments = ToolbarView().render(config, EditorSnapshot(), SAMPLE, (), NOW, False, 5)
        self.assertEqual("".join(text for _, text in fragments), "Len:0")
