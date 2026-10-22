"""An independent wire protocol exercises real Loop storage, effects and quotas."""

import asyncio
from contextlib import aclosing
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from llm.core.model_protocol import (CompletionUpdate, ModelMessage, ModelRequest,
    ModelResponse, StopReason, ToolCall, copy_model)
from llm.components.processing import CompletionSession
from llm.components.tools import Tool, ToolRegistry, ToolContract, ToolEffect, ToolIsolation
from llm.engines.base import BaseEngine, EngineEvent, EngineEventType
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel
from llm.providers.calls import ProviderCalls, ProviderLimits, current_calls
from llm.providers.observations import ModelCall, provider_attempt
from llm.providers.streaming import CompletionError
from tests.llm.support.runtime_tools import RuntimeTools
from tests.llm.test_completion_processing import Feature, Processor


class IndependentDecoder:
    """Test wire keys deliberately differ from the LiteLLM/OpenAI protocol."""

    def __init__(self, *, max_tool_calls=None, max_argument_chars=None, max_output_chars=None):
        self.limits = max_tool_calls, max_argument_chars, max_output_chars
        self.text, self.calls, self.opaque = '', [], {}
        self.end, self.tally = None, None

    def decode(self, frame):
        if self.end is not None:
            raise CompletionError('Frame after termination')
        self.text += frame.get('words', '')
        self.opaque.update(frame.get('seal', {}))
        for proposal in frame.get('invoke', []):
            self.calls.append(ToolCall(proposal['key'], proposal['action'], proposal['values'], proposal.get('seal', {})))
        count, arguments, output = self.limits
        if count is not None and len(self.calls) > count:
            raise CompletionError('Too many calls', code='completion_tool_limit')
        if arguments is not None and any(len(json.dumps(c.arguments)) > arguments for c in self.calls):
            raise CompletionError('Arguments too large', code='completion_argument_limit')
        if output is not None and len(self.text) > output:
            raise CompletionError('Output too large', code='completion_output_limit')
        self.end, self.tally = frame.get('end'), frame.get('tally')
        if frame.get('words'):
            yield CompletionUpdate(text=frame['words'])

    def finish(self):
        if self.end not in ('done', 'invoke') or (self.end == 'invoke') != bool(self.calls):
            raise CompletionError('Incomplete independent response')
        return ModelResponse(ModelMessage('assistant', self.text, tuple(self.calls), opaque=self.opaque),
            StopReason.TOOLS if self.calls else StopReason.COMPLETE, self.tally,
            response_id='independent-response', model='independent-model', raw_finish_reason=self.end)


class IndependentAdapter:
    execution_binding = {'revision': 'independent-v1'}

    def __init__(self, *scripts):
        self.scripts, self.requests, self.closed = list(scripts), [], 0
        self.entered = asyncio.Event()

    def describe_config(self):
        return {'type': 'object', 'properties': {
            'model': {'type': 'string'}, 'output_cap': {'type': 'integer', 'minimum': 1},
            'wait': {'type': 'number', 'minimum': 0}}, 'additionalProperties': False}

    def validate_request(self, parameters, *, require_model=False):
        from jsonschema import validate
        validate(parameters, self.describe_config())
        if require_model and not parameters.get('model'):
            raise ValueError('Explicit independent model required')

    def validate_input_ownership(self, parameters, *, auxiliary=False):
        self.validate_request(parameters)

    def auxiliary_parameters(self, parameters, *, preserve_retries=False, preserve_output_limits=True):
        result = copy_model(parameters)
        if not preserve_output_limits:
            result.pop('output_cap', None)
        return result

    def prepare(self, request):
        if not isinstance(request, ModelRequest):
            raise TypeError('Independent adapter requires ModelRequest')
        self.validate_request(request.parameters, require_model=True)
        request.validate_exchanges()
        return copy_model(request)

    def describe_call(self, request):
        def reserve(counter):
            return counter(copy_model(request)) + request.parameters['output_cap']
        return ModelCall('completion', request.parameters.get('model'), reserve,
                         lambda response: copy_model(response.usage), self.classify_error)

    def describe_request(self, request):
        return {'model': request.parameters.get('model')}

    def effective_attempts(self, request, maximum):
        return maximum

    def classify_error(self, error):
        return getattr(error, 'code', 'independent_transport')

    def retryable(self, error):
        return isinstance(error, ConnectionError)

    def retry_fields(self, request):
        return ('wait',)

    def validate_retry(self, request, *, original):
        self.validate_request(request.parameters, require_model=True)
        fixed = replace(request, parameters={**request.parameters, 'wait': original.parameters.get('wait')})
        previous = replace(original, parameters={**original.parameters, 'wait': original.parameters.get('wait')})
        if fixed != previous:
            raise ValueError('Independent retry changed input or authority')

    def validate_processing(self, parameters, *, original):
        if parameters != original:
            raise ValueError('Independent processing changed parameters')

    def decoder(self, **limits):
        return IndependentDecoder(**limits)

    async def stream(self, request, *, buffer_size):
        slots = current_calls()
        if slots is not None:
            await slots.acquire()
        try:
            with provider_attempt(self.describe_call(request)):
                self.requests.append(copy_model(request))
                self.entered.set()
                for frame in self.scripts.pop(0):
                    if isinstance(frame, BaseException):
                        raise frame
                    if isinstance(frame, asyncio.Event):
                        await frame.wait()
                    else:
                        yield frame
        finally:
            self.closed += 1
            if slots is not None:
                slots.release()


