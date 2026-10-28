"""Agent-owned models, schema editors and durable participant group routing."""

import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import Mock, AsyncMock

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.document import Document
from prompt_toolkit.completion import CompleteEvent
from hub.backend.runtime import HubRuntime, HubConfig
from hub.backend.project.participant_groups import ParticipantAction, ParticipantGroups, ParticipantGroup
from hub.backend.project.record_management import RecordManagement
from hub.backend.project.management_values import EditorRecord
from hub.backend.project.component_commands import component_commands, parse_arguments
from hub.locales import Language
from hub.ui.chat.completion import HubCompleter
from hub.ui.input.commands import HubCommands
from llm.engines.base import BaseEngine
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine
from tests.hub.support import SizedOutput, eventually, render_container


class AgentContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_assistant_model_and_prompt_belong_to_agent(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model='test/model', api_base='http://test.invalid', auto_title=False))
            try:
                await runtime.start()
                agent = await runtime.project.agents.aload('assistant')
                self.assertEqual(agent['completion']['model'], 'test/model')
                self.assertEqual(agent['completion']['api_base'], 'http://test.invalid')
                self.assertTrue(agent['system_prompt'])
                record = await runtime.project.aget_data()
                self.assertNotIn('completion', record.config.parameter_targets.engine('loop').config)
                manager = await RecordManagement.for_project(runtime.project, 'agents')
                form = await manager.execute('describe', 'loop')
                self.assertIn('model', form['schema']['properties']['completion']['properties'])
                graph = await manager.execute('describe', 'graph')
                self.assertNotIn('completion', graph['schema']['properties'])
                self.assertEqual((await runtime.snapshot()).model, 'test/model')
            finally:
                await runtime.close()

    async def test_groups_persist_and_deletion_keeps_agents(self):
        with tempfile.TemporaryDirectory() as root:
            config = HubConfig(root, auto_title=False, agent_definition={'engine':'host'},
                               engine_factories={'host': lambda: BaseEngine(action=lambda ctx: 'ok')})
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                sid = await runtime.new_session()
                pid = runtime.project.id
                await runtime.project.agents.acreate({'engine':'host'}, identifier='reviewer')
                await runtime.invite_agent(pid, sid, 'reviewer')
                await runtime.change_participants(pid, sid, ParticipantAction.ADD_GROUP, '', 'Team')
                group, = (await runtime.snapshot()).sessions[0].participant_groups
                for agent in ('assistant', 'reviewer'):
                    await runtime.change_participants(pid, sid, ParticipantAction.MOVE, agent, group.id)
                group, = (await runtime.snapshot()).sessions[0].participant_groups
                self.assertEqual(set(group.members), {'assistant','reviewer'})
            finally:
                await runtime.close()
            runtime = HubRuntime(replace(config, project_id=pid, session_id=sid))
            try:
                await runtime.start()
                self.assertEqual((await runtime.snapshot()).sessions[0].participant_groups, (group,))
                await runtime.change_participants(pid, sid, ParticipantAction.DELETE_GROUP, group.id, '')
                snapshot = await runtime.snapshot()
                self.assertEqual(snapshot.sessions[0].participant_groups, ())
                self.assertEqual(snapshot.participant_ids, ('assistant','reviewer'))
                await runtime.change_participants(pid, sid, ParticipantAction.EXPEL, 'reviewer', '')
                self.assertEqual((await runtime.snapshot()).participant_ids, ('assistant',))
                self.assertIn('reviewer', await runtime.project.agents.alist())
            finally:
                await runtime.close()

    def test_singular_commands_open_managers_without_arguments(self):
        commands = component_commands(('tools','skills','mcp','rag','workflows','memory','prompts'))
        self.assertEqual(set(commands), {'tool','skill','mcp','rag','workflow','agent','memory','prompt'})
        language = Language('en')
        completion = HubCompleter(lambda: '.', language, components=lambda: commands)
        suggestions = [c.text for c in completion.get_completions(Document('/'), CompleteEvent())]
        self.assertEqual(suggestions.count('/agent'), 1)
        for command in commands:
            view = Mock(component_commands=commands)
            self.assertTrue(HubCommands(view).execute('/'+command))
            view.on_component.assert_called_once_with(command, '')
            self.assertIsNone(parse_arguments('', language))
            self.assertEqual(list(completion.get_completions(Document('/'+command+' '), CompleteEvent())), [])
        for argument in ('config', 'help', 'reviewer'):
            with self.assertRaises(ValueError):
                parse_arguments(argument, language)


class AgentEditorTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_menu_targets_members_and_agent_menu_dispatches_actions(self):
        from examples.hub.preview import create_preview
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            view.participant_ids = ('a', 'b', 'c')
            view._participant_groups[view._draft_key(view.selected)] = (
                ParticipantGroup('group:team', 'Team', ('a', 'b')),)
            calls = []
            view.on_change_participants = lambda *args: calls.append(args[:3])
            view.on_component = Mock()
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                with set_app(app):
                    bar = view.output.participants
                    bar.open()
                    menus = []
                    bar.choose = lambda choices, accept: menus.append((choices, accept))
                    bar.selected = 'group:team'
                    bar.expand()
                    self.assertEqual(len(menus[-1][0]), 2)
                    menus[-1][1](0)
                    self.assertEqual(view.recipients, ('a', 'b'))
                    self.assertIn('Team', view.recipient_title())
                    menus[-1][1](0)
                    self.assertEqual(view.recipients, ('a', 'b', 'c'))
                    menus[-1][1](1)
                    self.assertEqual(calls[-1], (ParticipantAction.DELETE_GROUP, 'group:team', ''))
                    bar.selected = 'a'
                    bar.expand()
                    self.assertEqual(len(menus[-1][0]), 4)
                    menus[-1][1](0)
                    view.on_component.assert_called_once_with('agent', '', consume_input=False, edit_identifier='a')
                    menus[-1][1](3)
                    self.assertEqual(calls[-1], (ParticipantAction.EXPEL, 'a', ''))
                    menus[-1][1](1)
                    self.assertEqual(menus[-1][0][-1], ('group:team', 'Team'))
                    menus[-1][1]('group:team')
                    self.assertEqual(calls[-1], (ParticipantAction.MOVE, 'a', 'group:team'))
            finally:
                app.exit()
                await task
                view.close()

    async def test_schema_form_engine_switch_preserves_drafts_and_revision(self):
        from examples.hub.preview import create_preview
        from hub.widget.forms.definition_manager import DefinitionManager
        from hub.widget.forms.agent_editor import AgentEditor
        from llm.engines.registry import EngineRegistry
        registry = EngineRegistry()
        registry.register('loop', LoopEngine())
        registry.register('graph', GraphEngine(handlers={}))
        schemas = {name: registry.describe_agent(name) for name in ('loop', 'graph')}
        schemas['loop']['allOf'].append({'properties': {'completion': {
            'properties': {'temperature': {'maximum': 0.5}}}}})
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            saved = []
            def request(action, argument, revision, done):
                if action == 'describe':
                    done({'engines':['loop','graph'], 'schema':schemas[argument], 'storage_version':1})
                else:
                    saved.append((action, argument, revision))
                    done([])
            edit_text = AsyncMock(return_value='Coder')
            manager = DefinitionManager(view, request, name='agents', edit_text=edit_text)
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                with set_app(app):
                    view.dialogs.show(manager, manager.path)
                    manager.agent_editor('assistant', EditorRecord.create(json.dumps({
                        'engine':'loop', 'completion':{'model':'test/model'}, 'metadata':{'title':'Coder'},
                    }), 'revision'))
                    editor = view._dialog
                    self.assertIsInstance(editor, AgentEditor)
                    self.assertIs(app.layout.current_control, editor.workspace.sidebar)
                from hub.widget.focus_region import grayscale
                for background in ('default', '#123456'):
                    for focused in (True, False):
                        with set_app(app):
                            view.set_theme(replace(view.theme, background=background))
                            if focused:
                                editor.workspace.focus_sidebar()
                            else:
                                editor.workspace.tabs.focus_tabs()
                        rendered_at = app.render_counter
                        app.invalidate()
                        await eventually(lambda: app.render_counter > rendered_at)
                        frame = app.renderer._last_screen
                        window = next(w for w in frame.visible_windows_to_write_positions
                                      if w.content is editor.workspace.sidebar)
                        rect = frame.visible_windows_to_write_positions[window]
                        expected = background.lstrip('#') if focused else grayscale(background.lstrip('#')).lstrip('#')
                        for y in range(rect.ypos, rect.ypos + rect.height):
                            cell = frame.data_buffer[y][rect.xpos]
                            self.assertEqual(app._merged_style.get_attrs_for_style_str(cell.style).bgcolor, expected)
                with set_app(app):
                    editor.workspace.focus_sidebar()
                pipe.send_text('\x1b[C')
                await eventually(lambda: editor.workspace.tabs.tabs_focused)
                pipe.send_text('\r')
                await eventually(lambda: app.layout.current_control is editor.identifier.control)
                pipe.send_text('\x1b[B')
                await eventually(lambda: app.layout.current_control is editor.display_name.control)
                pipe.send_text('d')
                await eventually(lambda: editor.display_name.text == '')
                pipe.send_text('e')
                await eventually(lambda: editor.display_name.text == 'Coder' and not editor.busy)
                edit_text.assert_awaited_once()
                # Text changes invalidate the app, but PTK caches formatted
                # controls until the next render counter. Inspect that frame,
                # not the old frame left by the preceding clear action.
                previous_render = app.render_counter
                app.invalidate()
                await eventually(lambda: app.render_counter > previous_render)
                with set_app(app):
                    rendered = '\n'.join(render_container(editor, 128, 36))
                    self.assertIn('에이전트 • 일반', rendered)
                    self.assertIn('에이전트 : Coder', rendered)
                    self.assertIn(' 에이전트', rendered)
                    self.assertGreater(editor.display_name.window.render_info.window_width, 60)
                    from hub.ui.layout import TwoPanelPage
                    self.assertIsInstance(editor.workspace.layout, TwoPanelPage)
                    sidebar = editor.workspace.layout.sidebar_region
                    self.assertEqual(sidebar.preferred_width(128).preferred, view.sidebar_width())
                pipe.send_text('\x1b[D')
                await eventually(lambda: app.layout.current_control is editor.workspace.sidebar)
                pipe.send_text('\x1b[C\r')
                await eventually(lambda: app.layout.current_control is editor.identifier.control)
                pipe.send_text('\t')
                await eventually(lambda: app.layout.current_control is editor.buttons[0].control)
                pipe.send_text('\x1b[C')
                await eventually(lambda: app.layout.current_control is editor.buttons[1].control)
                pipe.send_text('\x1b')
                await eventually(lambda: app.layout.current_control is editor.workspace.sidebar)
                with set_app(app):
                    editor.workspace.select('completion')
                    editor.workspace.focus_fields()
                    self.assertIn(('completion','model'), editor.form.fields)
                    editor.form.fields[('completion','model')].input.text = 'test/edited'
                    editor.change_engine('graph')
                    self.assertEqual(editor.engine.value, 'graph')
                    self.assertIn('graph', editor.engine.caption())
                    self.assertFalse(any(path[0]=='completion' for path in editor.form.fields))
                    editor.change_engine('loop')
                    self.assertEqual(editor.engine.value, 'loop')
                    self.assertEqual(editor.form.fields[('completion','model')].input.text, 'test/edited')
                    self.assertTrue(render_container(editor, 100, 30))
                    editor.form.fields[('completion','temperature')].input.text = '1'
                    editor.save()
                    self.assertEqual(saved, [])  # Root allOf constraints are still enforced.
                    self.assertIs(view._dialog, editor)
                    editor.form.fields[('completion','temperature')].input.text = ''
                    editor.save()
                    action, argument, revision = saved[0]
                    self.assertEqual((action, revision), ('save','revision'))
                    values = json.loads(argument['text'])
                    self.assertEqual(values['completion']['model'], 'test/edited')
                    self.assertEqual(values['metadata'], {'title':'Coder'})
            finally:
                app.exit()
                await task
                view.close()
