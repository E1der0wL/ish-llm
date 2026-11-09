"""Producer-owned progress updates and language-aware stage presentation."""
import unittest

from dok.features.completion.progress import CompletionProgress
from dok.features.completion.runtime import Phase, Progress, RegistrationResult
from dok.locales import Locale
from dok.core.theme import Theme, ThemeRole
from dok import DockConfig
from dok.popups.config import PopupRect
from dok.rendering.popups import PopupSurface
from dok.rendering.text import width
from dok.popups.models import Notification, NotificationQueue, PopupContent


class CompletionProgressTests(unittest.TestCase):
    def test_one_key_updates_queued_and_visible_notifications_without_losing_other_notices(self):
        queue = NotificationQueue()
        key = CompletionProgress.KEY
        first = Notification(PopupContent.plain("Progress", "executing"), None, key=key)
        analysis = Notification(PopupContent.plain("Progress", "analyzing"), None, key=key)
        unrelated = Notification(PopupContent.plain("Reminder", "event"), 6)
        queue.add(first)
        queue.add(unrelated)
        queue.add(analysis)
        self.assertEqual(queue.pending, 2)
        queue.sync(True, 0)
        self.assertIs(queue.current, analysis)
        queue.sync(True, 500)
        self.assertIs(queue.current, analysis)
        done = Notification(PopupContent.plain("Progress", "done"), 6, key=key)
        queue.add(done)
        self.assertIs(queue.current, done)
        queue.sync(True, 500)
        queue.sync(True, 507)
        self.assertIs(queue.current, unrelated)
        queue.add(first)
        queue.remove(key)
        self.assertIs(queue.current, unrelated)
        self.assertEqual(queue.pending, 1)

    def test_progress_is_localized_stages_not_estimated_percentages(self):
        for language, phrase in (("en", "Analyzing and translating"), ("ko", "분석 및 번역 중")):
            locale = Locale.resolve(language)
            content = CompletionProgress.content(Progress(("git", "log"), Phase.ANALYZING, True), locale, "git log")
            self.assertEqual(content.rows[0].text, "git log")
            self.assertIn(phrase, content.rows[1].text)
            self.assertIn("(2/3)", content.rows[1].text)
            self.assertNotIn("%", "".join(row.text for row in content.rows))
            rows = content.rows_at(36, Theme())
            self.assertEqual(rows[-1].cells, 36)
            self.assertTrue(any(span.color == ThemeRole.TITLE for span in rows[-1].spans))
            self.assertTrue(any(span.color == ThemeRole.MUTED for span in rows[-1].spans))
            done = CompletionProgress.finished(RegistrationResult(("git", "log"), 12), locale, "git log")
            rows = done.rows_at(36, Theme())
            self.assertEqual(rows[-1].cells, 36)
            self.assertTrue(all(span.color == ThemeRole.SAVE for span in rows[-1].spans))

    def test_rich_bar_follows_popup_body_width_and_notifications_have_no_footer(self):
        config = DockConfig()
        locale = Locale.resolve('ko')
        notice = CompletionProgress.content(Progress(('git','log'), Phase.ANALYZING, True), locale, 'git log')
        surface = PopupSurface(lambda: None, lambda: None, modal=False)
        for columns in (0, 1, 10, 36, 80):
            self.assertEqual(notice.rows_at(columns, config.theme)[-1].cells, columns)
        for columns in (24, 40, 60):
            surface.draw(notice, config.popups.notifications.panel, PopupRect(0, 0, columns, 9),
                         notification=True, theme=config.theme, locale=locale)
            self.assertEqual(sum(width(part[1]) for part in surface._wrapped_rows[-1]), columns - 4)
            text = ''.join(part[1] for part in surface.fragments)
            self.assertNotIn('닫기', text)
            self.assertIn('', text)
            if columns >= 40:
                self.assertEqual(surface.painted_height, 8)
                lines = text.splitlines()
                self.assertEqual(lines[2].strip('│ '), '')
                self.assertEqual(lines[-2].strip('│ '), '')
                self.assertIn('━', lines[-3])
                self.assertTrue(lines[-1].startswith('└'))
        ordinary = PopupContent.plain('Notice', 'Body')
        surface.draw(ordinary, config.popups.notifications.panel, PopupRect(0,0,40,9),
                     notification=True, theme=config.theme, locale=locale)
        self.assertNotIn('닫기', ''.join(part[1] for part in surface.fragments))
        self.assertEqual(surface._actions, ())
        self.assertEqual(surface.painted_height, 6)
        surface.draw(ordinary, config.popups.general, PopupRect(0,0,40,12),
                     theme=config.theme, locale=locale)
        self.assertIn('닫기', ''.join(part[1] for part in surface.fragments))