def invoke(*values):
    return {'invoke': [{'key': key, 'action': 'write_effect', 'values': arguments,
                        'seal': {'signed_id': key + ':signature'}} for key, arguments in values],
            'seal': {'reasoning_signature': 'signed:preserve'}, 'end': 'invoke'}


class IndependentProviderAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def create(self, *scripts, approval=False, usage=None, processors=()):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        effect = root / 'actual-effects.txt'
        async def write(arguments):
            with effect.open('a') as stream:
                stream.write(arguments['text'] + '\n')
            return {'source': 'original effect result'}
        contract = ToolContract('independent-v1', ToolEffect.EXTERNAL, ToolIsolation.NONE,
                                approval_required=approval, operation_key_required=False, retry_fields=())
        tool = Tool('write_effect', 'Write one test file', {'type': 'object',
            'properties': {'text': {'type': 'string'}}, 'required': ['text'], 'additionalProperties': False},
            write, contract=contract)
        adapter = IndependentAdapter(*scripts)
        components = [RuntimeTools(ToolRegistry((tool,))), *processors]
        app = LargeLanguageModel(root, engines={'loop': LoopEngine(completion_adapter=adapter)}, components=components)
        self.addAsyncCleanup(app.shutdown)
        app.policy_resolver.token_counters['independent'] = lambda request: 1 if isinstance(request, ModelRequest) else -1
        config = {'parameters': {'engines': {'loop': {'config': {'completion': {
            'model': 'independent-model', 'output_cap': 2}}}}, 'components': {
            'tools': {'config': {'enabled': ['write_effect']}}}}}
        if usage is not None:
            config['policies'] = {'usage': {**usage, 'counter': 'independent'}}
        project = await app.projects.acreate(components=[c.name for c in components], config=config)
        session = await project.sessions.acreate()
        return app, project, session, adapter, effect

    async def run_once(self, session):
        return await (await session.run.submit('write once, then answer', engine='loop')).wait(timeout=20)

    async def test_real_effect_opaque_pair_processor_and_checkpoint(self):
        seen = []
        class Preview(CompletionSession):
            async def prepare(self, request):
                for index, message in enumerate(request.messages):
                    if message.message.result is not None:
                        request.messages[index] = message.with_text('preview only')
                if False:
                    yield
            async def after_completion(self, observation):
                seen.append(observation)
                if False:
                    yield
        feature = Feature('independent_observer', Processor('independent_observer', lambda _: Preview()))
        _, _, session, adapter, effect = await self.create(
            [invoke(('effect:one', {'text': 'one'}))], [{'words': 'done', 'end': 'done'}], processors=(feature,))
        run = await self.run_once(session)
        self.assertEqual(run.data.status, 'completed', run.data.error)
        self.assertEqual(effect.read_text(), 'one\n')
        self.assertEqual((len(adapter.requests), adapter.closed), (2, 2))
        sent = adapter.requests[1]
        proposal = next(m for m in sent.messages if m.calls)
        result = next(m.result for m in sent.messages if m.result)
        self.assertEqual(proposal.opaque, {'reasoning_signature': 'signed:preserve'})
        self.assertEqual(proposal.calls[0].opaque, {'signed_id': 'effect:one:signature'})
        self.assertEqual((proposal.calls[0].id, result.call_id, result.content), ('effect:one', 'effect:one', 'preview only'))
        self.assertEqual(seen[-1].request, sent)
        checkpoint = await run.acheckpoint()
        self.assertEqual(checkpoint['header']['format'], LoopEngine.CHECKPOINT_FORMAT)
        self.assertEqual(ModelResponse.from_dict(checkpoint['records']['iteration:1']['response']).message, proposal)
        steps = await run.steps.alist()
        tool = next(s for s in steps if s.kind == 'tool')
        self.assertIn('original effect result', json.dumps(tool.metadata))
        self.assertNotIn('preview only', json.dumps(tool.metadata))
        self.assertEqual((await run.aresponse()).content, 'done')

    async def test_incomplete_response_and_invalid_batch_have_no_file_effect(self):
        for script in ([{'invoke': invoke(('partial', {'text': 'bad'}))['invoke']}],
                       [invoke(('valid', {'text': 'bad'}), ('invalid', {'text': 42}))]):
            with self.subTest(script=script):
                _, _, session, adapter, effect = await self.create(script)
                run = await self.run_once(session)
                self.assertEqual(run.data.status, 'failed')
                self.assertEqual((effect.exists(), len(adapter.requests), adapter.closed), (False, 1, 1))

    async def test_approval_reopen_uses_saved_common_response_once(self):
        app, project, session, adapter, effect = await self.create(
            [invoke(('approved:one', {'text': 'approved'}))], [{'words': 'done', 'end': 'done'}], approval=True)
        paused = await self.run_once(session)
        self.assertEqual(paused.data.status, 'paused', paused.data.error)
        self.assertFalse(effect.exists())
        request, = await paused.ainteractions()
        await paused.arespond(request.respond('approve'))
        await app.shutdown()
        reopened = LargeLanguageModel(effect.parent,
            engines={'loop': LoopEngine(completion_adapter=adapter)}, components=[app.project_manager.components.get("tools")])
        self.addAsyncCleanup(reopened.shutdown)
        resumed_session = await (await reopened.projects.aload(project.id)).sessions.aload(session.id)
        result = await (await resumed_session.run.resume(paused.id, engine='loop')).wait(timeout=20)
        self.assertEqual(result.data.status, 'completed', result.data.error)
        self.assertEqual((effect.read_text(), len(adapter.requests)), ('approved\n', 2))

    async def test_unknown_usage_keeps_reservation_and_blocks_next_call(self):
        _, project, session, adapter, effect = await self.create(
            [{'words': 'done', 'end': 'done'}], usage={'project_max_tokens': 3})
        first = await self.run_once(session)
        self.assertEqual(first.data.status, 'completed', first.data.error)
        evidence, = first.data.metadata['completions']
        self.assertEqual((evidence['reserved_tokens'], evidence['usage'], evidence['usage_complete']), (3, {}, False))
        blocked = await self.run_once(session)
        self.assertEqual((blocked.data.error_code, len(adapter.requests), effect.exists()), ('usage_limit', 1, False))

    async def test_old_checkpoint_or_missing_response_field_cannot_resume_an_effect(self):
        for damage in ('old_format', 'missing_usage'):
            with self.subTest(damage=damage):
                app, project, session, adapter, effect = await self.create(
                    [invoke(('protected', {'text': 'must not run'}))], approval=True)
                paused = await self.run_once(session)
                self.assertEqual(paused.data.status, 'paused', paused.data.error)
                request, = await paused.ainteractions()
                await paused.arespond(request.respond('approve'))
                checkpoint = await paused.acheckpoint()
                damaged = copy_model(checkpoint)
                if damage == 'old_format':
                    damaged['header']['format'] = 'loop-iterations-v1'
                else:
                    del damaged['records']['iteration:1']['response']['usage']
                with self.assertRaises(ValueError):
                    LoopEngine(completion_adapter=adapter).validate_resume(damaged)
                directory = paused.data.paths.state / 'checkpoints' / 'loop'
                if damage == 'old_format':
                    path = directory / 'checkpoint.json'
                    value = json.loads(path.read_text())
                    value['header']['format'] = damaged['header']['format']
                else:
                    import hashlib
                    path = directory / 'records' / (hashlib.sha256(b'iteration:1').hexdigest() + '.json')
                    value = json.loads(path.read_text())
                    del value['value']['response']['usage']
                await app.shutdown()
                path.write_text(json.dumps(value))
                before = path.read_bytes()
                reopened = LargeLanguageModel(effect.parent,
                    engines={'loop': LoopEngine(completion_adapter=adapter)},
                    components=[app.project_manager.components.get('tools')])
                self.addAsyncCleanup(reopened.shutdown)
                reopened_session = await (await reopened.projects.aload(project.id)).sessions.aload(session.id)
                with self.assertRaises((ValueError, TypeError)):
                    await reopened_session.run.resume(paused.id, engine='loop')
                self.assertEqual((path.read_bytes(), effect.exists(), len(adapter.requests)), (before, False, 1))

    def test_falsey_adapter_identity_is_preserved_at_each_streaming_owner(self):
        from llm.components.memory.component import MemoryComponent
        from llm.components.memory.processing import MemoryProcessor
        class FalseyAdapter(IndependentAdapter):
            def __bool__(self):
                return False
        adapter = FalseyAdapter()
        for owner in (BaseEngine(completion_adapter=adapter), LoopEngine(completion_adapter=adapter),
                      MemoryComponent(completion_adapter=adapter), MemoryProcessor(None, completion_adapter=adapter)):
            self.assertIs(owner.completion_adapter, adapter)

    async def test_final_only_usage_and_termination_are_persisted(self):
        _, _, session, _, _ = await self.create(
            [{'words': 'done', 'end': 'done', 'tally': {'total_tokens': 2}}], usage={'project_max_tokens': 3})
        run = await self.run_once(session)
        self.assertEqual(run.data.status, 'completed', run.data.error)
        entry, = run.data.metadata['completions']
        self.assertEqual((entry['usage'], entry['usage_complete'], entry['termination'], entry['finish_reason']),
                         ({'total_tokens': 2}, True, 'complete', 'done'))

    async def test_partial_chunk_forbids_retry_and_cancel_releases_slot(self):
        request = ModelRequest([ModelMessage('user', 'q')], {'model': 'independent', 'output_cap': 2})
        adapter = IndependentAdapter([{'words': 'partial'}, ConnectionError('lost')], [{'end': 'done'}])
        engine = BaseEngine(completion_adapter=adapter)
        with self.assertRaises(ConnectionError):
            async for _ in engine.stream_completion(request, provider={'max_attempts': 2}):
                pass
        self.assertEqual((len(adapter.requests), adapter.closed), (1, 1))
        blocker = asyncio.Event()
        adapter = IndependentAdapter([blocker])
        engine = BaseEngine(completion_adapter=adapter)
        slots = ProviderCalls(ProviderLimits(max_active=1, max_waiting=0))
        async def consume():
            async with aclosing(engine.stream_completion(request)) as stream:
                async for _ in stream:
                    pass
        with slots.scope():
            task = asyncio.create_task(consume())
            await asyncio.wait_for(adapter.entered.wait(), 5)
            self.assertEqual(slots.stats['active'], 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual((adapter.closed, slots.stats), (1, {'active': 0, 'waiting': 0}))

    async def test_explicit_decoder_limits_cannot_be_ignored(self):
        request = ModelRequest([ModelMessage('user', 'q')], {'model': 'independent'})
        cases = [({'max_tool_calls': 1}, invoke(('one', {'text': 'one'}), ('two', {'text': 'two'}))),
                 ({'max_argument_chars': 1}, invoke(('one', {'text': 'one'}))),
                 ({'max_output_chars': 1}, {'words': 'too long', 'end': 'done'})]
        for limits, frame in cases:
            with self.subTest(limits=limits):
                adapter = IndependentAdapter([frame])
                with self.assertRaises(CompletionError):
                    async for _ in BaseEngine(completion_adapter=adapter).stream_completion(request, **limits):
                        pass
                self.assertEqual(adapter.closed, 1)
