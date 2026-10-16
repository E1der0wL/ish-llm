"""Notification geometry, wrapping and cache invalidation through real PTK cells."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from hub.asset.icon import UNICODE
from hub.config.general import GeneralSettings
from hub.locales import Language
from hub.widget.feedback.notifications import SessionToasts
from tests.hub.support import render_container


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.columns, self.rows = 100, 40
        self.view = SimpleNamespace(t=Language(), general=GeneralSettings(),
                                    _columns=lambda: self.columns, _rows=lambda: self.rows)
        self.toasts = SessionToasts(self.view)
        self.toasts.app = SimpleNamespace(invalidate=Mock())

    def tearDown(self):
        self.toasts.close()

    def paint(self):
        return render_container(self.toasts.container, self.toasts.width(), self.toasts.height())

    async def test_full_long_body_separate_icon_and_text_padding(self):
        self.toasts.alert("Long session name", "A" * 86 + "\nLast line", "error")
        lines = self.paint()
        self.assertEqual(self.toasts.width(), 48)
        self.assertEqual(len(lines), 6)  # Three wrapped rows, explicit newline, borders.
        self.assertIn("Long se…", lines[0])
        self.assertEqual(lines[1][1:3].strip(), self.view.t.icons.alerts["error"])
        self.assertEqual("".join(line[4:-2].rstrip() for line in lines[1:-1]), "A" * 86 + "Last line")
        for line in lines[1:-1]:
            self.assertEqual((line[0], line[-1]), ("│", "│"))
            self.assertEqual((line[3], line[-2]), (" ", " "))
        self.assertTrue(all(line[1:3] == "  " for line in lines[2:-1]))

    async def test_unicode_title_limit_and_resize_reflow(self):
        self.view.t = Language(icons=UNICODE)
        self.toasts.alert("가나다라마바사아자", "가나다라마바사아자차" * 7, "success")
        wide = self.paint()
        self.assertIn("가나다라마바사…", wide[0])
        self.assertIn(UNICODE.alerts["success"], wide[1])
        self.columns = 32
        narrow = self.paint()
        self.assertEqual(self.toasts.width(), 28)
        self.assertGreater(len(narrow), len(wide))
        self.assertEqual("".join(line.strip("│ ").removeprefix(UNICODE.alerts["success"]).strip()
                                 for line in narrow[1:-1]), "가나다라마바사아자차" * 7)
        self.toasts.alert("12345678", "short", key="replace")
        self.assertIn("12345678", "\n".join(self.paint()))

    async def test_short_terminal_prefers_newest_and_never_exceeds_bounds(self):
        self.toasts.alert("Older", "old " * 50)
        self.toasts.alert("Newest", "new " * 20)
        self.rows = 12
        lines = self.paint()
        self.assertLessEqual(len(lines), self.rows - 3)
        self.assertIn("Newest", lines[0])
        self.assertNotIn("Older", "\n".join(lines))
        self.toasts.alert("Huge", "body " * 200)
        self.assertEqual(self.toasts.height(), self.rows - 3)
        self.assertIn("Huge", self.paint()[0])
        self.columns, self.rows = 10, 4
        self.assertLessEqual(self.toasts.width(), self.columns)
        self.assertLessEqual(self.toasts.height(), self.rows)
        self.assertNotIn("Window too small", "\n".join(self.paint()))

    async def test_reuses_widgets_until_content_or_geometry_changes(self):
        self.toasts.push("Session", "failed", "s")
        first = self.toasts._container()
        items = self.toasts.items
        with patch("hub.widget.feedback.notifications.chop_cells", side_effect=AssertionError("repeated wrap")):
            self.toasts.height()
            self.assertIs(self.toasts._container(), first)
            self.assertIs(self.toasts.items, items)
        self.view.t = Language("en", icons=UNICODE)
        self.assertIsNot(self.toasts._container(), first)
        self.assertIn("Failed", "\n".join(self.paint()))
        self.view.general = GeneralSettings(notification_kinds=[])
        self.assertFalse(self.toasts.active())
        self.assertEqual(self.toasts.height(), 1)

    async def test_replacement_and_expiry_share_one_timer_and_close_cancels_it(self):
        with patch("hub.widget.feedback.notifications.monotonic", return_value=100):
            self.toasts.alert("First", "first", key="same")
            first_timer = self.toasts._expiry_timer
        with patch("hub.widget.feedback.notifications.monotonic", return_value=101):
            self.toasts.alert("Changed", "second", key="same")
            self.assertTrue(first_timer.cancelled())
            self.assertEqual(len(self.toasts.items), 1)
            self.toasts.alert("Other", "third")
        timer = self.toasts._expiry_timer
        with patch("hub.widget.feedback.notifications.monotonic", return_value=110):
            timer._callback()
        self.assertFalse(self.toasts.items)
        self.assertIsNone(self.toasts._expiry_timer)
        self.toasts.alert("Last", "last")
        timer = self.toasts._expiry_timer
        self.toasts.close()
        self.assertTrue(timer.cancelled())
        self.assertFalse(self.toasts.items)
