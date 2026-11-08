"""Repeated navigation preserves work; explicit actions retain their meaning."""

import asyncio
import unittest
from unittest.mock import Mock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout import Window

from examples.hub.preview import create_preview
from hub.widget.tab_page import TabPage
from tests.hub.support import SizedOutput, eventually


class ReselectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_home_tab_and_repeat_selection_keep_transcript_and_composer(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running and view.visible)
                with set_app(app):
                    view.composer.text = 'unfinished draft'
                    view.composer.buffer.cursor_position = 4
                    app.layout.focus(view.projects.control)
                    view.on_select, view.on_save_view = Mock(), Mock()
                with patch.object(view.output, 'set_messages', wraps=view.output.set_messages) as messages, \
                     patch.object(view.output, 'deactivate', wraps=view.output.deactivate) as deactivate:
                    pipe.send_text('\t')
                    await eventually(lambda: app.layout.current_control is view._session_control)
                    with set_app(app):
                        view._session_control.activate()
                    messages.assert_not_called()
                    deactivate.assert_not_called()
                    view.on_select.assert_not_called()
                    view.on_save_view.assert_not_called()
                    self.assertEqual(view.composer.text, 'unfinished draft')
                    self.assertEqual(view.composer.buffer.cursor_position, 4)
                    with set_app(app):
                        view._session_control.move(1)
                    self.assertEqual(view.selected, 1)
                    messages.assert_called_once()
                    view.on_select.assert_called_once_with(view.sessions[1].id)
            finally:
                app.exit()
                await task

    async def test_settings_reselection_keeps_form_scroll_and_draft(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running and view.visible)
                with set_app(app):
                    view.toggle_settings()
                    screen = view.settings
                    screen.choose('profile')
                    page = screen.page
                    page.form.fields[('email',)].input.text = 'draft@example.test'
                    screen.main.vertical_scroll = 7
                    with patch.object(screen, 'choose', wraps=screen.choose) as choose:
                        screen.select_left('profile')
                        choose.assert_not_called()
                        self.assertIs(screen.page, page)
                        self.assertEqual(screen.main.vertical_scroll, 7)
                        screen.select_left('appearance')
                        choose.assert_called_once_with('appearance')
                    self.assertEqual(page.form.fields[('email',)].input.text, 'draft@example.test')
                    # Direct choose is the explicit page refresh/reload path.
                    with patch.object(screen, 'show_page', wraps=screen.show_page) as show:
                        screen.choose('appearance')
                        show.assert_called_once()
            finally:
                app.exit()
                await task

    async def test_tab_boundary_does_not_repeat_change_callback(self):
        changed = Mock()
        tabs = TabPage([('a', 'A', Window()), ('b', 'B', Window())], on_change=changed)
        with patch.object(tabs, 'focus_tabs') as focus:
            tabs.move(-1)
            tabs.select('a')
            changed.assert_not_called()
            self.assertEqual(focus.call_count, 2)
            tabs.select('b')
            changed.assert_called_once_with('b')
            tabs.move(1)
            changed.assert_called_once_with('b')
