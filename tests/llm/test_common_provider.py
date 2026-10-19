"""Execute a non-OpenAI transport through real Loop, Tool, Processor and Memory paths."""
import asyncio
from contextlib import aclosing
from pathlib import Path
import tempfile
import unittest

from llm.components.base import Component
from llm.components.memory import MemoryComponent
from llm.components.processing import CompletionMessage, CompletionSession
from llm.components.tools import Tool, ToolRegistry
from llm.core.model_protocol import ModelMessage, ModelRequest, StopReason
from llm.engines.base import BaseEngine
from llm.llm import LargeLanguageModel, LoopEngine
from tests.llm.configuration_fixtures import memory_settings
from tests.llm.support.common_provider import CommonProvider
from tests.llm.support.runtime_tools import RuntimeTools


class CommonProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_loop_tool_processor_memory_and_opaque_state(self):
        observed=[]
        class Processing(Component):
            name=directory='processing'
            capabilities=('completion_processors',)
            def resolve(self,project,capability):
                class Processor:
                    name='inspect'; priority=1; close_timeout=None
                    def session(self,context):
                        class Session(CompletionSession):
                            async def prepare(self,request):
                                request.messages.insert(0,CompletionMessage(ModelMessage('system','common instruction')))
                                if False: yield
                            async def after_completion(self,observation):
                                observed.append(observation.snapshot())
                                if False: yield
                        return Session()
                return Processor()
        class TypedLoop(LoopEngine):
            async def stream_completion(self, request, **options):
                assert isinstance(request, ModelRequest)  # No protocol marker or wire projection.
                async with aclosing(super().stream_completion(request, **options)) as stream:
                    async for event in stream:
                        yield event
        effects=[]
        async def lookup(arguments):
            effects.append(arguments)
            return 'x'*200
        provider=CommonProvider(
            [{'sealed':{'invoke':[{'identity':'fake-call','operation':'lookup','input':{'q':'question'}}],
                        'signature':'first-signature'}}],
            [{'fragment':'finished'},{'sealed':{'say':'finished','spent':{'total_tokens':7}}}],
        )
        tools=RuntimeTools(ToolRegistry((Tool('lookup','Lookup',{'type':'object','properties':{'q':{'type':'string'}},
            'required':['q'],'additionalProperties':False},lookup),)))
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root,components=[tools,MemoryComponent(),Processing()],
                    engines={'loop':TypedLoop(completion_adapter=provider)}) as app:
                project=await app.projects.acreate('common',components=['tools','memory','processing'],config={
                    'parameters':{'engines':{'loop':{'config':{'completion':{'model':'fake'}}}}}})
                project.components.tools.enable('lookup')
                memory=await project.components.aget('memory')
                await memory.aconfigure(memory_settings({'processing':{'priority':100,
                    'compress_tools':True,'tool_result_chars':20}}))
                session=await project.sessions.acreate()
                run=await (await session.run.submit('question',engine='loop')).wait(timeout=10)
                self.assertEqual(run.data.status,'completed',run.data.error)
                self.assertEqual((await run.aresponse()).content,'finished')
                self.assertEqual(effects,[{'q':'question'}])
                self.assertEqual(len(provider.requests),2)
                sent=provider.requests[1]
                self.assertEqual(sent.tools[0].name,'lookup')
                assistant=next(m for m in sent.messages if m.calls)
                self.assertEqual(assistant.opaque,{'fake':{'signature':'first-signature'}})
                result=next(m.result for m in sent.messages if m.result)
                self.assertEqual(result.call_id,'fake-call')
                self.assertIn('"truncated": true',result.content)
                tool_steps=[s for s in await run.steps.alist() if s.kind=='tool']
                self.assertEqual(tool_steps[0].output.data,'x'*200)
                checkpoint=await run.acheckpoint('loop')
                self.assertEqual(checkpoint['records']['iteration:1']['response']['message']['opaque'],assistant.opaque)
                self.assertIsNone(observed[0].answer.usage)
                self.assertEqual(observed[1].answer.stop,StopReason.COMPLETE)

    async def test_first_chunk_boundary_controls_retry_and_preserves_partial_output(self):
        for received in (False,True):
            with self.subTest(received=received):
                first=([{'fragment':'partial'}] if received else [])+[ConnectionError('offline')]
                provider=CommonProvider(first,[{'fragment':'complete'},{'sealed':{'say':'complete'}}])
                engine=BaseEngine(completion_adapter=provider)
                capture=[]; text=[]
                try:
                    async with aclosing(engine.stream_completion(ModelRequest([ModelMessage('user','task')]),
                        response=capture,provider={'max_attempts':2,'delay_seconds':0},include_events=False)) as stream:
                        async for delta in stream:
                            text.append(delta)
                except ConnectionError:
                    self.assertTrue(received)
                self.assertEqual(len(provider.requests),1 if received else 2)
                self.assertEqual(text,['partial'] if received else ['complete'])
                self.assertEqual(len(capture),0 if received else 1)
                self.assertEqual(provider.closed,len(provider.requests))

    async def test_cancel_after_received_envelope_closes_without_replay(self):
        entered=asyncio.Event(); release=asyncio.Event()
        class Blocked(CommonProvider):
            async def stream(self,request,*,buffer_size):
                self.requests.append(request)
                try:
                    yield {'fragment':'partial'}
                    entered.set()
                    await release.wait()
                finally:
                    self.closed+=1
        provider=Blocked(); engine=BaseEngine(completion_adapter=provider)
        capture=[]
        async def consume():
            async for _ in engine.stream_completion(ModelRequest(),response=capture,provider={'max_attempts':3}):
                pass
        task=asyncio.create_task(consume())
        await asyncio.wait_for(entered.wait(),5)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual((len(provider.requests),provider.closed,capture),(1,1,[]))

    async def test_common_counter_unknown_usage_and_admission_release(self):
        from llm.services.composition import BackendServices
        from llm.providers.calls import ProviderCalls
        seen = []
        def count(request):
            self.assertIsInstance(request, ModelRequest)
            seen.append(request)
            return 3
        provider = CommonProvider([{'sealed': {'say': 'done'}}])
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, engines={'loop': LoopEngine(completion_adapter=provider)},
                    services=BackendServices(token_counters={'common': count})) as app:
                project = await app.projects.acreate(config={'policies': {
                    'usage': {'max_tokens': 10, 'counter': 'common'}}, 'parameters': {'engines': {'loop': {
                        'config': {'completion': {'model': 'fake'}},
                        'policy': {'completion': {'max_tokens': 10, 'counter': 'common'}}}}}})
                session = await project.sessions.acreate()
                run = await (await session.run.submit('task', engine='loop')).wait(timeout=10)
                self.assertEqual(run.data.status, 'completed', run.data.error)
                result = await run.aresult()
                self.assertIsNone(result.total_tokens)
                self.assertEqual(result.completions[0].reserved_tokens, 3)
                self.assertFalse(result.completions[0].usage_complete)
                self.assertTrue(seen)
        calls = ProviderCalls()
        with calls.scope(), self.assertRaises(ConnectionError):
            async for _ in BaseEngine(completion_adapter=CommonProvider([ConnectionError()])).stream_completion(ModelRequest()):
                pass
        self.assertEqual(calls.stats, {"active": 0, "waiting": 0})

    async def test_common_checkpoints_reopen_after_effect_and_pending_approval(self):
        from llm.components.tools import ToolContract
        for approved in (False, True):
            with self.subTest(approval=approved), tempfile.TemporaryDirectory() as root:
                effects = []
                async def act(arguments):
                    effects.append(arguments)
                    return 'effect'
                registry = ToolRegistry((Tool('act', 'Action', {'type': 'object'}, act,
                    contract=ToolContract(approval_required=approved)),))
                first = CommonProvider([{'sealed': {'invoke': [
                    {'identity': 'call', 'operation': 'act', 'input': {}}], 'signature': 'resume-seal'}}],
                    [ConnectionError('after effect')])
                async with LargeLanguageModel(root, components=[RuntimeTools(registry)],
                        engines={'loop': LoopEngine(completion_adapter=first)}) as app:
                    project = await app.projects.acreate(components=['tools'], config={
                        'parameters': {'engines': {'loop': {'config': {'completion': {'model': 'fake'}}}}}})
                    project.components.tools.enable('act')
                    session = await project.sessions.acreate()
                    old = await (await session.run.submit('task', engine='loop')).wait(timeout=10)
                    self.assertEqual(old.data.status, 'paused' if approved else 'failed', old.data.error)
                    self.assertEqual(len(effects), 0 if approved else 1)
                    ids = project.id, session.id, old.id
                second = CommonProvider([{'sealed': {'say': 'done'}}])
                async with LargeLanguageModel(root, components=[RuntimeTools(registry)],
                        engines={'loop': LoopEngine(completion_adapter=second)}) as app:
                    session = await (await app.projects.aload(ids[0])).sessions.aload(ids[1])
                    prior = await session.run.aload(ids[2])
                    if approved:
                        interaction, = await prior.ainteractions()
                        await prior.arespond(interaction.respond('approve'))
                    resumed = await (await session.run.resume(prior.id, engine='loop')).wait(timeout=10)
                    self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
                    self.assertEqual(effects, [{}])
                    self.assertEqual(len(second.requests), 1)
                    call = next(m for m in second.requests[0].messages if m.calls)
                    self.assertEqual(call.opaque['fake']['signature'], 'resume-seal')

    async def test_memory_auxiliary_model_uses_common_protocol_and_rejects_tools(self):
        from llm.components.memory.processing import MemoryProcessor
        for tool_response in (False, True):
            with self.subTest(tool=tool_response):
                body = ({'invoke': [{'identity': 'nested', 'operation': 'forbidden', 'input': {}}]}
                        if tool_response else {'say': '{"summary":"common summary"}'})
                provider = CommonProvider([{'sealed': body}])
                session = MemoryProcessor(None, completion_adapter=provider).session(None)
                session.config = {'completion': {'model': 'fake'}}
                result = {}
                async def consume():
                    async for _ in session._model('Summarize', {'messages': []}, result):
                        pass
                if tool_response:
                    with self.assertRaisesRegex(ValueError, 'cannot execute tools'):
                        await consume()
                    self.assertEqual(result, {})
                else:
                    await consume()
                    self.assertEqual(result, {'summary': 'common summary'})
                self.assertEqual(provider.requests[0].tools, ())
                self.assertEqual(provider.requests[0].messages[0].text, 'Summarize')
