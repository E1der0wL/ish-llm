"""Post-response records never delay answers or grant authority for external work."""

import asyncio
from copy import deepcopy
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from hub.backend.bootstrap import create_backend
from hub.backend.execution.environment import underlying_engine
from hub.backend.execution.retries import HubLoopEngine
from hub.backend.project.postprocessing_values import Postprocessing as P
from hub.backend.runtime import HubConfig, HubRuntime
from llm.providers.litellm import LiteLLMCompletionAdapter
from tests.llm.test_loop import ScriptedCompletion, call, chunk
from tests.hub.test_live import until


EMPTY = {'memories': [], 'goals': [], 'refinement': None}


class BackgroundRecordTests(unittest.IsolatedAsyncioTestCase):
    async def runtime(self, foreground, background, *, enabled=True):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        def factory(config):
            backend = create_backend(config)
            if enabled:
                underlying_engine(backend.engines.get(P.ENGINE)).completion_adapter = LiteLLMCompletionAdapter(completion_fn=background)
            return backend
        runtime = HubRuntime(HubConfig(root.name, model='test/records', auto_title=False,
            postprocess=enabled, engine_factories={'loop': lambda: HubLoopEngine(
                completion_adapter=LiteLLMCompletionAdapter(completion_fn=foreground))}), factory)
        self.addAsyncCleanup(runtime.close)
        await runtime.start()
        sid = await runtime.new_session()
        return runtime, runtime.sessions[sid]

    async def submit(self, session, text='Explain this useful result.'):
        run, = await asyncio.wait_for((await session.run.submit(text, recipients=['assistant'])).wait(), 20)
        result = await run.aget_data()
        self.assertEqual(str(result.status), 'completed', result.error)
        return run

    async def test_answer_finishes_before_background_and_public_records_keep_cas(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        requests = []
        def background(**request):
            requests.append(request)
            started.set()
            release.wait(15)
            yield chunk(json.dumps({'memories':[{'action':'create','id':None,'data':{'content':'Prefer short explanations.'}}],
                'goals':[{'action':'update','id':'work','data':{'progress':['Explained result'], 'next_actions':['Verify change']}}],
                'refinement':None}), finish='stop')
        runtime, session = await self.runtime(ScriptedCompletion([chunk('Useful answer.',finish='stop')]), background)
        goals = runtime.project.components.goals
        await goals.acreate({'title':'Review','objective':'Understand result',
            'scope':{'type':'session','id':session.id},'status':'active',
            'success_criteria':['Result explained'],'progress':[], 'next_actions':['Explain result']}, identifier='work')
        run = await self.submit(session)
        self.assertTrue(await asyncio.to_thread(started.wait, 10))
        self.assertEqual((await run.aresponse()).content, 'Useful answer.')
        self.assertEqual(len(await session.run.alist()), 1)
        self.assertEqual(len(await session.aconversation()), 2)
        self.assertFalse(await runtime.project.components.memory.alist())
        self.assertFalse(requests[0].get('tools'))
        self.assertNotIn('max_tokens', requests[0])
        release.set()
        await until(lambda: not runtime.background_records.tasks)
        memories = await runtime.project.components.memory.alist(session_id=session.id)
        self.assertEqual(len(memories), 1)
        memory, = memories.values()
        self.assertEqual(memory['status'], 'candidate')
        self.assertEqual(memory['scope'], 'session')
        self.assertEqual((await goals.aload('work'))['progress'], ['Explained result'])
        self.assertEqual(len(await session.run.alist()), 1)
        self.assertTrue(all(n.session_id == session.id for n in runtime.notifications.items()))
        runtime.background_records.observe(await run.aget_data())
        self.assertFalse(runtime.background_records.tasks)

    async def test_new_request_cancels_pending_bookkeeping_and_greetings_skip_it(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def background(**request):
            started.set()
            release.wait(15)
            yield chunk(json.dumps(EMPTY),finish='stop')
        provider = ScriptedCompletion([chunk('Useful answer.',finish='stop')], [chunk('Hello!',finish='stop')])
        runtime, session = await self.runtime(provider, background)
        await self.submit(session)
        self.assertTrue(await asyncio.to_thread(started.wait, 10))
        await self.submit(session, '안녕?')
        release.set()
        await until(lambda: not runtime.background_records.tasks)
        self.assertFalse(await runtime.project.components.memory.alist())

    async def test_background_failure_is_logged_without_changing_success(self):
        runtime, session = await self.runtime(ScriptedCompletion([chunk('Useful answer.',finish='stop')]),
            ScriptedCompletion([chunk('not JSON',finish='stop')]))
        with patch.object(runtime.background_records, '_log', wraps=runtime.background_records._log) as log:
            run = await self.submit(session)
            await until(lambda: bool(log.call_args_list))
            await until(lambda: not runtime.background_records.tasks)
            self.assertEqual(log.call_args.args[2], 'hub.background.failed')
            self.assertEqual(str((await run.aget_data()).status), 'completed')
            self.assertEqual((await run.aresponse()).content, 'Useful answer.')

    async def test_memory_tools_create_update_delete_without_approval(self):
        provider = ScriptedCompletion(
            [chunk(calls=[call(json.dumps({'content':'A useful fact','scope':'project'}),name='memory_create',call_id='m')],finish='tool_calls')],
            [chunk('Candidate saved.', finish='stop')])
        runtime, session = await self.runtime(provider, None, enabled=False)
        await self.submit(session, 'Remember this useful fact.')
        memory, = (await runtime.project.components.memory.alist(session_id=session.id)).values()
        self.assertEqual(memory['status'], 'candidate')
        provider.responses.extend([
            [chunk(calls=[call(json.dumps({'identifier':memory['id'], 'changes':{'content':'An updated fact'},
                'expected_revision':memory['revision'],'expected_generation':memory['generation']}),
                name='memory_update',call_id='update')],finish='tool_calls')],
            [chunk('Memory updated.', finish='stop')]])
        await self.submit(session, 'Update that memory.')
        memory = await runtime.project.components.memory.aload(memory['id'])
        self.assertEqual(memory['content'], 'An updated fact')
        provider.responses.extend([[chunk(calls=[call(json.dumps({'identifier':memory['id'],
            'expected_revision':memory['revision'],'expected_generation':memory['generation']}),
            name='memory_delete',call_id='delete')],finish='tool_calls')],
            [chunk('Memory deleted.', finish='stop')]])
        run = await self.submit(session, 'Delete that memory.')
        self.assertFalse(await run.ainteractions(pending_only=True))
        self.assertFalse(await runtime.project.components.memory.alist())

    async def test_plan_validates_all_targets_before_writing_and_rejects_stale_goal(self):
        runtime, session = await self.runtime(ScriptedCompletion([chunk('Useful answer.',finish='stop')]), None, enabled=False)
        run = await self.submit(session)
        source = await run.aget_data()
        goals = runtime.project.components.goals
        await goals.acreate({'title':'Review','objective':'Understand result','scope':{'type':'session','id':session.id},
            'status':'active','success_criteria':['Explained'],'progress':[],'next_actions':[]},identifier='work')
        payload, owners, _ = await runtime.background_records._inputs(runtime.project,session,source)
        invalid = {**deepcopy(EMPTY), 'memories':[{'action':'create','id':None,'data':{'content':'Should not save'}}],
                   'goals':[{'action':'update','id':'foreign','data':{'progress':[],'next_actions':[]}}]}
        with self.assertRaisesRegex(ValueError, 'outside'):
            await runtime.background_records._apply(source,session,payload,owners,invalid)
        self.assertFalse(await runtime.project.components.memory.alist())
        await goals.aupdate('work',{'progress':['User changed']},expected_version=payload['goals']['work']['version'])
        with self.assertRaises(Exception):
            await runtime.background_records._apply(source,session,payload,owners,
                {**deepcopy(EMPTY),'goals':[{'action':'update','id':'work','data':{'progress':['Stale'], 'next_actions':[]}}]})
        self.assertEqual((await goals.aload('work'))['progress'], ['User changed'])

    async def test_background_internal_crud_includes_confirmed_memory_and_goal_deletion(self):
        runtime, session = await self.runtime(ScriptedCompletion([chunk('Useful answer.',finish='stop')]), None, enabled=False)
        source = await (await self.submit(session)).aget_data()
        records = runtime.background_records
        payload, owners, _ = await records._inputs(runtime.project, session, source)
        await records._apply(source, session, payload, owners, {**deepcopy(EMPTY),
            'memories':[{'action':'create','id':None,'data':{'content':'A fact'}}],
            'goals':[{'action':'create','id':None,'data':{'title':'Requested work','objective':'Verify requested result'}}]})
        memory, = (await runtime.project.components.memory.alist(session_id=session.id)).values()
        goal_id, = await runtime.project.components.goals.alist()
        await runtime.project.components.memory.aupdate(memory['id'], {'status':'confirmed'}, session_id=session.id,
            expected_generation=memory['generation'], expected_revision=memory['revision'])
        payload, owners, _ = await records._inputs(runtime.project, session, source)
        self.assertEqual(payload['memories'][memory['id']]['status'], 'confirmed')
        await records._apply(source, session, payload, owners, {**deepcopy(EMPTY),
            'memories':[{'action':'update','id':memory['id'],'data':{'content':'Corrected fact'}}],
            'goals':[{'action':'update','id':goal_id,'data':{'status':'completed','progress':['Verified result']}}]})
        self.assertEqual((await runtime.project.components.memory.aload(memory['id'],session_id=session.id))['content'], 'Corrected fact')
        self.assertEqual((await runtime.project.components.goals.aload(goal_id))['status'], 'completed')
        payload, owners, _ = await records._inputs(runtime.project, session, source)
        await records._apply(source, session, payload, owners, {**deepcopy(EMPTY),
            'memories':[{'action':'delete','id':memory['id'],'data':None}],
            'goals':[{'action':'delete','id':goal_id,'data':None}]})
        self.assertFalse(await runtime.project.components.memory.alist(session_id=session.id))
        self.assertFalse(await runtime.project.components.goals.alist())

    async def test_stale_memory_delete_cannot_remove_newer_content(self):
        runtime, session = await self.runtime(ScriptedCompletion([chunk('Useful answer.',finish='stop')]), None, enabled=False)
        source = await (await self.submit(session)).aget_data()
        memory = runtime.project.components.memory
        identifier = await memory.acreate({'content':'Original','scope':'session','session_id':session.id})
        created = await memory.aload(identifier,session_id=session.id)
        payload, owners, _ = await runtime.background_records._inputs(runtime.project, session, source)
        await memory.aupdate(created['id'], {'content':'Newer'},session_id=session.id,expected_generation=created['generation'],expected_revision=created['revision'])
        with self.assertRaises(Exception):
            await runtime.background_records._apply(source, session, payload, owners,
                {**deepcopy(EMPTY),'memories':[{'action':'delete','id':created['id'],'data':None}]})
        self.assertEqual((await memory.aload(created['id'],session_id=session.id))['content'], 'Newer')

    async def test_refinement_remains_a_proposal_and_does_not_send_connections(self):
        runtime, session = await self.runtime(ScriptedCompletion([chunk('Useful answer.',finish='stop')]), None, enabled=False)
        definition = await runtime.project.agents.asnapshot('assistant')
        definition['definition']['completion']['api_key'] = 'test-private-key'
        await runtime.project.agents.arevise('assistant',definition['definition'],expected_revision=definition['revision'])
        run = await self.submit(session)
        source = await run.aget_data()
        payload, owners, _ = await runtime.background_records._inputs(runtime.project,session,source)
        self.assertNotIn('test-private-key',json.dumps(payload))
        self.assertNotIn('completion',payload['refinement_target']['guidance'])
        plan = {**deepcopy(EMPTY),'refinement':{'reason':'Use concise explanations.', 'patch':{'description':'Concise helper'}}}
        await runtime.background_records._apply(source,session,payload,owners,plan)
        proposals = await runtime.project.components.refinement.alist()
        self.assertEqual(len(proposals),1)
        proposal, = proposals.values()
        self.assertEqual(proposal['status'],'proposed')
        self.assertNotEqual((await runtime.project.agents.aload('assistant')).get('description'),'Concise helper')
        with self.assertRaisesRegex(ValueError,'guidance'):
            await runtime.background_records._apply(source,session,payload,owners,
                {**deepcopy(EMPTY),'refinement':{'reason':'Wrong scope','patch':{'tools':[]}}})

    async def test_goal_creation_has_no_approval_and_preserves_owning_session(self):
        provider = ScriptedCompletion([chunk(calls=[call(json.dumps({'identifier':'work','title':'Review result','objective':'Verify requested work',
            'success_criteria':['Verified']}),name='goal_create',call_id='g')],finish='tool_calls')],
            [chunk('Goal recorded.',finish='stop')])
        runtime, session = await self.runtime(provider,None,enabled=False)
        await self.submit(session,'Create a goal for this task.')
        goal, = (await runtime.project.components.goals.alist(session_id=session.id)).values()
        self.assertEqual(goal['scope'], {'type':'session','id':session.id})
