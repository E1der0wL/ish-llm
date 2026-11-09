"""Finite schema inputs preserve typed drafts, missing values and explicit commit."""

import asyncio
import unittest
from unittest.mock import Mock

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout import HSplit, Layout

from hub.locales import Language
from hub.widget.color_preview import ColorPreview
from hub.widget.controls import Button
from hub.widget.forms.settings import ChoiceField, ColorField, MISSING, SchemaForm, SettingField
from tests.hub.support import SizedOutput, eventually


class SettingsChoiceTests(unittest.TestCase):
    def test_only_finite_simple_schemas_get_dropdowns_without_materializing_defaults(self):
        specs = {
            'mode': {'type': 'string', 'enum': ['fast', 'complete'], 'default': 'fast'},
            'flag': {'type': ['boolean', 'null']},
            'version': {'const': 1},
            'text': {'type': 'string'},
            'range': {'type': 'integer', 'minimum': 1, 'maximum': 10},
            'array': {'type': 'array', 'items': {'enum': ['a', 'b']}},
            'union': {'anyOf': [{'enum': ['a']}, {'type': 'object'}]},
        }
        form = SchemaForm({'type': 'object', 'properties': specs}, {}, Language('en'))
        for name in ('mode', 'flag', 'version'):
            self.assertIsInstance(form.fields[(name,)], ChoiceField)
        for name in ('text', 'range', 'array', 'union'):
            self.assertIs(type(form.fields[(name,)]), SettingField)
        self.assertEqual(form.values(), {})
        self.assertTrue(all(field._input is None for field in form.fields.values()))

    def test_missing_null_and_json_types_stay_distinct(self):
        field = ChoiceField('choice', {'enum': [True, 1, '1', None]}, MISSING, Language('en'))
        for index, expected in enumerate([True, 1, '1', None], 1):
            field.select(index)
            self.assertIs(type(field.value()), type(expected))
            self.assertEqual(field.value(), expected)
            self.assertEqual(field.selected_index(), index)
        field.select(0)
        self.assertIs(field.value(), MISSING)

    def test_labels_are_presentation_only_and_equal_selection_does_not_dirty(self):
        field = ChoiceField('mode', {'type': 'string', 'enum': ['a', 'b'],
                            ChoiceField.SCHEMA_KEY: {'a': 'Alpha', 'b': 'Beta'}}, 'a', Language('en'))
        field.select(2)
        self.assertEqual(field.value(), 'b')
        self.assertEqual(field.menu_items()[2], (2, 'Beta'))
        field.select(1)
        self.assertEqual(field.text, field.initial)
        field.readonly = True
        field.select(2)
        self.assertEqual(field.value(), 'a')

    def test_fixed_choice_can_hide_unset_without_changing_other_fields(self):
        field = ChoiceField('language', {'type': 'string', ChoiceField.SCHEMA_KEY: {
            '': 'Auto-detect', 'en': 'English'}}, '', Language('en'))
        field.allow_unset = False
        self.assertEqual(field.menu_items(), [(1, 'Auto-detect'), (2, 'English')])
        field.select(2)
        field.select(0)
        field.clear()
        self.assertEqual(field.value(), 'en')
        field.select(1)
        self.assertEqual(field.value(), '')

    def test_field_change_notification_does_not_require_a_text_buffer(self):
        for field in (
            SettingField('text', {'type': 'string'}, 'a', Language('en')),
            ChoiceField('mode', {'type': 'string', 'enum': ['a', 'b']}, 'a', Language('en')),
        ):
            notify = Mock()
            field.on_change += notify
            field.text = 'b'
            field.text = 'b'
            notify.assert_called_once_with(field)

    def test_color_preview_tracks_draft_without_changing_or_rejecting_incomplete_input(self):
        form = SchemaForm({'type': 'object', 'properties': {'color': {
            'type': 'string', ColorPreview.SCHEMA_KEY: True}}}, {'color': '#123456'}, Language('en'))
        field = form.fields[('color',)]
        self.assertIsInstance(field, ColorField)
        preview = ColorPreview(field.value)
        for raw, expected in (('#123456', '#123456'), ('"#abcdef"', '#abcdef')):
            field.text = raw
            self.assertEqual(preview.fragments(), [('bg:' + expected, '  ')])
        for raw in ('default', '#12', 'not a color', '"unfinished'):
            field.text = raw
            self.assertEqual(preview.fragments(), [('', '  ')])
            self.assertEqual(field.text, raw)


class SettingsChoiceKeyboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_menu_navigation_cancel_commit_and_focus_loss_keep_drafts_consistent(self):
        field = ChoiceField('mode', {'type': 'string', 'enum': ['a', 'b']}, 'a', Language('en'))
        other = Button('Other')
        with create_pipe_input() as pipe:
            app = Application(layout=Layout(HSplit([field.container, other]), focused_element=field.input.control),
                              input=pipe, output=SizedOutput(), full_screen=True)
            task = asyncio.create_task(app.run_async())
            try:
                await eventually(lambda: app.is_running)
                pipe.send_text('\r')
                await eventually(lambda: field.editing)
                pipe.send_text('\x1b[B')
                await eventually(lambda: field.input.choices._selected_index == 2)
                self.assertEqual(field.value(), 'a')
                pipe.send_text('\x1b')
                await eventually(lambda: not field.editing)
                self.assertIs(app.layout.current_control, field.input.control)
                self.assertEqual(field.value(), 'a')
                pipe.send_text('\r\x1b[B\r')
                await eventually(lambda: field.value() == 'b' and not field.editing)
                pipe.send_text('\r')
                await eventually(lambda: field.editing)
                with set_app(app):
                    app.layout.focus(other)
                    self.assertFalse(field.menu_open)
                    self.assertFalse(field.editing)
                self.assertEqual(field.value(), 'b')
            finally:
                app.exit()
                await task
