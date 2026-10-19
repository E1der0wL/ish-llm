"""Canonical Agent Runs and the explicit engine-request compatibility adapter."""
from llm.providers.litellm import LiteLLMCompletionAdapter

import asyncio
from contextlib import asynccontextmanager
from copy import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from llm.llm import LargeLanguageModel
from llm.engines.base import BaseEngine, EngineEventType
from llm.engines.graph import GraphEngine
from llm.engines.loop import LoopEngine
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.components.skills import SkillComponent
from llm.components.prompts import PromptComponent
from llm.components.mcp import MCPComponent
from llm.components.tools import Tool, ToolRegistry, ToolContract
from llm.services.runtime.runs import RunRequestError
from tests.llm.support.fake_engine import FakeStreamingEngine
from tests.llm.support.runtime_tools import RuntimeTools
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class AgentRunTests(unittest.IsolatedAsyncioTestCase):
    async def setup_app(self, *, engines=None, components=(), config=None, on_event=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.engine = FakeStreamingEngine(chunks=('result',))
        self.app = LargeLanguageModel(self.root, engines=engines or {'echo': self.engine},
            components=list(components), on_event=on_event)
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=[c.name for c in components], config=config)
        self.session = await self.project.sessions.acreate()
        return self.project, self.session

    async def test_minimal_agent_identity_snapshot_and_legacy_share_dispatch(self):
        project, session = await self.setup_app()
        await project.agents.acreate({'engine': 'echo'}, identifier='writer')
        await session.agents.aattach('writer')
        request = await session.run.submit('first', agent_id='writer')
        run = await request.wait(timeout=10)
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(run.data.agent_id, 'writer')
        self.assertEqual(run.data.metadata['agent_binding']['definition'], {'engine': 'echo'})
        self.assertEqual(run.data.metadata['agent_execution']['agent_id'], 'writer')
        self.assertEqual(self.engine.contexts[-1].run.agent_id, 'writer')
        self.assertTrue(all(step.run_id == run.id for step in await run.steps.alist()))
        legacy = await (await session.run.submit('second', engine='echo')).wait(timeout=10)
        again = await (await session.run.submit('third', engine='echo')).wait(timeout=10)
        self.assertEqual(legacy.data.agent_id, again.data.agent_id)
        self.assertNotEqual(legacy.data.agent_id, 'writer')
        self.assertEqual(legacy.data.metadata['agent_binding']['adapter'], 'engine-request-v1')
        self.assertEqual((await project.agents.aload(legacy.data.agent_id))['engine'], 'echo')
        self.assertEqual(len(await project.agents.alist()), 2)
        self.assertEqual([c.run.agent_id for c in self.engine.contexts], ['writer', legacy.data.agent_id, legacy.data.agent_id])
        raw = json.loads((run.data.paths.root / 'run.json').read_text())
        self.assertEqual(raw['storage_version'], 2)
        self.assertEqual(raw['agent_id'], 'writer')

    async def test_invalid_or_ambiguous_selection_has_no_admission(self):
        project, session = await self.setup_app()
        await project.agents.acreate({'engine': 'echo'}, identifier='writer')
        await session.agents.aattach('writer')
        for options in ({'agent_id': 'missing'}, {'agent_id': None}, {'agent_id': '../writer'},
                        {'agent_id': 'writer', 'engine': 'echo'},
                        {'agent_id': 'writer', 'engine_options': {}}, {'engine': 'missing'}):
            with self.subTest(options=options), self.assertRaises((ValueError, TypeError)):
                await session.run.submit('invalid', **options)
        with self.assertRaises(TypeError):
            await session.run.submit('unspecified')
        self.assertEqual(await session.run.alist(), [])
        self.assertEqual(await session.aconversation(), [])
        self.assertEqual(self.engine.contexts, [])
        self.assertEqual(list(await project.agents.alist()), ['writer'])

    async def test_domain_schemas_do_not_require_a_custom_engine_factory(self):
        project, session = await self.setup_app()
        await project.agents.acreate({'engine': 'echo', 'tools': [],
            'input_schema': {'type': 'string', 'minLength': 2},
            'output_schema': {'type': 'object', 'required': ['text']}}, identifier='validated')
        await session.agents.aattach('validated')
        bad = await (await session.run.submit('x', agent_id='validated')).wait(timeout=10)
        self.assertEqual(bad.data.status, 'failed')
        self.assertEqual(self.engine.contexts, [])
        good = await (await session.run.submit('valid', agent_id='validated')).wait(timeout=10)
        self.assertEqual(good.data.status, 'completed', good.data.error)
        self.assertEqual(len(self.engine.contexts), 1)

    async def test_runtime_configuration_identity_never_copies_credentials_to_history(self):
        credential = {'value': 'fake-host-secret-before'}
        class Worker(BaseEngine):
            def resolve_config(self, config, name, *, session_config=None):
                return {'values': {'credential': credential['value']}}
            def bind_execution(self, context):
                return {'private_runtime_value': credential['value']}
            async def run(self, context):
                yield 'done'
        project, session = await self.setup_app(engines={'worker': Worker()})
        await project.agents.acreate({'engine': 'worker'}, identifier='writer')
        await session.agents.aattach('writer')
        first = await (await session.run.submit('one', agent_id='writer')).wait(timeout=10)
        credential['value'] = 'fake-host-secret-after'
        second = await (await session.run.submit('two', agent_id='writer')).wait(timeout=10)
        for run in (first, second):
            self.assertEqual(run.data.status, 'completed', run.data.error)
            identity = run.data.metadata['agent_execution']
            for name in ('configuration', 'execution'):
                self.assertEqual(set(identity[name]), {'revision'})
                self.assertEqual(len(identity[name]['revision']), 64)
        self.assertEqual(first.data.metadata['agent_binding'], second.data.metadata['agent_binding'])
        self.assertNotEqual(first.data.metadata['agent_execution']['configuration'],
                            second.data.metadata['agent_execution']['configuration'])
        self.assertNotEqual(first.data.metadata['agent_execution']['execution'],
                            second.data.metadata['agent_execution']['execution'])
        for path in project.paths.root.rglob('*.json*'):
            self.assertNotIn('fake-host-secret-', path.read_text(), str(path))

    async def test_queued_definition_change_blocks_effect_and_delete_is_protected(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def observe(run, event):
            if event.type == EngineEventType.TEXT_DELTA:
                entered.set()
        engine = FakeStreamingEngine(gate=release)
        project, session = await self.setup_app(engines={'echo': engine}, on_event=observe)
        await project.agents.acreate({'engine': 'echo'}, identifier='writer')
        await session.agents.aattach('writer')
        first = await session.run.submit('held', engine='echo')
        await asyncio.wait_for(entered.wait(), 10)
        queued = await session.run.submit('queued', agent_id='writer')
        await session.agents.adetach('writer')
        try:
            with self.assertRaisesRegex(ValueError, 'queued request'):
                await project.agents.adelete('writer')
            version = (await project.agents.asnapshot('writer'))['revision']
            await project.agents.arevise('writer', {'engine': 'echo', 'description': 'changed'}, expected_revision=version)
        finally:
            release.set()
        self.assertEqual((await first.wait(timeout=10)).data.status, 'completed')
        failed = await queued.wait(timeout=10)
        self.assertEqual(failed.data.status, 'failed')
        self.assertEqual(failed.data.error_code, 'agent_binding_changed')
        self.assertEqual(len(engine.contexts), 1)
        self.assertEqual(await failed.steps.alist(), [])

    async def test_active_definition_snapshot_does_not_change(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class Worker(BaseEngine):
            def for_agent(self, definition):
                result = copy(self)
                result.label = definition.get('description', 'original')
                return result
            async def run(self, context):
                entered.set()
                await release.wait()
                yield self.label
        project, session = await self.setup_app(engines={'worker': Worker()})
        await project.agents.acreate({'engine': 'worker'}, identifier='writer')
        await session.agents.aattach('writer')
        before = await project.agents.asnapshot('writer')
        request = await session.run.submit('held', agent_id='writer')
        await asyncio.wait_for(entered.wait(), 10)
        try:
            await project.agents.arevise('writer', {'engine': 'worker', 'description': 'later'}, expected_revision=before['revision'])
        finally:
            release.set()
        run = await request.wait(timeout=10)
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual((await run.aresponse()).content, 'original')
        self.assertEqual(run.data.metadata['agent_binding']['revision'], before['revision'])
        next_run = await (await session.run.submit('new', agent_id='writer')).wait(timeout=10)
        self.assertEqual((await next_run.aresponse()).content, 'later')

    async def test_graph_agent_resume_keeps_identity_and_completed_effect(self):
        effects = []
        async def work(node):
            effects.append(node.node_id)
            return {}
        project, session = await self.setup_app(engines={'graph': GraphEngine(handlers={'work': work})},
                                                 components=[WorkflowComponent()])
        graph = (WorkflowGraph(entry='a').node('a', 'work').node('b', 'work', pause_before=True)
                 .node('end', 'end').connect('a', 'b').connect('b', 'end').to_dict())
        await project.components.workflows.acreate(graph, identifier='flow')
        await project.agents.acreate({'engine': 'graph', 'engine_options': {'workflow': 'flow'}}, identifier='coordinator')
        await session.agents.aattach('coordinator')
        paused = await (await session.run.submit('go', agent_id='coordinator')).wait(timeout=10)
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        self.assertEqual(effects, ['a'])
        plan = await session.run.resume_plan(paused.id)
        self.assertFalse(plan.can_resume)
        interaction, = await paused.ainteractions(pending_only=True)
        await paused.arespond(interaction.respond(interaction.find_approval().id))
        plan = await session.run.resume_plan(paused.id)
        self.assertTrue(plan.can_resume, plan.to_dict())
        resumed = await (await session.run.resume(paused.id)).wait(timeout=10)
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(resumed.data.agent_id, 'coordinator')
        self.assertEqual(effects, ['a', 'b'])
        self.assertFalse(any(step.kind == 'agent' for step in await resumed.steps.alist()))
        self.assertEqual(len(await session.run.alist()), 2)

    async def test_loop_approval_uses_agent_tool_scope_and_preserves_budget_on_resume(self):
        effects = []
        async def write(arguments):
            effects.append(arguments)
            return 'saved'
        tools = RuntimeTools(ToolRegistry((Tool('write', 'write', {'type': 'object'}, write,
             contract=ToolContract(approval_required=True)), Tool('unselected', 'unselected', {'type': 'object'}, write))))
        provider = ScriptedCompletion([chunk(calls=[call('{}', name='write')], finish='tool_calls')],
                                      [chunk('done', finish='stop')])
        project, session = await self.setup_app(engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}, components=[tools])
        await project.components.tools.aenable('write', 'unselected')
        await project.agents.acreate({'engine': 'loop', 'completion': {'model': 'test/agent'},
                                     'tools': ['write'], 'policy': {'max_tool_calls': 1, 'require_tool': True}}, identifier='writer')
        await session.agents.aattach('writer')
        paused = await (await session.run.submit('save', agent_id='writer')).wait(timeout=10)
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        self.assertEqual(effects, [])
        self.assertEqual([d['function']['name'] for d in provider.requests[0]['tools']], ['write'])
        interaction, = await paused.ainteractions(pending_only=True)
        self.assertEqual(interaction.source['run_id'], paused.id)
        await paused.arespond(interaction.respond('approve'))
        resumed = await (await session.run.resume(paused.id)).wait(timeout=10)
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(effects, [{}])

        self.assertEqual(resumed.data.agent_id, 'writer')
        self.assertEqual(resumed.data.metadata['agent_execution']['tool_policy']['max_calls'], 1)
        self.assertEqual((await resumed.aresponse()).content, 'done')
        with self.assertRaises(RunRequestError):
            await session.run.resume(paused.id)
        self.assertEqual(effects, [{}])

    async def test_prompt_skill_and_input_output_contracts_are_bound(self):
        provider = ScriptedCompletion([chunk('{"answer":42}', finish='stop')])
        project, session = await self.setup_app(engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))},
            components=[SkillComponent(), PromptComponent()])
        await project.components.skills.acreate({'instructions': 'Verify the answer.'}, identifier='review')
        await project.components.prompts.acreate({'messages': [{'role': 'system', 'content': 'Reference prompt.'}]}, identifier='guide')
        await project.agents.acreate({'engine': 'loop', 'completion': {'model': 'test/agent'}, 'tools': [],
            'resources': {'skills': ['review'], 'prompt': 'guide'}, 'system_prompt': 'Selected Agent.',
            'input_schema': {'type': 'string', 'minLength': 2}, 'output_format': 'json',
            'output_schema': {'type': 'object', 'required': ['text', 'data'],
                              'properties': {'data': {'type': 'object', 'required': ['answer']}}}}, identifier='writer')
        await session.agents.aattach('writer')
        invalid = await (await session.run.submit('x', agent_id='writer')).wait(timeout=10)
        self.assertEqual(invalid.data.status, 'failed')
        self.assertEqual(provider.requests, [])
        run = await (await session.run.submit('question', agent_id='writer')).wait(timeout=10)
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(run.data.metadata['output']['data'], {'answer': 42})
        prompt = provider.requests[0]['messages'][0]['content']
        for text in ('Reference prompt.', 'Selected Agent.', 'Verify the answer.'):
            self.assertIn(text, prompt)
        binding = run.data.metadata['agent_execution']
        self.assertEqual(binding['tools'], [])
        self.assertEqual(set(binding['resources']['skill_revisions']), {'review'})
        self.assertIsNotNone(binding['resources']['prompt_revision'])

    async def test_changed_paused_agent_blocks_approved_effect_and_keeps_checkpoint(self):
        effects = []
        async def write(arguments):
            effects.append(arguments)
        tools = RuntimeTools(ToolRegistry((Tool('write', 'write', {'type': 'object'}, write,
            contract=ToolContract(approval_required=True)),)))
        provider = ScriptedCompletion([chunk(calls=[call('{}', name='write')], finish='tool_calls')])
        project, session = await self.setup_app(engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}, components=[tools])
        await project.components.tools.aenable('write')
        await project.agents.acreate({'engine': 'loop', 'completion': {'model': 'test/agent'}, 'tools': ['write']}, identifier='writer')
        await session.agents.aattach('writer')
        paused = await (await session.run.submit('save', agent_id='writer')).wait(timeout=10)
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        interaction, = await paused.ainteractions(pending_only=True)
        await paused.arespond(interaction.respond('approve'))
        checkpoint = await paused.acheckpoint()
        selected = await project.agents.asnapshot('writer')
        await project.agents.arevise('writer', {**selected['definition'], 'description': 'revised'}, expected_revision=selected['revision'])
        self.assertFalse((await session.run.resume_plan(paused.id)).can_resume)
        with self.assertRaises(RunRequestError):
            await session.run.resume(paused.id)
        self.assertEqual(effects, [])
        self.assertEqual(await paused.acheckpoint(), checkpoint)
        self.assertEqual(len(await session.run.alist()), 1)

    async def test_queued_resume_rechecks_engine_configuration_before_approved_effect(self):
        entered, release, effects = asyncio.Event(), asyncio.Event(), []
        async def hold(context):
            entered.set()
            await release.wait()
            yield 'released'
        async def write(arguments):
            effects.append(arguments)
        tools = RuntimeTools(ToolRegistry((Tool('write', 'write', {'type': 'object'}, write,
            contract=ToolContract(approval_required=True)),)))
        provider = ScriptedCompletion([chunk(calls=[call('{}', name='write')], finish='tool_calls')])
        project, session = await self.setup_app(engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider)), 'hold': BaseEngine(action=hold)},
                                                 components=[tools])
        await project.components.tools.aenable('write')
        await project.agents.acreate({'engine': 'loop', 'completion': {'model': 'test/agent'}, 'tools': ['write']}, identifier='writer')
        await session.agents.aattach('writer')
        paused = await (await session.run.submit('save', agent_id='writer')).wait(timeout=10)
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        interaction, = await paused.ainteractions(pending_only=True)
        await paused.arespond(interaction.respond('approve'))
        held = await session.run.submit('hold', engine='hold')
        await asyncio.wait_for(entered.wait(), 10)
        try:
            resume = await session.run.resume(paused.id)
            configuration = project.data.config.to_dict()
            configuration.setdefault('parameters', {}).setdefault('engines', {})['loop'] = {'config': {'system_prompt': 'changed after admission'}}
            await project.asave(config=configuration)
        finally:
            release.set()
        self.assertEqual((await held.wait(timeout=10)).data.status, 'completed')
        failed = await resume.wait(timeout=10)
        self.assertEqual(failed.data.status, 'failed')
        self.assertIn('binding changed', failed.data.error)
        self.assertEqual(effects, [])
        self.assertEqual(len(provider.requests), 1)

    async def test_new_record_corruption_is_rejected_without_overwrite(self):
        project, session = await self.setup_app()
        await project.agents.acreate({'engine': 'echo'}, identifier='writer')
        await session.agents.aattach('writer')
        run = await (await session.run.submit('read', agent_id='writer')).wait(timeout=10)
        await session.run.shutdown()
        path = run.data.paths.root / 'run.json'
        original = path.read_bytes()
        data = json.loads(original)
        for mutation in ('version', 'missing_id', 'revision', 'engine'):
            with self.subTest(mutation=mutation):
                broken = json.loads(original)
                if mutation == 'version':
                    broken['storage_version'] = 999
                elif mutation == 'missing_id':
                    del broken['agent_id']
                elif mutation == 'revision':
                    broken['metadata']['agent_binding']['revision'] = 'wrong'
                else:
                    broken['engine'] = 'other'
                path.write_text(json.dumps(broken))
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    await session.run.aload(run.id)
                self.assertEqual(path.read_bytes(), before)
        path.write_bytes(original)
        self.assertEqual((await session.run.aload(run.id)).data.agent_id, data['agent_id'])

    async def test_clone_backup_restore_and_memory_mode_preserve_agent_contract(self):
        project, session = await self.setup_app()
        await project.agents.acreate({'engine': 'echo'}, identifier='writer')
        await session.agents.aattach('writer')
        run = await (await session.run.submit('saved', agent_id='writer')).wait(timeout=10)
        await session.run.shutdown()
        clone = await project.aclone()
        self.assertEqual(await clone.agents.asnapshot('writer'), await project.agents.asnapshot('writer'))
        cloned_session, = await clone.sessions.alist()
        self.assertEqual(await cloned_session.run.alist(), [])
        self.assertEqual(len(await cloned_session.aconversation()), 2)
        backup = await project.abackup(self.root / 'backup')
        async with LargeLanguageModel(self.root / 'restored', components=[], engines={'echo': FakeStreamingEngine()}) as other:
            restored = await other.projects.arestore_backup(backup)
            loaded = await (await restored.sessions.aload(session.id)).run.aload(run.id)
            self.assertEqual(loaded.data.agent_id, 'writer')
            self.assertEqual(loaded.data.metadata['agent_binding'], run.data.metadata['agent_binding'])
        memory = await self.app.projects.acreate(conversation_storage='memory', components=[])
        await memory.agents.acreate({'engine': 'echo'}, identifier='memory-agent')
        memory_session = await memory.sessions.acreate()
        await memory_session.agents.aattach('memory-agent')
        result = await (await memory_session.run.submit('ephemeral', agent_id='memory-agent')).wait(timeout=10)
        self.assertEqual(result.data.agent_id, 'memory-agent')
        self.assertEqual(result.data.status, 'completed', result.data.error)
        self.assertEqual(len(await memory_session.aconversation()), 2)
        self.assertFalse(memory_session.paths.conversation.exists())

    async def test_missing_agent_is_reported_by_recovery_and_backup(self):
        project, session = await self.setup_app()
        await project.agents.acreate({'engine': 'echo'}, identifier='writer')
        await session.agents.aattach('writer')
        await (await session.run.submit('history', agent_id='writer')).wait(timeout=10)
        await session.run.shutdown()
        (project.paths.agents / 'records/writer.json').unlink()
        plan = await project.arecovery()
        self.assertIn('missing_agent_reference', [issue.code for issue in plan.issues])
        with self.assertRaisesRegex(ValueError, 'missing Project Agent'):
            await project.abackup(self.root / 'invalid-backup')

    async def test_mcp_alias_scope_approval_resume_and_connection_cleanup(self):
        effects, connections = [], []
        async def read(arguments):
            effects.append(arguments)
            return 'remote evidence'
        @asynccontextmanager
        async def connector(definition):
            connections.append('open')
            try:
                yield ToolRegistry((Tool('read', 'read', {'type': 'object'}, read,
                    contract=ToolContract(approval_required=True)),))
            finally:
                connections.append('close')
        provider = ScriptedCompletion([chunk(calls=[call('{}', name='remote')], finish='tool_calls')],
                                      [chunk('verified', finish='stop')])
        project, session = await self.setup_app(engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))},
                                                 components=[MCPComponent(connector=connector)])
        await project.components.mcp.acreate({'transport': 'stdio', 'command': 'injected-test-connector'}, identifier='server')
        await project.agents.acreate({'engine': 'loop', 'completion': {'model': 'test/agent'}, 'tools': [],
            'resources': {'mcp': {'server': {'remote': 'read'}}}}, identifier='reader')
        await session.agents.aattach('reader')
        paused = await (await session.run.submit('read', agent_id='reader')).wait(timeout=10)
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        self.assertEqual(effects, [])
        self.assertEqual(connections, ['open', 'close'])
        interaction, = await paused.ainteractions(pending_only=True)
        await paused.arespond(interaction.respond('approve'))
        resumed = await (await session.run.resume(paused.id)).wait(timeout=10)
        self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
        self.assertEqual(effects, [{}])
        self.assertEqual(connections, ['open', 'close', 'open', 'close'])
        self.assertEqual(resumed.data.metadata['agent_execution']['tool_policy']['allowed_tools'], ['remote'])


