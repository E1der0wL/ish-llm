"""Creation profiles, portable credentials and bounded application actions."""

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.execution.retries import HubLoopEngine
from hub.backend.execution.completion_review import TaskCompletion
from hub.backend.tools.application import AgentMessageContext
from hub.config.defaults import HubDefaults
from hub.config.presets import PresetStore
from llm.components.review import CompletionReviewObservation, ReviewAction
from llm.core.model_protocol import ModelMessage, ModelResponse, ToolCall, ToolResult
from llm.providers.litellm import LiteLLMCompletionAdapter
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class ApplicationDefaultsTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_files_preserve_connections_and_agents_without_seeding_secrets(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False))
            await runtime.start()
            try:
                settings, project = runtime.settings_service, runtime.project
                value = await settings.execute('preset_export', project.id)
                completion = value['project']['agents']['assistant'].get('completion', {})
                self.assertFalse(completion)
                self.assertEqual(value['global']['appearance']['comment2'], '#3A3A3A')
                value['project']['agents']['assistant']['completion'] = {
                    'model': 'test/chosen', 'api_key': 'test-secret', 'api_base': 'https://example.invalid'}
                value['project']['agents']['reviewer'] = {'engine': 'loop', 'tools': [],
                                                          'completion': {'model': 'test/reviewer'}}
                await settings.execute('preset_save', '사내 업무', value)
                path = Path(root)/'.hub/presets/사내 업무.json'
                self.assertEqual(json.loads(path.read_text()), value)
                self.assertEqual(path.stat().st_mode & 0o077, 0)
                self.assertFalse((Path(root)/'.hub/presets.json').exists())
                result = await settings.execute('preset_apply', '사내 업무', project.id)
                self.assertIsNone(result['batch']['error'])
                self.assertEqual((await project.agents.aload('reviewer'))['completion']['model'], 'test/reviewer')
                again = await settings.execute('preset_export', project.id)
                self.assertEqual(again['project']['agents'], value['project']['agents'])
                for name in ('../outside', 'a/b', 'a\\b', '.', 'CON', 'bad:port'):
                    with self.subTest(name=name), self.assertRaises(ValueError):
                        PresetStore(root).save(name, value)
                with self.assertRaises(ValueError):
                    PresetStore(root).save('사내 업무', value)
            finally:
                await runtime.close()
            fresh = HubRuntime(HubConfig(Path(root)/'fresh', _workspace_anchor=Path(root), auto_title=False))
            await fresh.start()
            try:
                self.assertEqual((await fresh.project.agents.aload('reviewer'))['completion']['model'], 'test/reviewer')
            finally:
                await fresh.close()

    async def test_optional_review_accepts_real_effect_and_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            provider = ScriptedCompletion(
                [chunk(calls=[call(json.dumps({'path': 'result.txt', 'content': 'done'}),
                                   name='file_create', call_id='write')], finish='tool_calls')],
                [chunk(calls=[call(json.dumps({'outcome':'completed','summary':'Created result.txt',
                                               'evidence':['write']}), name=TaskCompletion.NAME,
                                   call_id='review')], finish='tool_calls')],
                [chunk('Created result.txt.', finish='stop')],
            )
            runtime = HubRuntime(HubConfig(Path(root)/'hub', file_root=root,
                model='test/review', auto_title=False,
                project_config={'policies': {'approval': {'enabled': False}}},
                engine_factories={'loop': lambda: HubLoopEngine(
                    completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
            await runtime.start()
            try:
                sid = await runtime.new_session()
                request = await runtime.sessions[sid].run.submit('Create result.txt containing done.', recipients=['assistant'])
                run, = await asyncio.wait_for(request.wait(), 15)
                self.assertEqual(str((await run.aresult()).status), 'paused')
                approval, = await run.ainteractions(pending_only=True)
                await run.arespond(approval.respond('approve'))
                resumed = await runtime.sessions[sid].run.resume(run.id, agent_id='assistant')
                run, = await asyncio.wait_for(resumed.wait(), 15)
                self.assertEqual(str((await run.aresult()).status), 'completed')
                self.assertEqual((Path(root)/'result.txt').read_text(), 'done')
                self.assertEqual((await run.aresponse()).content, 'Created result.txt.')
                self.assertEqual(len(provider.requests), 3)
                self.assertIn(TaskCompletion.ROUTING, str(provider.requests[0]['messages']))
            finally:
                await runtime.close()

    async def test_session_peer_delivery_uses_saved_membership_and_preserves_context(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False))
            await runtime.start()
            try:
                await runtime.project.agents.acreate({'engine':'loop','completion':{'model':'test/peer'},'tools':[]}, identifier='peer')
                sid = await runtime.new_session()
                await runtime.invite_agent(runtime.project.id, sid, 'peer')
                application = runtime.builtin_tools.application
                session = runtime.sessions[sid]
                from unittest.mock import AsyncMock
                from llm.core.agents import AgentBinding
                source = SimpleNamespace(agent_id='assistant', metadata={
                    AgentBinding.CONTEXT_FIELD: {'shell': 'preserved'}})
                with patch.object(application, 'scope', new=AsyncMock(return_value=(runtime.project,session,source))), \
                     patch.object(session.run, 'submit', new=AsyncMock(return_value=SimpleNamespace(id='queued'))) as submit:
                    result = await application.send({'recipient':'peer','message':'Please review the result.'})
                    self.assertEqual(result['delivery'], 'queued')
                    options = submit.call_args.kwargs
                    self.assertEqual(options['recipients'], ['peer'])
                    self.assertEqual(options['request_context']['shell'], 'preserved')
                    for recipient in ('assistant', 'outside'):
                        with self.assertRaises(ValueError):
                            await application.send({'recipient':recipient,'message':'x'})
                    source.metadata[AgentBinding.CONTEXT_FIELD][AgentMessageContext.KEY] = {
                        'hops':4,'sender':'peer'}
                    with self.assertRaises(ValueError):
                        await application.send({'recipient':'peer','message':'x'})
                    self.assertEqual(submit.await_count, 1)
            finally:
                await runtime.close()

    async def test_peer_message_is_admitted_durably_and_executes_in_same_session(self):
        from llm.engines.loop import LoopEngine
        from hub.config.environment import ENVIRONMENT_OPTION
        from tests.hub.test_shell_environment import snapshot
        provider = ScriptedCompletion(
            [chunk(calls=[call(json.dumps({'recipient':'peer','message':'Check the result'}),
                               name='hub_agent_message', call_id='send')], finish='tool_calls')],
            [chunk('Received peer message.', finish='stop')],
            [chunk('Queued peer work.', finish='stop')],
        )
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(Path(root)/'hub', file_root=root, auto_title=False,
                model='test/peer', engine_factories={'loop':lambda: LoopEngine(
                    completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
            await runtime.start()
            try:
                await runtime.project.agents.acreate({'engine':'loop','completion':{'model':'test/peer'},'tools':[]}, identifier='peer')
                sid = await runtime.new_session()
                await runtime.invite_agent(runtime.project.id, sid, 'peer')
                session = runtime.sessions[sid]
                request = await session.run.submit('Ask the peer to check.', recipients=['assistant'],
                    request_context={ENVIRONMENT_OPTION: snapshot(root,'captured').to_dict()})
                paused, = await asyncio.wait_for(request.wait(), 15)
                approval, = await paused.ainteractions(pending_only=True)
                await paused.arespond(approval.respond('approve'))
                resumed = await session.run.resume(paused.id, agent_id='assistant')
                await asyncio.wait_for(resumed.wait(), 15)
                await asyncio.wait_for(session.run.wait_idle(), 15)
                records = [await row.aget_data() for row in await session.run.alist()]
                peers = [row for row in records if row.agent_id == 'peer']
                self.assertEqual(len(peers), 1)
                self.assertEqual(str(peers[0].status), 'completed', peers[0].error)
                from llm.core.agents import AgentBinding
                context = AgentBinding.request_context(peers[0].metadata)
                self.assertEqual(context[ENVIRONMENT_OPTION], snapshot(root,'captured').to_dict())
                self.assertEqual(context[AgentMessageContext.KEY], {'sender':'assistant','hops':1})
            finally:
                await runtime.close()

    def test_review_does_not_accept_fabricated_evidence_or_require_work_for_information(self):
        report = ToolCall('report', TaskCompletion.NAME,
                          {'outcome':'completed','summary':'changed','evidence':['invented']})
        messages = (ModelMessage('user','Do work'), ModelMessage('assistant', calls=(report,)),
                    ModelMessage('tool', result=ToolResult('report','recorded')))
        from llm.components.tools import ToolRegistry
        observation = CompletionReviewObservation(1, messages, ModelResponse(ModelMessage('assistant','done')),
                                                   messages, ToolRegistry((TaskCompletion.tool(),)).definitions())
        self.assertEqual(TaskCompletion.review(observation).action, ReviewAction.CONTINUE)
        information = ToolCall('report', TaskCompletion.NAME,
                               {'outcome':'information','summary':'Explanation only','evidence':[]})
        informational = (messages[0], ModelMessage('assistant', calls=(information,)), messages[-1])
        accepted = CompletionReviewObservation(1, informational, observation.response,
                                               informational, observation.tools)
        self.assertEqual(TaskCompletion.review(accepted).action, ReviewAction.ACCEPT)
        invalid = ToolCall('report', TaskCompletion.NAME, {'outcome':'made-up'})
        invalid_messages = (messages[0], ModelMessage('assistant', calls=(invalid,)), messages[-1])
        rejected = CompletionReviewObservation(1, invalid_messages, observation.response,
                                               invalid_messages, observation.tools)
        self.assertEqual(TaskCompletion.review(rejected).action, ReviewAction.CONTINUE)

    def test_default_profiles_are_detached(self):
        values = HubDefaults.globals()
        values['appearance']['accent1'] = 'red'
        self.assertNotEqual(HubDefaults.globals()['appearance']['accent1'], 'red')
        components = HubDefaults.components()
        components.clear()
        self.assertTrue(HubDefaults.components())
        from hub.config.preset_records import PresetGlobals
        saved = HubDefaults.globals()['appearance']
        saved.pop('comment2')
        saved.pop('accent4')
        saved.pop('accent5')
        saved['foreground'] = '#123456'
        exported = PresetGlobals.snapshot(None, lambda: {'appearance': saved})
        self.assertEqual(exported['appearance']['foreground'], '#123456')
        self.assertEqual(exported['appearance']['comment2'], '#3A3A3A')
        self.assertEqual(exported['appearance']['accent4'], '#A8B2BF')
        self.assertEqual(exported['appearance']['accent5'], '#1D2035')
        self.assertNotIn('comment2', saved)

    def test_palette_preferences_use_additive_defaults_without_legacy_migration(self):
        from hub.config.preferences import PreferencesStore
        saved = HubDefaults.globals()['appearance']
        saved.pop('accent4')
        saved.pop('accent5')
        saved['accent1'] = '#123456'
        validated = PreferencesStore.validate_section('appearance', saved)
        self.assertEqual(validated['accent1'], '#123456')
        self.assertEqual(validated['accent4'], '#A8B2BF')
        self.assertEqual(validated['accent5'], '#1D2035')
        self.assertNotIn('accent4', saved)
        with self.assertRaises(TypeError):
            PreferencesStore.validate_section('appearance', {**saved, 'mode': 'dark'})
        with self.assertRaises(ValueError):
            PreferencesStore.validate_section('appearance', {**validated, 'accent5': 'invalid'})

    def test_dropdown_uses_completion_styles_without_radio_or_border(self):
        from hub.widget.dropdown import DropdownBox, CompletionChoices
        dropdown = DropdownBox([('a','Alpha'),('b','Beta')], value='a')
        dropdown.choices.values = dropdown.values()
        fragments = dropdown.choices._get_text_fragments()
        rendered = ''.join(item[1] for item in fragments)
        self.assertNotIn('(', rendered)
        self.assertNotIn('┌', rendered)
        self.assertTrue(any(item[0] == CompletionChoices.CURRENT for item in fragments))
        self.assertTrue(any(item[0] == CompletionChoices.NORMAL for item in fragments))
        dropdown.choices.values = [('long', '아주 긴 프리셋 이름 ' * 20)]
        from prompt_toolkit.formatted_text import fragment_list_width
        clipped = dropdown.choices._get_text_fragments()
        self.assertEqual(fragment_list_width(clipped), dropdown.rendered_width() - 1)
        self.assertIn('…', ''.join(item[1] for item in clipped))
