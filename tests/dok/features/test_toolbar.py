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
                                   "system": {"order": ["network", "cpu"], "cpu": {"graph": {"columns": 20}}}})
        self.assertEqual(config.toolbar.system.cpu.label, ToolbarConfig().system.cpu.label)
        self.assertEqual(DockConfig.from_dict(Jsonc.loads(Jsonc.dumps(config))), config)
        jsonschema.validate(config.to_dict(), DockConfig.schema())
        for data in ({"system": {"order": ["cpu", "cpu"]}}, {"numbers": {"decimal_places": 9}},
                     {"date": {"weekdays": ["Mon"]}}, {"date": {"format": "%Q"}}):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    ToolbarConfig.from_dict(data)
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.validate(data, ToolbarConfig.schema())

    def test_date_shared_timezone_format_and_actual_visibility(self):
        date = ToolbarConfig().date
        self.assertEqual(date.render(NOW), "2026-11-06(금)")
        self.assertTrue(date.visible(False))
        self.assertFalse(date.visible(True))
        self.assertTrue(replace(date, hide_when_dock_visible=False).visible(True))
        self.assertEqual(replace(date, format="%%a %a").render(NOW), "%a 금")
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
        self.assertTrue(wide.startswith("2026-11-06(금)"))
        self.assertTrue(shown.startswith("Len:"))
        self.assertTrue(wide.endswith("↑1 KiB/s"))
        self.assertTrue(shown.endswith("↑1 KiB/s"))

    def test_graphs_are_bounded_and_unknown_samples_leave_gaps(self):
        graph = MetricFormat.line((0, 100, None, 50), 2)
        self.assertEqual(width(graph), 2)
        self.assertEqual(ord(graph[0]), 0x2800 + 0x40 + 0x08)
        self.assertEqual(MetricFormat.line((), 0), "")
        self.assertEqual(MetricFormat.bar(50, 4), "━━──")
        self.assertEqual(MetricFormat.bytes(1024, rate=True), "1 KiB/s")

    def test_disabled_sections_do_not_leave_separators(self):
        view = ToolbarView()
        config = ToolbarConfig(date={"enabled": False}, input={"enabled": False}, numbers={"enabled": False}, system={"enabled": False})
        result = "".join(text for _, text in view.render(config, EditorSnapshot(), SAMPLE, (), NOW, False, 40))
        self.assertEqual(result, " " * 40)

    def test_empty_optional_text_does_not_displace_input_on_a_narrow_toolbar(self):
        config = ToolbarConfig(date={"format": "%a", "weekdays": [""] * 7},
            input={"items": [{"field": "length", "label": "Len:"}]},
            numbers={"label": "", "empty_text": ""}, system={"enabled": False})
        fragments = ToolbarView().render(config, EditorSnapshot(), SAMPLE, (), NOW, False, 5)
        self.assertEqual("".join(text for _, text in fragments), "Len:0")