class AgentAdmissionCrashTests(unittest.TestCase):
    def test_before_write_before_commit_and_after_durable_admission(self):
        program = r'''
import asyncio, json, os, sys
from pathlib import Path
from unittest.mock import patch
from llm.llm import LargeLanguageModel
from llm.core.models import MessageRole
from llm.services.history.conversation import ConversationStore
from tests.llm.support.fake_engine import FakeStreamingEngine
root, project_id, session_id, point = sys.argv[1:]
marker = Path(root).parent / 'reached.json'
def reached(identifier, code):
    marker.write_text(json.dumps({'point': point, 'message_id': identifier}))
    os._exit(code)
async def main():
    async with LargeLanguageModel(root, components=[], engines={'echo': FakeStreamingEngine()}) as app:
        session = await (await app.projects.aload(project_id)).sessions.aload(session_id)
        manager = app._manager(session._snapshot)
        original_create, original_put = ConversationStore.create, asyncio.Queue.put_nowait
        def create(store, role, content, *args, **kwargs):
            selected = role == MessageRole.USER and content == 'crash-proof'
            if selected and point == 'before':
                reached(None, 81)
            value = original_create(store, role, content, *args, **kwargs)
            if selected and point == 'written':
                reached(value.id, 82)
            return value
        def put(queue, value):
            if point == 'committed' and manager._active is not None and queue is manager._active.queue:
                reached(value, 83)
            return original_put(queue, value)
        with patch.object(ConversationStore, 'create', create), patch.object(asyncio.Queue, 'put_nowait', put):
            await session.run.submit('crash-proof', agent_id='writer')
        raise AssertionError('failpoint was not reached')
asyncio.run(main())
'''
        for point, code in (('before', 81), ('written', 82), ('committed', 83)):
            with self.subTest(point=point), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'workspace'
                async def seed():
                    async with LargeLanguageModel(root, components=[], engines={'echo': FakeStreamingEngine()}) as app:
                        project = await app.projects.acreate()
                        await project.agents.acreate({'engine': 'echo'}, identifier='writer')
                        session = await project.sessions.acreate()
                        await session.agents.aattach('writer')
                        return project.id, session.id, await project.agents.asnapshot('writer')
                project_id, session_id, definition = asyncio.run(seed())
                result = subprocess.run([sys.executable, '-c', program, str(root), project_id, session_id, point],
                    cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                marker = json.loads((Path(directory) / 'reached.json').read_text())
                self.assertEqual(marker['point'], point)
                async def recover():
                    engine = FakeStreamingEngine(chunks=('recovered',))
                    async with LargeLanguageModel(root, components=[], engines={'echo': engine}) as app:
                        project = await app.projects.aload(project_id)
                        self.assertEqual(await project.agents.asnapshot('writer'), definition)
                        session = await project.sessions.aload(session_id)
                        messages = await session.aconversation()
                        self.assertEqual(len(messages), 1 if point == 'committed' else 0)
                        self.assertEqual(await session.run.alist(), [])
                        await session.run.start()
                        await session.run.wait_idle()
                        runs = await session.run.alist()
                        self.assertEqual(len(runs), 1 if point == 'committed' else 0)
                        self.assertEqual(len(engine.contexts), len(runs))
                        if runs:
                            data = await runs[0].aget_data()
                            self.assertEqual(data.agent_id, 'writer')
                            self.assertEqual(data.input_message_id, marker['message_id'])
                            self.assertEqual(data.status, 'completed')
                            self.assertEqual(data.metadata['agent_binding']['revision'], definition['revision'])
                            self.assertEqual(len(await session.aconversation()), 2)
                    second_engine = FakeStreamingEngine(chunks=('must not replay',))
                    async with LargeLanguageModel(root, components=[], engines={'echo': second_engine}) as other:
                        session = await (await other.projects.aload(project_id)).sessions.aload(session_id)
                        await session.run.start()
                        await session.run.wait_idle()
                        self.assertEqual(second_engine.contexts, [])
                asyncio.run(recover())


if __name__ == '__main__':
    unittest.main()
