"""Alt navigation operates the visible viewport and preserves input and selection."""

import asyncio
import json
from dataclasses import replace
from unittest import IsolatedAsyncioTestCase

from prompt_toolkit.application.current import set_app
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.menus import CompletionsMenuControl

from examples.hub.preview import create_preview
from hub.model import ChatMessage
from hub.widget.controls import CheckboxList, TextArea
from hub.widget.dropdown import DropdownBox
from hub.widget.output.reader import ReadOnlyDialog
from hub.widget.dialog import PopupInput, DialogContext
from prompt_toolkit.layout import Window
from tests.hub.support import SizedOutput, eventually


class ScrollNavigationTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pipe = self.enterContext(create_pipe_input())
        self.app, self.view, _ = create_preview(input=self.pipe, output=SizedOutput(100, 32))
        self.enterContext(set_app(self.app))
        self.task = asyncio.create_task(self.app.run_async(pre_run=lambda: self.view.show(self.app)))
        await eventually(lambda: self.app.is_running and self.view.composer.window.render_info is not None)

    async def asyncTearDown(self):
        self.app.exit()
        await self.task

    async def alt(self, key, reached):
        self.pipe.send_text('\x1b\x1b[' + key)
        await eventually(reached)

    async def test_modal_reader_from_its_close_button_and_footer_priority(self):
        view, app = self.view, self.app
        reader = ReadOnlyDialog('Log', '\n'.join('row ' * 70 for _ in range(160)),
                                view._rows, view._columns, view.close_dialog, view.t)
        view.dialogs.show(reader, reader.close_button)
        await eventually(lambda: reader.output.window.render_info is not None)
        home = view.output.transcript.control.top_line
        self.assertEqual([key for key, _ in view.bindings.hints()][:2], ['Ctrl+Q', 'Ctrl+W'])
        for width in (52, 60, 100):
            bar = view.shortcut_bar()
            bar.width_available = lambda: width
            caption = ''.join(text for _, text in bar.fragments())
            self.assertLess(caption.index('<Q>'), caption.index('<W>'))
        await self.alt('H', lambda: reader.output.top_line == 0)
        await self.alt('B', lambda: reader.output.top_line == 1)
        await self.alt('C', lambda: reader.output.left_column == 1)
        await self.alt('D', lambda: reader.output.left_column == 0)
        await self.alt('6~', lambda: reader.output.top_line > 1)
        await self.alt('5~', lambda: reader.output.top_line == 1)
        await self.alt('A', lambda: reader.output.top_line == 0)
        await self.alt('F', lambda: reader.output.top_line > 100)
        self.assertIs(app.layout.current_control, reader.close_button.control)
        self.assertEqual(view.output.transcript.control.top_line, home)

    async def test_list_and_popup_preview_keep_selection_and_focus(self):
        choices = CheckboxList([(i, str(i)) for i in range(90)])
        choices.window.height = 3
        detail = TextArea(text='한글' * 1000, height=3, read_only=True,
                          focusable=False, wrap_lines=True)
        self.view.open_dialog('Records', HSplit([choices, detail]), focus=choices)
        await eventually(lambda: choices.window.render_info is not None)
        await self.alt('F', lambda: choices.window.vertical_scroll > 80)
        self.assertEqual((choices.highlighted, choices.current_values), (0, []))
        self.app.layout.focus(self.view._dialog._buttons[0])
        await self.alt('F', lambda: detail.window.vertical_scroll_2 > 0)
        await self.alt('H', lambda: detail.window.vertical_scroll_2 == 0)
        self.assertEqual(detail.buffer.cursor_position, 0)
        self.assertIs(self.app.layout.current_control, self.view._dialog._buttons[0].control)

    async def test_dropdown_and_editable_text_have_separate_viewports(self):
        field = TextArea(text='\n'.join('abcdefghij' * 50 for _ in range(20)),
                         height=3, wrap_lines=False)
        menu = DropdownBox([(i, str(i)) for i in range(80)], value=0)
        self.view.open_dialog('Edit', HSplit([field, menu]), focus=field)
        field.buffer.cursor_position = 0
        await eventually(lambda: field.window.render_info is not None)
        doc = field.buffer.document
        await self.alt('F', lambda: field.window.vertical_scroll == 17)
        await self.alt('C', lambda: field.window.horizontal_scroll == 1)
        self.assertEqual(field.buffer.document, doc)
        self.app.layout.focus(menu.button)
        menu.open()
        await eventually(lambda: menu.choices.window.render_info is not None)
        await self.alt('F', lambda: menu.choices.window.vertical_scroll > 60)
        self.assertEqual((menu.value, menu.choices._selected_index), (0, 0))
        self.assertEqual(field.window.vertical_scroll, 17)

    async def test_long_home_sidebar_scroll_does_not_switch_sessions(self):
        view = self.view
        view.sessions = [replace(view.sessions[0], title=str(i), id=str(i)) for i in range(80)]
        view._focus_sessions()
        self.app.invalidate()
        window = self.app.layout.current_window
        await eventually(lambda: window.render_info and window.render_info.content_height >= 80)
        await self.alt('F', lambda: window.vertical_scroll > 60)
        self.assertEqual(view.selected, 0)
        self.assertIs(self.app.layout.current_control, view._session_control)

    async def test_completion_menu_scroll_keeps_draft_and_candidate(self):
        buffer = self.view.composer.buffer
        buffer.completer = WordCompleter([f'option{i}' for i in range(100)])
        self.app.layout.focus(self.view.composer)
        buffer.insert_text('option')
        buffer.start_completion(select_first=False)
        def menu():
            return next((w for w in self.app.layout.visible_windows
                         if isinstance(w.content, CompletionsMenuControl)), None)
        await eventually(menu)
        window = menu()
        selected = buffer.complete_state.complete_index
        await self.alt('F', lambda: window.vertical_scroll > 60)
        self.assertEqual(buffer.complete_state.complete_index, selected)
        self.assertEqual(buffer.text, 'option')

    async def test_table_scroll_from_actions_preserves_cell_and_aligns_heading(self):
        payload = {'columns': [str(i) for i in range(8)],
                   'rows': [[f'{i:03}' + 'x' * 37 for _ in range(8)] for i in range(120)]}
        source = '<hub-table>' + json.dumps(payload) + '</hub-table>'
        self.view.output.transcript.set_messages((ChatMessage('assistant', source, id='table'),))
        self.app.invalidate()
        await eventually(lambda: bool(self.view.output.transcript.control.objects))
        self.view.output.tags.open()
        self.view.output.tags.expand()
        await eventually(lambda: self.view._dialog is not None)
        popup = self.view._dialog
        await eventually(lambda: popup.table.window.render_info is not None
                         and popup.table.window.render_info.content_height == len(payload['rows']))
        self.app.layout.focus(popup.buttons[-1])
        selection = popup.table.row, popup.table.column
        await self.alt('F', lambda: popup.table.window.vertical_scroll > 80)
        await self.alt('C', lambda: popup.table.window.horizontal_scroll == 1)
        await eventually(lambda: popup.table.header.horizontal_scroll == 1)
        self.assertEqual((popup.table.row, popup.table.column), selection)
        self.assertIs(self.app.layout.current_control, popup.buttons[-1].control)

    async def test_existing_custom_popup_needs_no_new_scroll_attribute(self):
        body = TextArea(text='\n'.join(str(i) for i in range(80)), height=4,
                        read_only=True, focusable=False)
        class Extension:
            shortcut_context = DialogContext.READER
            def __init__(self):
                self.receiver = PopupInput().receiver
                self.container = HSplit([body, Window(self.receiver, height=0)], width=60, height=4)
            def __pt_container__(self):
                return self.container
        popup = Extension()
        self.view.dialogs.show(popup, popup.receiver)
        await eventually(lambda: body.window.render_info is not None)
        await self.alt('F', lambda: body.window.vertical_scroll > 60)
        self.assertIs(self.app.layout.current_control, popup.receiver)
