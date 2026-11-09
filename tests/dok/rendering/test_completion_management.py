"""Draft editing, confirmation, mouse redraws and narrow manager layouts."""
from types import SimpleNamespace
from unittest.mock import Mock
import shlex
import unittest

from prompt_toolkit.data_structures import Point
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.styles import Style

from dok import Theme, Locale
from dok.features.completion.management import CompletionManager, ManagementAction
from dok.features.completion.management_editor import ItemField
from dok.features.completion.models import Candidate, Catalog, Registration, Argument
from dok.features.completion.runtime import Phase
from dok.rendering.text import width


class ManagerViewTests(unittest.TestCase):
    def setUp(self):
        self.runtime = SimpleNamespace(catalog=Catalog(registrations=(Registration(('tool',),
            items=(Candidate('--all', help='All'), Candidate('--mode', argument=Argument.REQUIRED))),)),
            catalog_ready=True, busy=False, phase=Phase.IDLE, error='', save_changes=Mock(return_value=True))
        self.invalidate, self.dismiss = Mock(), Mock()
        self.page = CompletionManager(self.runtime, SimpleNamespace(prefix=lambda text: tuple(shlex.split(text)),
            label=shlex.join), self.invalidate, self.dismiss)
        self.page.content()

    def test_dirty_marks_follow_editor_and_catalog_revert_without_status_row(self):
        page = self.page
        def text():
            return '\n'.join(''.join(item[1] for item in row) for row in page.draw_body(100, 23, Theme()))
        self.assertNotIn('* Save', text())
        page.key('tab', '')
        page.key('enter', '')
        self.assertFalse(page.editor_dirty)
        page.editor.buffer(ItemField.HELP).text = 'Edited'
        self.assertIn('* Apply', text())
        page.editor.buffer(ItemField.HELP).text = 'All'
        self.assertNotIn('* Apply', text())
        page.editor.buffer(ItemField.HELP).text = 'Edited'
        page.key('f2', '')
        self.assertIn('* Save', text())
        self.assertNotIn('Unsaved changes', text())
        page.key('f2', '')
        callback = self.runtime.save_changes.call_args.args[2]
        callback('Concurrent edit')
        self.assertIn('* Save', text())
        self.assertIn('Concurrent edit', text())
        page.key('f2', '')
        self.runtime.catalog = page.draft
        self.runtime.save_changes.call_args.args[2]('')
        self.assertNotIn('* Save', text())
        self.assertNotIn('Changes saved', text())

    def test_escape_walks_back_without_closing_and_unchanged_item_keeps_evidence(self):
        page = self.page
        page.key('tab', '')
        page.key('enter', '')
        original = page.editor.original
        self.assertIs(page.editor.build(), original)
        page.field = ItemField.KIND
        page.key('enter', '')
        self.assertIsNotNone(page.dropdown)
        page.key('escape', '')
        self.assertIsNone(page.dropdown)
        self.assertIsNotNone(page.editor)
        page.key('escape', '')
        self.assertIsNone(page.editor)
        self.assertEqual(page.area, 'items')
        page.key('escape', '')
        self.assertEqual(page.area, 'prefixes')
        page.key('escape', '')
        self.dismiss.assert_not_called()

    def test_edit_cancel_apply_and_save_are_distinct_and_prompt_independent(self):
        page = self.page
        page.key('tab', '')
        page.key('enter', '')
        page.editor.buffer(ItemField.HELP).text = 'Edited'
        page.key('escape', '')
        self.assertEqual(page.registration().item('--all').help, 'All')
        page.key('enter', '')
        page.editor.buffer(ItemField.HELP).text = 'Edited'
        page.key('f2', '')
        self.assertTrue(page.dirty)
        self.assertEqual(page.registration().item('--all').help, 'Edited')
        self.runtime.save_changes.assert_not_called()
        page.key('f2', '')
        self.runtime.save_changes.assert_called_once_with(page.expected, page.draft,
                                                         self.runtime.save_changes.call_args.args[2])
        self.assertTrue(page.saving)
        callback = self.runtime.save_changes.call_args.args[2]
        callback('Concurrent edit')
        self.assertFalse(page.saving)
        self.assertTrue(page.dirty)
        self.assertEqual(page.error, 'Concurrent edit')

    def test_close_and_delete_require_confirmation_without_silent_writes(self):
        page = self.page
        page.key('any', 'a')
        page.prefix_editor.text = 'new sub'
        page.key('enter', '')
        self.assertTrue(page.dirty)
        self.assertFalse(page.request_close())
        page.key('enter', '')  # Cancel is the initial choice.
        self.assertTrue(page.dirty)
        self.dismiss.assert_not_called()
        page.key('any', 'd')
        page.key('right', '')
        page.key('enter', '')
        self.assertFalse(page.dirty)
        self.assertTrue(page.request_close())
        self.runtime.save_changes.assert_not_called()

    def test_confirmation_has_fixed_footer_and_mouse_cancel_preserves_draft(self):
        page = self.page
        page.key('d', '')
        original = page.draft
        for height in (7, 15, 23):
            rows = page.draw_body(50, height, Theme())
            text = [''.join(item[1] for item in row).strip() for row in rows]
            self.assertIn('tool', text[0])  # No internal heading reservation.
            self.assertEqual(text[-3], '')
            self.assertIn('Apply', text[-2])
            self.assertIn('Cancel', text[-2])
            self.assertEqual(text[-1], '')
        cancel = next(item for item in rows[-2] if item[1].strip() == 'Cancel')
        cancel[2](MouseEvent(Point(0, 0), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
        self.assertIsNone(page.confirm)
        self.assertIs(page.draft, original)
        self.runtime.save_changes.assert_not_called()

    def test_mouse_edit_and_cancel_invalidate_without_mouse_movement_redraws(self):
        page = self.page
        page.key('tab', '')
        def click(label, occurrence=0):
            rows = page.draw_body(100, 21, Theme())
            fragment = [item for row in rows for item in row if len(item) == 3 and item[1].strip() == label][occurrence]
            self.invalidate.reset_mock()
            fragment[2](MouseEvent(Point(0, 0), MouseEventType.MOUSE_MOVE, MouseButton.NONE, frozenset()))
            self.invalidate.assert_not_called()
            fragment[2](MouseEvent(Point(0, 0), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
            self.invalidate.assert_called()
        click(ManagementAction.EDIT, 1)  # The right category's own action.
        self.assertIsNotNone(page.editor)
        click('Cancel')
        self.assertIsNone(page.editor)

    def test_list_lock_and_dropdown_keys_keep_local_state(self):
        page = self.page
        page.key('tab', '')
        page.key('any', 'l')
        self.assertTrue(page.selected_item().locked)
        page.key('enter', '')
        page.field = ItemField.ARGUMENT
        page.key('enter', '')
        page.key('down', '')
        page.key('enter', '')
        self.assertEqual(page.editor.argument, Argument.REQUIRED)
        self.assertIn(ItemField.VALUES, page.editor.fields())
        self.assertFalse(self.runtime.catalog.get(('tool',)).item('--all').locked)

    def test_category_actions_share_heading_row_and_each_editor_field_has_one_blank_row(self):
        page = self.page
        rows = page.draw_body(100, 23, Theme())
        for icon in (ManagementAction.ADD, ManagementAction.EDIT, ManagementAction.LOCK, ManagementAction.DELETE):
            self.assertEqual(sum(item[1].strip() == icon for item in rows[0]), 2)
            self.assertFalse(any(item[1].strip() == icon for row in rows[1:] for item in row))
        page.key('tab', '')
        page.key('down', '')  # --mode has argument/value fields.
        page.key('enter', '')
        rows = page.draw_body(100, 23, Theme())
        for index, field in enumerate(page.editor.fields()):
            label, control, padding = rows[index * 3:index * 3 + 3]
            self.assertIn(field.label(Locale()), ''.join(item[1] for item in label))
            self.assertTrue(''.join(item[1] for item in control))
            self.assertEqual(''.join(item[1] for item in padding).strip(), '')

    def test_secondary_actions_use_the_heading_background_and_align_to_each_right_edge(self):
        heading = self.page.draw_body(100, 23, Theme())[0]
        position, divider, delete_ends, backgrounds = 0, None, [], []
        style = Style([])
        title_background = style.get_attrs_for_style_str(heading[0][0]).bgcolor
        for fragment in heading:
            if fragment[1] == '│':
                divider = position
            position += width(fragment[1])
            if len(fragment) == 3:
                backgrounds.append(style.get_attrs_for_style_str(fragment[0]).bgcolor)
                if fragment[1].strip() == ManagementAction.DELETE:
                    delete_ends.append(position)
        self.assertEqual(delete_ends, [divider, 100])
        self.assertEqual(backgrounds, [title_background] * 8)

    def test_editor_footer_stays_fixed_when_fields_menus_errors_and_height_change(self):
        page = self.page
        page.key('tab', '')
        page.key('enter', '')
        for height in (12, 23, 30):
            for argument in (Argument.NONE, Argument.REQUIRED):
                page.editor.argument = argument
                page.field = ItemField.NAME
                for dropdown, error in ((None, ''), (ItemField.ARGUMENT, ''), (None, 'Invalid entry')):
                    with self.subTest(height=height, argument=argument, dropdown=dropdown, error=error):
                        page.dropdown, page.error = dropdown, error
                        rows = page.draw_body(100, height, Theme())
                        self.assertEqual(len(rows), height)
                        apply_label = '* Apply' if argument == Argument.REQUIRED else 'Apply'
                        actions = [(index, part[1].strip()) for index, row in enumerate(rows) for part in row
                                   if len(part) == 3 and part[1].strip() in ('Apply', '* Apply', 'Cancel')]
                        self.assertEqual(actions, [(height - 2, apply_label), (height - 2, 'Cancel')])
                        self.assertEqual(''.join(part[1] for part in rows[-3]).strip(), '')
                        self.assertEqual(''.join(part[1] for part in rows[-1]).strip(), '')

    def test_readable_rows_fit_small_windows_and_failed_lookup_keeps_saved_catalog(self):
        self.runtime.error = 'A later lookup failed'
        page = CompletionManager(self.runtime, self.page.host, self.invalidate, self.dismiss,
                                 locale=lambda: Locale.resolve('ko'))
        self.assertEqual(page.content().title, '자동완성 관리')
        self.assertIsNotNone(page.draft)
        for columns, rows in ((1, 1), (20, 6), (100, 21)):
            with self.subTest(columns=columns):
                body = page.draw_body(columns, rows, Theme())
                self.assertLessEqual(len(body), rows)
                self.assertTrue(all(sum(width(part[1]) for part in row) <= columns for row in body))
