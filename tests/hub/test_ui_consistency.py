"""Shared chrome uses allocated cells, schema ownership and consistent navigation."""

import unittest
import asyncio
from unittest.mock import Mock

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import DummyInput, create_pipe_input
from prompt_toolkit.layout import Layout, HSplit, Window
from prompt_toolkit.widgets import Label
from prompt_toolkit.data_structures import Point
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType, MouseButton
from prompt_toolkit.utils import get_cwidth

from hub.locales import Language
from hub.widget.controls import Button, TextArea
from hub.widget.dialog import Dialog, PopupSize, PopupGeometry
from hub.widget.dropdown import DropdownBox
from hub.widget.sidebar import SidebarList, SidebarItem
from hub.widget.shortcuts import ShortcutBar
from hub.widget.forms.settings import SchemaForm
from tests.hub.support import SizedOutput, render_container
from tests.hub.support import eventually


class SharedChromeTests(unittest.TestCase):
    def app(self, body, columns=132, rows=40):
        return Application(layout=Layout(HSplit([body, TextArea(height=1)])),
                           input=DummyInput(), output=SizedOutput(columns, rows))

    def test_buttons_center_once_using_allocated_cells(self):
        for label in ('OK', '그룹 추가', ''):
            button = Button(lambda: label, width=20)
            with set_app(self.app(button)):
                for width in (6, 12, 15, 20):
                    if get_cwidth(label) > width:
                        continue
                    row = render_container(button, width, 1)[0]
                    self.assertEqual(row.strip(), label)
                    self.assertLessEqual(abs(len(row)-len(row.lstrip()) -
                                             (len(row)-len(row.rstrip()))), 1)

    def test_three_popup_sizes_and_interactive_body_promotion(self):
        with set_app(self.app(Window())):
            info = Dialog(body=Label('Information'), size=PopupSize.SMALL,
                          buttons=[Button('Close')])
            editable = Dialog(body=TextArea(), size=PopupSize.SMALL)
            self.assertEqual(info.size, PopupSize.SMALL)
            self.assertIs(info.body, info.popup_body)
            info.body = TextArea()
            self.assertEqual(info.size, PopupSize.MEDIUM)
            self.assertEqual(editable.size, PopupSize.MEDIUM)
            for size, expected in ((PopupSize.SMALL,(60,9)),
                                   (PopupSize.MEDIUM,(100,36)),
                                   (PopupSize.LARGE,(128,36))):
                self.assertEqual(tuple(d.preferred for d in PopupGeometry.dimensions(size)), expected)
                popup = Dialog(body=Label('Body'), size=size, buttons=[Button('Close')])
                rows = render_container(popup, *expected)
                self.assertFalse(any('Window too small' in row for row in rows))
                self.assertTrue(any('Body' in row for row in rows))
        with set_app(self.app(Window(), 65, 18)):
            self.assertEqual(tuple(d.preferred for d in PopupGeometry.dimensions(PopupSize.LARGE)), (61,14))

    def test_dropdown_uses_button_allocation_for_menu_and_clipping(self):
        dropdown = DropdownBox([('a','Alpha'),('b','Very long option label')], width=20)
        with set_app(self.app(dropdown)):
            render_container(dropdown, 13, 1)
            self.assertEqual(dropdown.rendered_width(), 13)
            self.assertEqual(dropdown.floats[0].get_width(), 13)
            dropdown.choices.values = dropdown.values()
            text = ''.join(fragment[1] for fragment in dropdown.choices._get_text_fragments())
            self.assertTrue(all(get_cwidth(line) == 12 for line in text.splitlines()))

    def test_sidebar_repeat_click_activates_empty_click_only_focuses(self):
        selected = ['a']
        activated = Mock()
        sidebar = SidebarList(lambda: [SidebarItem('a','A'),SidebarItem('b','B')],
                              lambda: selected[0], lambda key: selected.__setitem__(0,key),
                              on_activate=activated)
        window = Window(sidebar)
        app = self.app(window)
        with set_app(app):
            render_container(window, 20, 8)
            def click(y):
                sidebar.mouse_handler(MouseEvent(Point(2,y), MouseEventType.MOUSE_UP,
                                                  MouseButton.LEFT, frozenset()))
            app.layout.focus(app.layout.container.children[-1])
            click(0)
            activated.assert_not_called()  # First click from another region only focuses.
            click(1)
            self.assertEqual(selected[0], 'b')
            activated.assert_not_called()
            click(1)
            activated.assert_called_once()
            click(7)
            self.assertEqual(selected[0], 'b')
            activated.assert_called_once()

    def test_empty_closed_objects_hidden_open_objects_preserved(self):
        schema = {'type':'object','properties': {
            'empty':{'type':'object','properties':{},'additionalProperties':False},
            'custom':{'type':'object','additionalProperties':True},
            'model':{'type':'string'}}}
        original = {'empty':{}, 'custom':{'x':1}}
        form = SchemaForm(schema, original, Language('en'))
        self.assertEqual(set(form.fields), {('custom',),('model',)})
        self.assertEqual(form.values(), original)

    def test_segmented_guide_groups_modifiers_and_respects_cells(self):
        bar = ShortcutBar(lambda: [('ESC','Panel'),('Ctrl+Q','Minimize'),
                                   ('Ctrl+W','Restart'),('Alt + 이동키','Scroll')], lambda: 132)
        text = ''.join(s for _,s in bar.fragments())
        self.assertEqual(text.count('Ctrl +'), 1)
        self.assertIn('<Q>', text)
        self.assertIn('', text)
        self.assertNotIn('ESC', text)
        self.assertEqual(get_cwidth(text), 132)
        self.assertTrue(text.endswith('Scroll '))
        self.assertLess(text.index('Ctrl +'), text.index('Alt +'))
        from hub.config.theme_style import ThemeSelector
        self.assertIn((ThemeSelector.SHORTCUT_LEADING.style, ''), bar.fragments())
        self.assertIn((ThemeSelector.SHORTCUT_KEY.style, 'Q'), bar.fragments())
        for width in (1, 15, 35, 70):
            bar.width_available = lambda: width
            self.assertLessEqual(get_cwidth(''.join(s for _,s in bar.fragments())), width)

    def test_approval_explanations_preserve_backend_schema_constraints(self):
        from copy import deepcopy
        from llm.core.policies import ApprovalPolicy
        from hub.backend.project.settings_descriptions import describe_options
        for language in ('ko', 'en'):
            schema = ApprovalPolicy.schema()
            original = deepcopy(schema)
            describe_options(schema, Language(language), ('config','policies','approval'))
            self.assertIn('false', schema['properties']['enabled']['description'])
            self.assertIn('tool.execute', schema['properties']['rules']['description'])
            for name, field in original['properties'].items():
                for key, value in field.items():
                    if key != 'description':
                        self.assertEqual(schema['properties'][name][key], value)


