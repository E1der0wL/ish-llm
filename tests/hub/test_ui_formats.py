"""Shared UI presentation never changes execution state or persisted settings."""

from copy import deepcopy
from dataclasses import replace
from unittest import TestCase

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import set_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType, MouseButton
from prompt_toolkit.utils import get_cwidth

from hub.asset.icon import for_style
from hub.locales import Language
from hub.locales.keys import MessageKey
from hub.model import ChatMessage, RetrySummary
from hub.widget.captions import Caption
from hub.widget.forms.field_layout import FieldLayout
from hub.widget.forms.settings import SchemaForm, MISSING
from hub.widget.list_presentation import ListPresentation
from hub.widget.notices import NoticeText
from hub.widget.sidebar import SidebarItem, SidebarList
from hub.widget.summary import clipped_line
from hub.widget.transcript.header import OutputHeader
from hub.widget.feedback.retry_status import retry_text


class SharedFormatTests(TestCase):
    def test_headers_keep_role_semantics_and_do_not_parse_body_or_author(self):
        language = Language('ko')
        message = ChatMessage('assistant', '**literal body**', author='[name] • Agent',
                              status='streaming', phase='draft_reviewing', elapsed_seconds=12)
        expected = '[name] • Agent • ' + language.icons.reasoning + ' 답변 검증 중 (초안) • 소요 12초'
        self.assertEqual(OutputHeader.message(message, language, 'account').render(), expected)
        self.assertEqual(OutputHeader.message(replace(message, role='reasoning'), language, 'account').render(),
                         '[name] • Agent • 생각 과정')
        self.assertEqual(OutputHeader.message(replace(message, role='system', time='15:10'), language, 'account').render(),
                         '시스템 • 15:10')
        self.assertEqual(Caption('target', details=('', 'details', '')).render(), 'target • details')
        self.assertEqual(message.text, '**literal body**')

    def test_known_and_new_statuses_have_one_declared_glyph_in_each_palette(self):
        for locale in ('ko', 'en'):
            for palette in ('nerd', 'unicode'):
                language = Language(locale, icons=for_style(palette))
                for status in ('preparing', 'thinking', 'running', 'draft_streaming', 'draft_reviewing',
                               'summarizing', 'waiting_approval', 'waiting_input', 'interrupting',
                               'completed', 'failed', 'paused', 'pending', 'applied', 'unapplied', 'custom_phase'):
                    with self.subTest(locale=locale, palette=palette, status=status):
                        text = language.status(status)
                        icon = language.icons.for_status(status)
                        self.assertTrue(text.startswith(icon + ' '))
                        self.assertEqual(text.count(icon), 1)
                self.assertEqual(language.status(''), '')
                self.assertIn('custom_phase', language.status('custom_phase'))

    def test_custom_pack_icons_are_placeholders_and_do_not_appear_twice(self):
        language = Language('custom', {'custom': {'status_running': '{icon_running} Work in progress'}})
        self.assertEqual(language.status('running'), language.icons.for_status('running') + ' Work in progress')
        self.assertEqual(language.without_icons('status_running'), 'Work in progress')

    def test_recovery_and_retry_reuse_status_glyphs_and_preserve_attempt_information(self):
        language = Language('ko')
        for phase in ('analyzing', 'compacting', 'retrying', 'rejected'):
            retry = RetrySummary(2, 4, kind='recovery', phase=phase)
            text = retry_text(retry, language)
            self.assertTrue(text.startswith(language.icons.for_status(phase) + ' '))
            self.assertIn('2/4', text)
            if phase == 'compacting':
                self.assertEqual(text.count(language.icons.compaction), 1)
        self.assertIn('2/4', retry_text(RetrySummary(2, 4), language))

    def test_list_metadata_omits_empty_segments_and_preserves_ids(self):
        item = ListPresentation('  이름\n둘째 줄 ', ('2026-11-03 15:10', ''), '설명\n다음 줄').sidebar('stable-id')
        self.assertEqual((item.key, item.title), ('stable-id', '이름 둘째 줄'))
        self.assertEqual(item.detail, '2026-11-03 15:10 • 설명 다음 줄')
        self.assertEqual(ListPresentation.timestamp(None), '—')
        self.assertEqual(ListPresentation.timestamp('invalid-date'), '—')
        self.assertTrue(ListPresentation('가나다라마바').line(7).endswith('…'))

    def test_clipping_keeps_private_font_glyphs_and_removes_terminal_controls(self):
        text = "    󰒓 한글"
        self.assertEqual(clipped_line(text + "\x1b\x07\n", 30), text)
        for width in range(1, 15):
            self.assertLessEqual(get_cwidth(clipped_line(text, width)), width)
        self.assertTrue(clipped_line(text, 4).startswith("  "))

    def test_sidebar_width_and_empty_notice_keep_click_row_identity(self):
        selected = ['a']
        items = [SidebarItem('a', '아주 긴 제목 ' * 15, '긴 설명 ' * 30), SidebarItem('b', '둘째')]
        changes = []
        control = SidebarList(lambda: items, lambda: selected[0], changes.append)
        app = Application(layout=Layout(Window(control)))
        with set_app(app):
            content = control.create_content(20, 8)
            self.assertEqual(content.line_count, 4)
            for index in range(content.line_count):
                self.assertLessEqual(get_cwidth(''.join(part[1] for part in content.get_line(index))), 20)
            control.mouse_handler(MouseEvent(Point(3, 3), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
            self.assertEqual(changes, ['b'])
            changes.clear()
            items[:] = [NoticeText('항목 없음', hint='가져오기를 사용하세요.').sidebar()]
            control.mouse_handler(MouseEvent(Point(3, 0), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
            control.move(1)
            self.assertEqual(changes, [])
            self.assertNotIn('▶', ''.join(part[1] for part in control.fragments()))

    def test_common_field_layout_keeps_missing_null_and_schema_constraints(self):
        schema = {'type': 'object', 'properties': {
            'count': {'type': 'integer', 'minimum': 1, 'maximum': 20, 'description': '개수'},
            'optional': {'type': ['string', 'null'], 'description': '선택 값'},
        }}
        original = deepcopy(schema)
        form = SchemaForm(schema, {'count': 3, 'optional': None, 'extension': 'preserved'}, Language('ko'))
        self.assertIsInstance(form.fields[('count',)].container, FieldLayout)
        form.fields[('count',)].text = '5'
        self.assertEqual(form.values(), {'count': 5, 'optional': None, 'extension': 'preserved'})
        form.fields[('optional',)].text = ''
        self.assertIs(form.fields[('optional',)].value(), MISSING)
        self.assertEqual(schema, original)
        form.fields[('count',)].text = '{invalid'
        with self.assertRaisesRegex(ValueError, 'count:'):
            form.values()
