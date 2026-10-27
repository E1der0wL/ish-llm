"""Shared Settings page behavior remains consistent inside the Agent modal."""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.utils import get_cwidth

from examples.hub.preview import create_preview
from hub.widget.forms.definition_manager import DefinitionManager
from hub.widget.output.reader import ReadOnlyDialog
from hub.backend.project.management_values import EditorRecord
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine
from llm.engines.registry import EngineRegistry
from tests.hub.support import SizedOutput, eventually, render_container


class PopupWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.input = create_pipe_input()
        self.pipe = self.input.__enter__()
        self.app, self.view, _ = create_preview(input=self.pipe, output=SizedOutput())
        self.task = asyncio.create_task(self.app.run_async(pre_run=lambda: self.view.show(self.app)))
        await eventually(lambda: self.app.is_running)
        registry = EngineRegistry()
        registry.register('loop', LoopEngine())
        registry.register('graph', GraphEngine(handlers={}))
        self.requests = []
        def request(action, name, version, done):
            self.requests.append((action, name))
            if action == 'describe':
                done({'schema': registry.describe_agent(name), 'engines': ['loop','graph'],
                      'storage_version': 1})
        self.edit = AsyncMock()
        with set_app(self.app):
            self.manager = DefinitionManager(self.view, request, name='agents', edit_text=self.edit)
            self.view.dialogs.show(self.manager, self.manager.path)
            self.manager.agent_editor('assistant', EditorRecord.create(json.dumps({
                'engine':'loop','completion':{'model':'test/main'}}), 'revision'))
            self.editor = self.view._dialog

    async def asyncTearDown(self):
        self.app.exit()
        await self.task
        self.view.close()
        self.input.__exit__(None, None, None)

    async def test_modal_status_exclusive_and_shared_focus_regions(self):
        with set_app(self.app):
            page = self.editor.workspace.layout
            main_status = self.view.progress.container(lambda: 'main notice').content
            self.editor.status = 'Agent draft status'
            status = page.status.content
            self.assertEqual(main_status.create_content(100,1).get_line(0), [])
            self.assertIn('Agent draft status', str(status.create_content(100,1).get_line(0)))
            token = self.view.progress.start('loading draft', total=4)
            self.view.progress.update(token, completed=2)
            self.assertEqual(main_status.create_content(100,1).get_line(0), [])
            self.assertIn('50%', ''.join(fragment[1] for fragment in status.create_content(100,1).get_line(0)))
            self.assertTrue(page.sidebar_region.active())
            self.assertFalse(page.main_region.active())
            page.main_region.on_click()
            self.assertTrue(self.editor.workspace.tabs.tabs_focused)
            self.assertTrue(page.main_region.active())
            self.assertFalse(page.sidebar_region.active())
            page.sidebar_region.on_click()
            self.assertTrue(page.sidebar_region.active())
            self.view.progress.finish(token)
            self.editor.close()
            self.manager.status = 'manager notice'
            self.assertEqual(status.create_content(100,1).get_line(0), [])
            self.assertIn('manager notice', str(self.manager.status_line.content.create_content(100,1).get_line(0)))
            self.view.close_dialog()
            self.assertIn('main notice', str(main_status.create_content(100,1).get_line(0)))

    async def test_auxiliary_filter_and_json_editor_preserve_validated_draft(self):
        editor = self.editor
        with set_app(self.app):
            self.app.layout.focus(editor.buttons[0])
        self.pipe.send_text('\x13')
        await eventually(lambda: editor.workspace.auxiliary.focused)
        self.pipe.send_text('\x1b')
        await eventually(lambda: self.app.layout.current_control is editor.buttons[0].control)
        with set_app(self.app):
            editor.workspace.select('completion')
            editor.draft_tools.search()
            search = self.view._dialog
            field = self.app.layout.current_control.buffer
            field.text = 'model'
            field.validate_and_handle()
            self.assertIs(self.view._dialog, editor)
            self.assertTrue(editor.workspace.tabs.tabs_focused)
            self.assertEqual([field.label for field in editor.workspace.fields()], ['completion.model'])
            self.assertIn('model', editor.workspace.status_text())
            editor.workspace.query = 'engine'
            editor.workspace.select(editor.workspace.GENERAL)
            self.assertTrue(editor.workspace.engine_visible())
            self.assertIn(editor.engine.button, editor.workspace.controls())
            editor.workspace.query = ''
            editor.workspace.refresh()
            editor.form.fields[('completion','model')].input.text = 'test/draft'
            self.edit.return_value = json.dumps({'engine':'graph', 'engine_options':{'workflow':'flow'}})
            editor.draft_tools.open_json()
        await eventually(lambda: not editor.busy)
        with set_app(self.app):
            self.assertEqual(editor.engine_name, 'graph', editor.status)
            self.assertEqual(editor.engine.value, 'graph')
            self.assertIn('graph', editor.engine.caption())
            self.assertEqual(editor.form.values()['engine_options']['workflow'], 'flow')
            self.assertIn('test/draft', self.edit.call_args.args[0])
            editor.change_engine('loop')
            self.assertEqual(editor.form.values()['completion']['model'], 'test/draft')
            before = editor.form.values()
            self.edit.return_value = '{ invalid JSON'
            editor.draft_tools.open_json()
        await eventually(lambda: not editor.busy)
        with set_app(self.app):
            self.assertEqual(editor.form.values(), before)
            self.assertEqual(editor.draft_tools.recovery_text, '{ invalid JSON')
            self.assertTrue(editor.status)
            self.assertTrue(all(action == 'describe' for action, _ in self.requests))

    async def test_help_markdown_wraps_to_allocated_popup_content(self):
        with set_app(self.app):
            self.manager.help()
            popup = self.view._dialog
            self.assertIsInstance(popup, ReadOnlyDialog)
            self.assertTrue(popup.output.markdown)
            # Synchronous render here isolates width/layout from worker timing.
            popup.output.render_worker = None
            popup.output.text = '**Emphasis**\n\n' + 'Long explanation ' * 100
            for width in (100, 68):
                render_container(popup, width, 30)
                allocated = popup.output.window.render_info.window_width - 1
                content = popup.output.create_content(allocated, 20)
                rows = [''.join(fragment[1] for fragment in content.get_line(i))
                        for i in range(content.line_count)]
                self.assertFalse(any('**Emphasis**' in line for line in rows))
                self.assertIn('Emphasis', '\n'.join(rows))
                self.assertTrue(all(get_cwidth(line) <= allocated for line in rows))
