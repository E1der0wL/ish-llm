"""Shared icon toolbar, persistent filters and theme/clipboard boundaries."""

import asyncio
from dataclasses import asdict, replace
import json
from pathlib import Path
import unittest
import tempfile
from unittest.mock import AsyncMock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.config.theme import HubTheme
from hub.widget.focus_region import grayscale
from tests.hub.support import SizedOutput, eventually, auxiliary, open_settings


class AuxiliaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pipe = self.enterContext(create_pipe_input())
        self.app, self.view, _ = create_preview(input=self.pipe, output=SizedOutput())
        self.task = asyncio.create_task(self.app.run_async(pre_run=lambda: self.view.show(self.app)))
        await eventually(lambda: self.app.is_running and self.view.output.transcript.window.render_info is not None)

    async def asyncTearDown(self):
        self.app.exit()
        await self.task
        self.view.close()
        self.view.progress.close()

    async def paint(self):
        event = asyncio.Event()
        callback = lambda _: event.set()
        self.app.after_render += callback
        try:
            self.app.invalidate()
            await asyncio.wait_for(event.wait(), 3)
        finally:
            self.app.after_render -= callback

    async def test_toolbar_navigation_geometry_callbacks_and_removed_shortcuts(self):
        view, app = self.view, self.app
        seen = []
        view.on_history = lambda: seen.append('history')
        view.on_activity = lambda: seen.append('logs')
        view.composer.text = 'draft'
        self.pipe.send_text('\x06\x07\x0c\x14')
        await asyncio.sleep(.08)
        self.assertFalse(view.output.tags.visible)
        self.assertIsNone(view._dialog)
        self.assertEqual(seen, [])
        self.assertEqual(view.composer.text, 'draft')
        await auxiliary(app, self.pipe, view, 'history')
        await eventually(lambda: seen == ['history'])
        with set_app(app):
            self.assertEqual(set(dict(view.bindings.hints())), {'ESC', 'Ctrl+P', 'Ctrl+Q', 'Ctrl+W', 'Enter/Space', '←→'})
        for key, index in (('\x1b[C', 1), ('\x1b[C', 2), ('\x1b[D', 1), ('\x1b[D', 0)):
            self.pipe.send_text(key)
            await eventually(lambda: app.layout.current_control is list(view.auxiliary.buttons.values())[index].control)
            self.pipe.send_text('\x1b[A\x1b[B')
            await asyncio.sleep(.05)
            self.assertIs(app.layout.current_control, list(view.auxiliary.buttons.values())[index].control)
        self.pipe.send_text('\x1b')
        await eventually(lambda: app.layout.current_control is view.composer.control)
        with set_app(app):
            view.set_theme(replace(view.theme, background='#123456'))
        await self.paint()
        screen = app.renderer._last_screen
        positions = screen.visible_windows_to_write_positions
        rects = [positions[b.window] for b in view.auxiliary.buttons.values()]
        self.assertEqual(len({r.ypos for r in rects}), 1)
        self.assertTrue(all(r.height == 1 and r.width == 3 for r in rects))
        self.assertTrue(all(b.xpos == a.xpos + a.width + 1 for a,b in zip(rects, rects[1:])))
        self.assertEqual(rects[-1].xpos + rects[-1].width,
                         positions[view.composer.window].xpos + positions[view.composer.window].width + 1)
        self.assertEqual(app.renderer._attrs_for_style[screen.data_buffer[rects[0].ypos][rects[0].xpos].style].bgcolor, '123456')
        self.assertEqual(app.renderer._attrs_for_style[screen.data_buffer[rects[0].ypos][rects[0].xpos - 1].style].bgcolor, 'default')
        await auxiliary(app, self.pipe, view, 'logs')
        await eventually(lambda: seen == ['history', 'logs'])

    async def test_settings_filters_json_and_exact_return_focus(self):
        view, app = self.view, self.app
        await open_settings(app, self.pipe, view)
        screen = view.settings
        with set_app(app):
            screen.choose('profile')
            app.layout.focus(screen.page.cancel)
        self.pipe.send_text('\x13')
        await eventually(lambda: app.layout.current_control is screen.auxiliary.buttons['filter'].control)
        self.pipe.send_text('\x1b[A\x1b[B')
        await asyncio.sleep(.05)
        self.assertIs(app.layout.current_control, screen.auxiliary.buttons['filter'].control)
        self.pipe.send_text('\x1b')
        await eventually(lambda: app.layout.current_control is screen.page.cancel.control)
        await auxiliary(app, self.pipe, view, 'filter')
        await eventually(lambda: view._dialog is not None)
        self.pipe.send_text('email\r')
        await eventually(lambda: screen.query == 'email' and view._dialog is None)
        self.assertEqual(screen.status_text(), '필터 : email')
        with set_app(app):
            task = view.progress.start('Loading')
            content = screen.layout.status.content.create_content(100, 1)
            self.assertIn('필터 : email', ''.join(fragment[1] for fragment in content.get_line(0)))
            view.progress.finish(task)
        with set_app(app):
            screen.choose('appearance')
        self.assertEqual(screen.page.query, 'email')
        self.assertEqual(screen.page.fields(), [])
        with set_app(app):
            screen.choose('profile')
        self.assertEqual(screen.page.fields(), [screen.page.form.fields[('email',)]])
        with set_app(app):
            screen.filter('')
        self.assertEqual(screen.status_text(), '')
        with patch('hub.ui.host.edit_text', new=AsyncMock(side_effect=lambda text, *a, **k: text)) as editor:
            await auxiliary(app, self.pipe, view, 'json')
            await eventually(lambda: editor.await_count == 1 and not screen.busy)
            self.assertIn('email', editor.call_args.args[0])
            self.assertEqual(editor.call_args.kwargs['suffix'], '.json')
        self.assertIsNone(view._dialog)
        self.assertIs(app.layout.current_control, screen.auxiliary.buttons['json'].control)
        self.pipe.send_text('\x1b')
        await eventually(lambda: app.layout.current_control is screen.page.save.control)
        for width in (132, 80, 62, 60):
            app.output.columns = width
            app._on_resize()
            await self.paint()
            positions = app.renderer._last_screen.visible_windows_to_write_positions
            self.assertIn(screen.page.save.window, positions)
            self.assertIn(screen.auxiliary.buttons['filter'].window, positions)
            self.assertLessEqual(positions[screen.auxiliary.buttons['json'].window].xpos + 3, width - 2)
            button_rows = [positions[button.window].ypos for button in screen.page.buttons]
            self.assertEqual(max(button_rows) + 3, positions[screen.layout.status].ypos)
            self.assertEqual(positions[screen.auxiliary.buttons['filter'].window].ypos + 3,
                             min(button_rows))

    async def test_attachment_inserts_path_and_copy_does_not_replace_notice(self):
        view, app = self.view, self.app
        view.file_root = Path('/project')
        view.composer.text = 'Review'
        view.composer.buffer.cursor_position = len(view.composer.text)
        await auxiliary(app, self.pipe, view, 'attach')
        await eventually(lambda: view._dialog is not None)
        self.pipe.send_text('my file.py\r')
        await eventually(lambda: view._dialog is None)
        self.assertEqual(view.composer.text, 'Review "/project/my file.py" ')
        self.assertIs(app.layout.current_control, view.composer.control)
        view.notice = 'keep'
        with patch('hub.widget.clipboard.export_text', new=AsyncMock(return_value='clipboard_copied')) as copied:
            buffer = view.composer.buffer
            buffer.cursor_position = 0
            buffer.start_selection()
            buffer.cursor_position = 6
            self.pipe.send_text('\x03')
            await eventually(lambda: copied.await_count == 1)
        self.assertEqual(view.notice, 'keep')


