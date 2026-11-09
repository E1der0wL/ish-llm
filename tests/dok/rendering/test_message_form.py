"""Shared confirmation layout, scrolling, Unicode and action hit targets."""
import unittest
from unittest.mock import Mock

from prompt_toolkit.data_structures import Point
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

from dok import Theme
from dok.core.theme import ThemeRole
from dok.popups.models import PopupAction
from dok.rendering.message_form import MessageForm
from dok.rendering.presentation import TextRow, TextSpan
from dok.rendering.text import width


class MessageFormTests(unittest.TestCase):
    def setUp(self):
        self.form, self.theme = MessageForm(), Theme()
        self.apply, self.cancel = Mock(), Mock()
        self.actions = (PopupAction("Delete", self.apply), PopupAction("Cancel", self.cancel))
        self.message = TextRow.plain("Delete this entry?", ThemeRole.WARNING)

    @staticmethod
    def texts(rows):
        return ["".join(item[1] for item in row).rstrip() for row in rows]

    def draw(self, **kwargs):
        return self.form.draw(self.message, 40, 12, self.theme, actions=self.actions, **kwargs)

    def test_fixed_heading_footer_and_one_row_padding(self):
        rows = self.draw(heading="Review", details=(TextRow.plain("An entry", ThemeRole.FOREGROUND),))
        text = self.texts(rows)
        self.assertEqual(len(rows), 12)
        self.assertEqual(text[:4], ["Review", "Delete this entry?", "", "An entry"])
        self.assertEqual(text[-3], "")
        self.assertIn("Delete", text[-2])
        self.assertIn("Cancel", text[-2])
        self.assertEqual(text[-1], "")
        for row in rows:
            self.assertEqual(sum(width(item[1]) for item in row), 40)
        self.apply.assert_not_called()
        self.cancel.assert_not_called()

    def test_scrolling_leaves_header_buttons_fixed_and_new_content_resets(self):
        details = tuple(TextRow.plain(f"detail {i}", ThemeRole.MUTED) for i in range(30))
        before = self.draw(heading="Review", details=details)
        self.assertTrue(self.form.key("end"))
        after = self.draw(heading="Review", details=details, selected=1)
        self.assertEqual(self.texts(before)[0], self.texts(after)[0])
        self.assertEqual(self.texts(before)[-3:], self.texts(after)[-3:])
        self.assertIn("detail 29", self.texts(after))
        self.form.key("pageup")
        self.assertNotEqual(self.texts(after), self.texts(self.draw(heading="Review", details=details)))
        changed = self.draw(details=(TextRow.plain("New entry", ThemeRole.MUTED),))
        self.assertEqual(self.texts(changed)[1], "Delete this entry?")
        self.assertFalse(self.form.key("enter"))
        self.apply.assert_not_called()

    def test_compact_layout_preserves_selected_mouse_action_and_bounds(self):
        event = MouseEvent(Point(0, 0), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset())
        for cells in (1, 2, 4, 9, 40):
            for height in (1, 2, 3, 4, 12):
                with self.subTest(cells=cells, height=height):
                    rows = self.form.draw(self.message, cells, height, self.theme, actions=self.actions, selected=1)
                    self.assertEqual(len(rows), height)
                    self.assertTrue(all(sum(width(item[1]) for item in row) == cells for row in rows))
                    clickable = [item for row in rows for item in row if len(item) == 3]
                    self.assertTrue(clickable)
                    clickable[-1][2](event)
        self.apply.assert_not_called()
        self.assertEqual(self.cancel.call_count, 25)

    def test_wrap_keeps_unicode_styles_newlines_and_sanitizes_controls(self):
        message = TextRow((TextSpan("가나다 A\u0301\n", ThemeRole.WARNING, True),
                           TextSpan("끝\x1b[31m", ThemeRole.ERROR)))
        rows = self.form.draw(message, 4, 9, self.theme, heading=None)
        combined = "".join(self.texts(rows))
        self.assertIn("가나다", combined)
        self.assertIn("A\u0301", combined)
        self.assertIn("끝", combined)
        self.assertNotIn("\x1b", combined)
        self.assertTrue(any("bold" in item[0] for row in rows for item in row if "가" in item[1]))
        self.assertTrue(all(sum(width(item[1]) for item in row) == 4 for row in rows))

    def test_enclosing_pane_heading_is_not_reserved_twice(self):
        rows = self.draw(heading=None, background=ThemeRole.EDITOR_BG)
        self.assertEqual(self.texts(rows)[0], "Delete this entry?")
        self.assertEqual(self.texts(rows)[-1], "")
        self.assertTrue(all(item[0] == self.theme.span(ThemeRole.FOREGROUND, background=ThemeRole.EDITOR_BG)
                            for item in rows[-1]))

    def test_loading_message_needs_no_action_or_footer(self):
        rows = self.form.draw(TextRow.plain("Loading…", ThemeRole.MUTED), 20, 5, self.theme)
        self.assertEqual(self.texts(rows), ["", "Loading…", "", "", ""])
        self.assertFalse(any(len(item) == 3 for row in rows for item in row))
        self.assertEqual(self.form.draw(self.message, 0, 5, self.theme), [])
