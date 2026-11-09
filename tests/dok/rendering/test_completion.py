"""Cursor fitting and the prefix/mode-only registration form."""
from pathlib import Path
import shlex
from types import SimpleNamespace
import unittest

from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text.utils import split_lines

from dok import DockConfig
from dok.features.completion.models import MergeMode
from dok.features.completion.runtime import CompletionRuntime
from dok.features.completion.view import CompletionPage
from dok.locales import Locale
from dok.rendering.popups import PopupSurface
from dok.rendering.text import width


class CompletionRenderingTests(unittest.TestCase):
    def page(self, submitted):
        runtime = CompletionRuntime(Path("unused"), lambda catalog: None, lambda: None, None)
        host = SimpleNamespace(prefix=lambda text: tuple(shlex.split(text)), suggest_prefix=lambda _: "tool")
        return CompletionPage(runtime, host, submitted.append, lambda: None, command="tool docs",
                              locale=lambda: Locale.resolve("ko"))

    def test_cursor_geometry_prefers_below_flips_above_and_clamps_right(self):
        panel = DockConfig().completion.panel
        below = panel.fit_cursor(100, 40, 10, 2)
        self.assertEqual(below.top, 3)
        above = panel.fit_cursor(100, 40, 98, 38)
        self.assertLessEqual(above.top + above.height, 38)
        self.assertLessEqual(above.left + above.width, 100)
        for columns, rows in ((1, 1), (20, 8), (40, 16)):
            rect = panel.fit_cursor(columns, rows, columns - 1, rows - 1)
            self.assertGreaterEqual(rect.left, 0)
            self.assertGreaterEqual(rect.top, 0)
            self.assertLessEqual(rect.left + rect.width, columns)
            self.assertLessEqual(rect.top + rect.height, rows)

    def test_surface_has_only_prefix_and_mode_without_executing_while_drawing(self):
        submitted = []
        page = self.page(submitted)
        surface = PopupSurface(lambda: None, lambda: None, modal=True)
        surface.page = page
        config = DockConfig()
        for columns, rows in ((1, 1), (12, 3), (60, 12)):
            rect = config.completion.panel.fit_cursor(columns, rows, columns - 1, 1)
            surface.draw(page.content(), config.completion.panel, rect, theme=config.theme, locale=Locale.resolve("ko"))
            drawn = list(split_lines(surface.fragments))
            self.assertLessEqual(len(drawn), rect.height)
            self.assertTrue(all(sum(width(part[1]) for part in row) <= rect.width for row in drawn))
        text = "\n".join("".join(part[1] for part in row) for row in drawn)
        self.assertIn("접두사", text)
        self.assertIn("● 병합", text)
        self.assertIn("○ 교체", text)
        for removed in ("관리", "등록", "취소", "Esc", "tool docs"):
            self.assertNotIn(removed, text)
        self.assertEqual(submitted, [])

    def test_editing_and_mode_selection_only_submit_on_enter_and_escape_cancels(self):
        submitted = []
        page = self.page(submitted)
        page.prefix.document = Document("tool sub", 8)
        self.assertFalse(page.key("escape", ""))
        self.assertEqual(submitted, [])
        page.key("tab", "")
        self.assertEqual(page.mode, MergeMode.REPLACE)
        page.key("any", " more")
        page.key("left", "")
        self.assertEqual(page.prefix.cursor_position, len("tool sub more") - 1)
        self.assertEqual(page.mode, MergeMode.REPLACE)
        self.assertEqual(submitted, [])
        page.key("enter", "")
        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0].prefix, ("tool", "sub", "more"))
        self.assertEqual(submitted[0].command, "tool docs")
        self.assertEqual(submitted[0].mode, MergeMode.REPLACE)
        page.key("s-tab", "")
        self.assertEqual(page.mode, MergeMode.MERGE)
        page.key("tab", "")
        self.assertEqual(page.mode, MergeMode.REPLACE)
        page.key("home", "")
        page.key("delete", "")
        self.assertEqual(page.prefix.text, "ool sub more")
        self.assertEqual(page.command, "tool docs")
        self.assertEqual(len(submitted), 1)