class ThemeModeTests(unittest.TestCase):
    def test_theme_mode_derivation_and_grayscale(self):
        dark, light = HubTheme(), HubTheme(mode='light')
        self.assertNotEqual(dark.code_background, light.code_background)
        self.assertNotEqual(dark.code_foreground, light.code_foreground)
        self.assertEqual(HubTheme(**asdict(light)), light)
        self.assertEqual(dark.background, light.background)
        self.assertNotEqual(replace(dark, background='#123456').button_background,
                            replace(light, background='#123456').button_background)
        self.assertEqual(grayscale('default'), 'default')
        self.assertEqual(grayscale('123456'), '#2f2f2f')
        self.assertEqual(grayscale('ansired')[1:3], grayscale('ansired')[3:5])
        with self.assertRaises(ValueError):
            HubTheme(mode='invalid')


class InternalEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_title_engine_hidden_without_discarding_saved_configuration(self):
        from hub.backend.runtime import HubConfig, HubRuntime
        from hub.ui.view import HubView
        from hub.ui.settings.pages import ProjectPage
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(directory, auto_title=False))
            await runtime.start()
            view = HubView()
            try:
                record = await runtime.settings_service.load_project(runtime.project.id)
                private = {'config': {'completion': {'model': 'internal/title'}}}
                record['project']['config'].setdefault('parameters', {}).setdefault('engines', {})['_hub_title'] = private
                record['values']['config'].setdefault('parameters', {}).setdefault('engines', {})['_hub_title'] = private
                page = ProjectPage(view.settings, record)
                self.assertNotIn('_hub_title', page.engine_forms)
                self.assertNotIn('_hub_title', page.engine_keys)
                self.assertEqual(page.values()['config']['parameters']['engines']['_hub_title'], private)
                self.assertIn('loop', page.engine_forms)
                self.assertIn('graph', page.engine_forms)
            finally:
                await runtime.close()
                view.close()
