"""Unified Agent list routes revisioned edits to the original owning Project."""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.backend.project.management_values import AgentLocation, EditorRecord, ManagementEntry
from hub.ui.chat.component_ui import ComponentCommands
from hub.widget.forms.agent_editor import AgentEditor
from hub.widget.forms.agent_review import AgentReview
from hub.widget.forms.settings import at_path, MISSING
from llm.engines.loop import LoopEngine
from llm.engines.registry import EngineRegistry
from tests.hub.support import SizedOutput, eventually


class UnifiedAgentManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_plugin_edit_returns_to_same_manager_and_keeps_original_owner_and_revision(self):
        pipe = self.enterContext(create_pipe_input())
        app, view, _ = create_preview(input=pipe, output=SizedOutput())
        view.project_id = 'home'
        view.component_commands = {'agent': 'agents'}
        pending = []
        def call(operation, *args, completed, **kwargs):
            self.assertEqual(operation, 'manage_agents')
            pending.append((args, completed))
        commands = ComponentCommands(SimpleNamespace(view=view, _call=call))
        task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
        try:
            await eventually(lambda: app.is_running)
            with set_app(app):
                commands.open('agent', '')
                popup = view._dialog
                local = AgentLocation.entry(ManagementEntry.record('helper', 'Local', '', '', 'local-version'),
                                            SimpleNamespace(id='home'))
                plugin = AgentLocation.entry(ManagementEntry.record('helper', 'Plugin', '', '', 'plugin-version'),
                                             SimpleNamespace(id='plugin'), plugin='Shell helper')
                rows = [local, plugin]
                original_rows = deepcopy(rows)
                args, done = pending.pop(0)
                self.assertEqual(args[:3], ('home', 'home', 'list'))
                done(rows)
                popup.items()
                self.assertEqual(rows, original_rows)
                popup.selected = plugin['name']
                popup.open_item()
                args, done = pending.pop(0)
                self.assertEqual(args, ('home', 'plugin', 'read', 'helper', None))
                done(EditorRecord.create(json.dumps({'engine': 'loop', 'completion': {'model': 'test/model'}}),
                                         'plugin-version'))
                editor = view._dialog
                self.assertIsInstance(editor, AgentEditor)
                args, done = pending.pop(0)
                self.assertEqual(args, ('home', 'plugin', 'describe', 'loop', None))
                registry = EngineRegistry()
                registry.register('loop', LoopEngine())
                done({'engines': ['loop'], 'schema': registry.describe_agent('loop'), 'storage_version': 1})
                self.assertTrue(editor.review.available)
                self.assertFalse(editor.dirty)
                editor.review.choice.value = AgentReview.DISABLED
                self.assertTrue(editor.dirty)
                editor.save()
                args, done = pending.pop(0)
                self.assertEqual(args[:3], ('home', 'plugin', 'save'))
                self.assertEqual(args[3]['name'], 'helper')
                self.assertIsNone(at_path(json.loads(args[3]['text']), AgentReview.PATH))
                self.assertEqual(args[4], 'plugin-version')
                done(rows)
                self.assertIs(view._dialog, popup)
                self.assertEqual(popup.selected, plugin['name'])
                args, done = pending.pop(0)
                self.assertEqual(args[:3], ('home', 'home', 'list'))
                done(rows)
                popup.create_item()
                args, done = pending.pop(0)
                self.assertEqual(args[:3], ('home', 'home', 'describe'))
        finally:
            app.exit()
            await task
            view.close()


class AgentReviewTests(unittest.TestCase):
    def test_missing_null_custom_budgets_and_unrelated_fields_are_preserved_until_changed(self):
        registry = EngineRegistry()
        registry.register('loop', LoopEngine())
        schema = registry.describe_agent('loop')
        editor = SimpleNamespace(view=SimpleNamespace(t=lambda key: str(key), icons=None))
        for definition in ({'engine': 'loop'}, {'engine': 'loop', 'engine_options': {'policy': {'final_review': None}}},
                           {'engine': 'loop', 'engine_options': {'policy': {'final_review': {'max_attempts': 7}}}}):
            with self.subTest(definition=definition):
                review = AgentReview(editor, schema, definition)
                values = deepcopy(definition)
                review.apply(values)
                self.assertEqual(values, definition)
                review.choice.value = review.INHERIT
                review.apply(values)
                self.assertIs(at_path(values, review.PATH), MISSING)
        absent = AgentReview(editor, {'type': 'object'}, {'engine': 'host'})
        self.assertFalse(absent.available)
        absent.choice.value = absent.ENABLED
        values = {'engine': 'host'}
        absent.apply(values)
        self.assertEqual(values, {'engine': 'host'})