class ManagerChromeTests(unittest.IsolatedAsyncioTestCase):
    async def test_component_manager_medium_actions_and_bottom_status(self):
        from hub.ui.view import HubView
        from hub.widget.forms.definition_manager import DefinitionManager, DefinitionEditor
        view = HubView()
        manager = DefinitionManager(view, Mock(), name='agents', edit_text=None)
        app = Application(layout=Layout(manager), input=DummyInput(), output=SizedOutput())
        with set_app(app):
            view.dialogs.current = manager
            self.assertEqual(manager.size, PopupSize.MEDIUM)
            self.assertNotIn(view.t('settings_reload'), [b.text for b in manager.buttons])
            self.assertEqual(manager.create_button.text, '생성')
            manager.status = 'saved-message'
            rows = render_container(manager, 100, 36)
            text = '\n'.join(rows)
            self.assertIn('Agent 파일 가져오기', text)
            self.assertIn('saved-message', rows[-2])
            self.assertFalse(any('Window too small' in row for row in rows))
            self.assertEqual(sum('┌' in row for row in rows), 1)
            self.assertNotIn(view.t('definition_manager_hint'), text)
            self.assertLess(text.index(manager.create_button.text), text.index('saved-message'))
            self.assertEqual(text.count('saved-message'), 1)
            for name, size in (('workflows', PopupSize.LARGE), ('prompts', PopupSize.MEDIUM),
                               ('memory', PopupSize.MEDIUM)):
                other = DefinitionManager(view, Mock(), name=name, edit_text=None)
                self.assertEqual(other.size, PopupSize.MEDIUM)
                editor = DefinitionEditor(other)
                self.assertEqual(editor.size, size)
                rows = render_container(editor, *[d.preferred for d in PopupGeometry.dimensions(size)])
                self.assertFalse(any('Window too small' in row for row in rows))
        await app.cancel_and_wait_for_background_tasks()
        view.close()


class SettingsWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_debugging_and_general_share_draft_without_mixing_visible_fields(self):
        from examples.hub.preview import create_preview
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                with set_app(app):
                    view.toggle_settings()
                    screen = view.settings
                    screen.choose('general')
                    page = screen.page
                    page.form.fields[('editor',)].input.text = 'vim'
                    screen.choose('debugging')
                    self.assertIs(screen.page, page)
                    self.assertNotIn(page.form.fields[('editor',)], page.fields())
                    field = page.form.fields[('recovery_enabled',)]
                    self.assertIn(field, page.fields())
                    field.input.text = 'false'
                    screen.choose('general')
                    self.assertNotIn(field, page.fields())
                    self.assertEqual(page.form.values()['editor'], 'vim')
                    self.assertFalse(page.form.values()['recovery_enabled'])
                    self.assertEqual(page.buttons, [page.save, page.cancel])
            finally:
                app.exit()
                await task
                view.close()
