"""Root Agent admission, immutable evidence and actual cumulative Tool effects."""

import asyncio
from copy import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from llm.core.models import RunStatus
from llm.engines.base import BaseEngine
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel
from llm.providers.litellm import LiteLLMCompletionAdapter
from llm.components.tools import Tool, ToolRegistry
from tests.llm.support.runtime_tools import RuntimeTools, TEST_CONTRACT
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class ContextEngine(BaseEngine):
    def for_context(self, context):
        worker = copy(self)
        worker.request = context
        return worker

    async def run(self, context):
        yield json.dumps(self.request, sort_keys=True)


class AgentExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    async def setup_app(self, engine, *, components=()):
        self.app = LargeLanguageModel(self.root / 'workspace', engines={'worker': engine}, components=components)
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate(components=[c.name for c in components])
        self.session = await self.project.sessions.acreate()

    async def create(self, definition=None, identifier='selected'):
        await self.project.agents.acreate(definition or {'engine': 'worker'}, identifier=identifier)
        await self.session.asave(agent_ids=sorted([*self.session.data.agent_ids, identifier]))

    async def submit(self, identifier='selected', **kwargs):
        return (await (await self.session.run.submit('request', recipients=[identifier], **kwargs)).wait(timeout=15))[0]

    async def test_explicit_agent_binding_and_omission_are_persisted(self):
        async def action(context):
            yield 'done'
        await self.setup_app(BaseEngine(action=action))
        await self.create()
        before = await self.project.agents.aload('selected')
        run = await self.submit()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(run.data.agent_id, 'selected')
        self.assertEqual(run.data.metadata['agent_binding']['definition'], before)
        self.assertNotIn('request_context', run.data.metadata)
        self.assertNotIn('engine_options', run.data.metadata)
        self.assertEqual(run.data.metadata['agent_execution']['tool_policy']['allowed_tools'], [])
        self.assertEqual(await self.project.agents.aload('selected'), before)
        with self.assertRaises(TypeError):
            await self.session.run.submit('rejected', engine='worker')
        snapshot = await self.project.agents.asnapshot('selected')
        with self.assertRaisesRegex(ValueError, 'referenced by a Run'):
            await self.project.agents.adelete('selected', expected_revision=snapshot['revision'])

    async def test_request_context_is_separate_and_exact(self):
        await self.setup_app(ContextEngine())
        await self.create()
        value = {'cwd': '/tmp', 'env': {}, 'explicit_null': None, 'turn': {'source': 'user'}}
        run = await self.submit(request_context=value)
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual(run.data.metadata['request_context'], value)
        self.assertEqual(await self.project.agents.aload('selected'), {'engine': 'worker'})
        with self.assertRaises(TypeError):
            await self.session.run.submit('bad', recipients=['selected'], request_context=None)

    async def test_factory_and_context_cannot_drop_behavioral_limits_by_changing_mode(self):
        from llm.core.agents import AgentMode
        class ChangesMode(BaseEngine):
            def for_agent(self, definition):
                worker = super().for_agent(definition)
                worker.agent_mode = AgentMode.WORKFLOW
                return worker
        await self.setup_app(ChangesMode())
        with self.assertRaisesRegex(ValueError, 'preserve Engine Agent mode'):
            await self.create({'engine': 'worker', 'policy': {'max_tool_calls': 1}})
        self.assertEqual(await self.project.agents.alist(), {})

    async def test_context_cannot_change_mode_or_ignore_agent_limits(self):
        from llm.core.agents import AgentMode
        class ChangesMode(ContextEngine):
            def for_context(self, context):
                worker = super().for_context(context)
                worker.agent_mode = AgentMode.WORKFLOW
                return worker
        await self.setup_app(ChangesMode())
        await self.create({'engine': 'worker', 'policy': {'max_tool_calls': 1}})
        with self.assertRaisesRegex(ValueError, 'preserve Engine execution contracts'):
            await self.submit(request_context={})
        self.assertEqual(await self.session.run.alist(), [])

    async def test_invalid_selection_has_no_execution_or_implicit_agent(self):
        calls = []
        async def action(context):
            calls.append(context.run.id)
            yield 'done'
        await self.setup_app(BaseEngine(action=action))
        with self.assertRaises((ValueError, FileNotFoundError)):
            await self.session.run.submit('request', recipients=['missing'])
        self.assertEqual(calls, [])
        self.assertEqual(await self.project.agents.alist(), {})

    async def test_queued_definition_change_blocks_new_effect_and_protects_reference(self):
        started, release = asyncio.Event(), asyncio.Event()
        effects = self.root / 'effects.txt'
        async def action(context):
            if context.messages[-1].content == 'block':
                started.set()
                await release.wait()
            else:
                effects.write_text('effect')
            yield 'done'
        await self.setup_app(BaseEngine(action=action))
        await self.create()
        first = await self.session.run.submit('block', recipients=['selected'])
        await asyncio.wait_for(started.wait(), 10)
        queued = await self.session.run.submit('request', recipients=['selected'])
        original = await self.project.agents.asnapshot('selected')
        with self.assertRaisesRegex(ValueError, 'queued request'):
            await self.project.agents.adelete('selected', expected_revision=original['revision'])
        await self.project.agents.arevise('selected', {'engine': 'worker', 'purpose': 'changed'},
                                         expected_revision=original['revision'])
        release.set()
        await first.wait(timeout=15)
        run = (await queued.wait(timeout=15))[0]
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertIn('changed since request admission', run.data.error)
        self.assertFalse(effects.exists())
        self.assertEqual(run.data.metadata['agent_binding']['definition'], original['definition'])

    async def setup_effect(self, model, *, contract=TEST_CONTRACT):
        self.effect_file = self.root / 'effects.txt'
        async def effect(arguments):
            with self.effect_file.open('a') as output:
                output.write(arguments['value'] + '\n')
            return {'written': arguments['value']}
        tools = RuntimeTools(ToolRegistry([Tool('effect', 'effect', {
            'type': 'object', 'properties': {'value': {'type': 'string'}},
            'required': ['value'], 'additionalProperties': False}, effect, contract=contract)]))
        await self.setup_app(LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model)),
                             components=[tools])
        await (await self.project.components.aget('tools')).aenable('effect')
        await self.create({'engine': 'worker', 'completion': {'model': 'test/model'},
                           'tools': ['effect'], 'policy': {'max_tool_calls': 1}})

    @staticmethod
    def tool(value, identifier):
        return [chunk(calls=[call(json.dumps({'value': value}), name='effect', call_id=identifier)]),
                chunk(finish='tool_calls')]

    async def test_root_one_call_limit_blocks_second_real_effect(self):
        model = ScriptedCompletion(self.tool('first', 'one'), self.tool('second', 'two'))
        await self.setup_effect(model)
        run = await self.submit()
        self.assertEqual(self.effect_file.read_text().splitlines(), ['first'],
                         'new Tool effect escaped the root Agent budget')
        self.assertEqual(run.data.status, RunStatus.FAILED, run.data.error)
        self.assertEqual(len(model.requests), 2)

    async def test_changed_agent_model_rejects_resume_without_replaying_effect(self):
        model = ScriptedCompletion(self.tool('first', 'one'), [RuntimeError('interrupted')])
        await self.setup_effect(model)
        run = await self.submit()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(self.effect_file.read_text().splitlines(), ['first'])
        self.assertTrue((await self.session.run.resume_plan(run.id, agent_id='selected')).can_resume)
        snapshot = await self.project.agents.asnapshot('selected')
        await self.project.agents.aupdate('selected', {'completion': {'model': 'test/changed'}},
                                         expected_revision=snapshot['revision'])
        plan = await self.session.run.resume_plan(run.id, agent_id='selected')
        self.assertFalse(plan.can_resume)
        with self.assertRaises(ValueError):
            await self.session.run.resume(run.id, agent_id='selected')
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(self.effect_file.read_text().splitlines(), ['first'])

    async def test_model_change_invalidates_pending_tool_approval(self):
        model = ScriptedCompletion(self.tool('first', 'one'))
        await self.setup_effect(model, contract=replace(TEST_CONTRACT, approval_required=True))
        run = await self.submit()
        self.assertEqual(run.data.status, RunStatus.PAUSED, run.data.error)
        request, = await run.ainteractions()
        await run.arespond(request.respond('approve'))
        self.assertFalse(self.effect_file.exists())
        self.assertTrue((await self.session.run.resume_plan(run.id, agent_id='selected')).can_resume)
        snapshot = await self.project.agents.asnapshot('selected')
        await self.project.agents.aupdate('selected', {'completion': {'model': 'test/changed'}},
                                         expected_revision=snapshot['revision'])
        self.assertFalse((await self.session.run.resume_plan(run.id, agent_id='selected')).can_resume)
        with self.assertRaises(ValueError):
            await self.session.run.resume(run.id, agent_id='selected')
        self.assertFalse(self.effect_file.exists())
        self.assertEqual(len(model.requests), 1)

    async def test_resume_preserves_root_budget_and_missing_binding_is_not_repaired(self):
        model = ScriptedCompletion(self.tool('first', 'one'), [RuntimeError('injected interruption')],
                                   self.tool('second', 'two'))
        await self.setup_effect(model)
        run = await self.submit()
        self.assertEqual(run.data.status, RunStatus.FAILED, run.data.error)
        self.assertEqual(self.effect_file.read_text().splitlines(), ['first'])
        plan = await self.session.run.resume_plan(run.id, agent_id='selected')
        self.assertTrue(plan.can_resume, plan.blockers)
        resumed = (await (await self.session.run.resume(run.id, agent_id='selected')).wait(timeout=15))[0]
        self.assertEqual(self.effect_file.read_text().splitlines(), ['first'],
                         'new Tool effect escaped the resumed root Agent budget')
        self.assertEqual(resumed.data.status, RunStatus.FAILED, resumed.data.error)
        path = resumed.data.paths.root / 'run.json'
        value = json.loads(path.read_text())
        del value['metadata']['agent_execution']
        path.write_text(json.dumps(value))
        before = path.read_bytes()
        plan = await self.session.run.resume_plan(resumed.id, agent_id='selected')
        self.assertFalse(plan.can_resume)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.effect_file.read_text().splitlines(), ['first'])
