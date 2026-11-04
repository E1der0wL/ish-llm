"""Direct revisioned Agent editing returns to the unchanged participant picker."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.backend.project.management_values import EditorRecord
from hub.backend.project.participant_groups import ParticipantGroup
from hub.ui.chat.component_ui import ComponentCommands
from hub.widget.forms.agent_editor import AgentEditor
from hub.widget.forms.participant_manager import ParticipantManager
from llm.engines.loop import LoopEngine
from llm.engines.registry import EngineRegistry
from tests.hub.support import SizedOutput, eventually


class ParticipantEditorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        pipe = self.enterContext(create_pipe_input())
        self.app, self.view, _ = create_preview(input=pipe, output=SizedOutput(120, 40))
        self.view.project_id = 'project'
        self.view.component_commands = {'agent': 'agents'}
        self.view.agents = self.view.participant_ids = ('writer', 'reviewer')
        self.view.agent_titles = {'writer': 'Writer', 'reviewer': 'Reviewer'}
        self.view._participant_groups[self.view._draft_key(self.view.selected)] = (
            ParticipantGroup('team', 'Team', ('writer', 'reviewer')),)
        self.pending = []

        def call(operation, *args, completed=None, **kwargs):
            self.assertEqual(operation, 'manage_agents')
            self.assertEqual(args[:2], ('project', 'project'))
            self.pending.append((*args[2:], completed))

        self.controller = SimpleNamespace(view=self.view, _call=call)
        self.commands = ComponentCommands(self.controller)
        self.view.on_component = self.commands.open
        self.task = asyncio.create_task(self.app.run_async(pre_run=lambda: self.view.show(self.app)))
        await eventually(lambda: self.app.is_running)
        with set_app(self.app):
            self.popup = ParticipantManager(self.view)
            self.popup.agents.current_values = ['writer']
            self.popup.select_group('team')
            self.view.dialogs.show(self.popup, self.popup.buttons[1])

    async def asyncTearDown(self):
        self.app.exit()
        await self.task
        self.view.close()

    def finish_read(self):
        action, identifier, version, ready = self.pending.pop(0)
        self.assertEqual((action, identifier, version), ('read', 'writer', None))
        ready(EditorRecord.create(json.dumps({
            'engine': 'loop', 'completion': {'model': 'test/model'},
            'metadata': {'title': 'Writer'},
        }), 'original-revision'))
        editor = self.view._dialog
        self.assertIsInstance(editor, AgentEditor)
        action, engine, _, ready = self.pending.pop(0)
        self.assertEqual((action, engine), ('describe', 'loop'))
        registry = EngineRegistry()
        registry.register('loop', LoopEngine())
        ready({'engines': ['loop'], 'schema': registry.describe_agent('loop'), 'storage_version': 1})
        return editor

    async def test_save_and_cancel_return_without_opening_agent_manager(self):
        with set_app(self.app), patch.object(self.view.dialogs, 'show', wraps=self.view.dialogs.show) as show:
            self.popup.manage()
            self.popup.manage()
            self.assertEqual(len(self.pending), 1)
            self.assertTrue(self.popup.busy)
            self.assertIs(self.view._dialog, self.popup)
            show.assert_not_called()
            editor = self.finish_read()
            self.assertFalse(self.popup.busy)
            self.assertEqual(show.call_count, 1)
            self.assertIs(show.call_args.args[0], editor)
            editor.display_name.text = 'Writer updated'
            editor.save()
            action, _, revision, ready = self.pending.pop(0)
            self.assertEqual((action, revision), ('save', 'original-revision'))
            ready([])
            self.assertIs(self.view._dialog, self.popup)
            self.assertEqual(self.popup.agents.current_values, ['writer'])
            self.assertEqual(self.popup.selected_group, 'team')
            self.assertIs(self.app.layout.current_control, self.popup.buttons[1].control)
            self.popup.manage()
            editor = self.finish_read()
            self.view.close_dialog()
            self.assertIs(self.view._dialog, self.popup)
            self.assertFalse(self.pending)
            self.assertFalse(self.view.progress.progress.tasks)

    async def test_failed_read_and_dispatch_release_parent_without_closing_it(self):
        with set_app(self.app):
            self.popup.manage()
            self.pending.pop()[3](None)
            self.assertFalse(self.popup.busy)
            self.assertIs(self.view._dialog, self.popup)
            self.assertEqual(self.popup.status, self.view.t('tools_failed'))
            with patch.object(self.controller, '_call', side_effect=RuntimeError('unavailable')):
                with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                    self.popup.manage()
            self.assertFalse(self.popup.busy)
            self.assertIs(self.view._dialog, self.popup)
            self.assertFalse(self.view.progress.progress.tasks)

    async def test_direct_sidebar_edit_returns_to_sidebar_without_manager(self):
        with set_app(self.app):
            self.popup.close()
            self.view.output.participants.open()
            self.commands.open('agent', '', consume_input=False, edit_identifier='writer')
            self.assertIsNone(self.view._dialog)
            self.finish_read()
            self.view.close_dialog()
            self.assertIsNone(self.view._dialog)
            self.assertIs(self.app.layout.current_control, self.view.output.participants.control)
            self.assertFalse(self.pending)
