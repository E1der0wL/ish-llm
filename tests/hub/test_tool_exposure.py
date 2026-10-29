"""Hub invocation selection integrates with the public native exposure contract."""

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from jsonschema import Draft202012Validator, ValidationError

from hub.backend.execution.request_profile import RequestMode, RequestProfile
from hub.backend.execution.request_processing import RoutedProcessor
from hub.backend.execution.request_routing import RequestRouting
from hub.backend.execution.tool_exposure import HubToolSelector
from hub.backend.tools.validation import ToolInputRegistry
from llm.components.processing import CompletionSession
from llm.components.tools import Tool, ToolExposure, ToolRegistry
from llm.core.model_protocol import ModelMessage, ModelRole, ToolCall, ToolResult
from llm.core.tool_contracts import ToolEffect
from llm.engines.loop import LoopEngine
from llm.engines.loop.review import LoopReview
from tests.llm.support.runtime_tools import TEST_CONTRACT


class ExposureTests(unittest.IsolatedAsyncioTestCase):
    def tools(self):
        async def handler(arguments):
            raise AssertionError('Exposure must never invoke a handler')
        return ToolInputRegistry(tuple(Tool(name, description, {'type': 'object', 'properties': {
            'count': {'type': 'integer'}}}, handler, contract=replace(TEST_CONTRACT, effect=effect))
            for name, description, effect in (
                ('hub_help', 'Find Tools', ToolEffect.READ_ONLY),
                ('file_read', 'Read a file', ToolEffect.READ_ONLY),
                ('file_create', 'Create a file', ToolEffect.EXTERNAL),
                ('ask_user', 'Ask a question', ToolEffect.UNKNOWN),
                ('company_export', 'Export a report', ToolEffect.UNKNOWN))))

    def exposure(self, tools, messages):
        return ToolExposure(1, tuple(messages), tools.names(), False)

    def discovery(self, selector, *, query='company_export'):
        args = {'tool_query': query}
        call = ToolCall('find', 'hub_help', args)
        return [ModelMessage(ModelRole.ASSISTANT, calls=(call,)),
                ModelMessage(ModelRole.TOOL, result=ToolResult('find',
                    json.dumps(selector.lookup.search(query)), 'hub_help'))]

    async def test_discovery_is_bounded_verified_and_cannot_expand_agent_scope(self):
        tools = self.tools()
        selector = HubToolSelector(tools, RequestMode.LOOKUP, discovery=True)
        detached = selector.lookup.search('company_export')
        detached['tools'][0]['tags'].clear()
        detached['tools'][0]['parameters']['properties'].clear()
        intact = selector.lookup.search('company_export')['tools'][0]
        self.assertTrue(intact['tags'])
        self.assertIn('count', intact['parameters']['properties'])
        request = [ModelMessage(ModelRole.USER, 'Find a report tool')]
        base = await selector.select_tools(self.exposure(tools, request))
        self.assertNotIn('company_export', base)
        messages = request + self.discovery(selector)
        self.assertIn('company_export', await selector.select_tools(self.exposure(tools, messages)))
        self.assertEqual(await selector.select_tools(self.exposure(tools, messages[:-1])), base)
        forged = replace(messages[-1], result=replace(messages[-1].result,
            content=json.dumps({'tools': [{'name': 'file_create'}]})))
        self.assertEqual(await selector.select_tools(self.exposure(tools, [*messages[:-1], forged])), base)
        wrong_tool = replace(messages[-1], result=replace(messages[-1].result, name='file_read'))
        self.assertEqual(await selector.select_tools(self.exposure(tools, [*messages[:-1], wrong_tool])), base)
        available = tuple(name for name in tools.names() if name != 'company_export')
        self.assertNotIn('company_export', await selector.select_tools(ToolExposure(2, tuple(messages), available, False)))
        # A new instruction segment gets a fresh exposure, not prior search grants.
        messages.append(ModelMessage(ModelRole.USER, 'Explain something else'))
        self.assertEqual(await selector.select_tools(self.exposure(tools, messages)), base)

    async def test_shared_selector_has_no_run_state_and_never_rebuilds_registry(self):
        tools = self.tools()
        selector = HubToolSelector(tools, RequestMode.CONVERSATION, discovery=True)
        greeting = self.exposure(tools, [ModelMessage(ModelRole.USER, 'hello')])
        working = self.exposure(tools, [ModelMessage(ModelRole.USER, 'hello'),
                                       ModelMessage(ModelRole.USER, 'Create a file')])
        with patch.object(ToolRegistry, 'register', side_effect=AssertionError('No per-call registration')):
            a, b, c = await asyncio.gather(selector.select_tools(greeting),
                selector.select_tools(working), selector.select_tools(greeting))
        self.assertEqual(a, ('ask_user',))
        self.assertEqual(a, c)
        self.assertEqual(b, tools.names())
        self.assertEqual(selector.revision, HubToolSelector(tools, RequestMode.CONVERSATION, discovery=True).revision)
        self.assertNotEqual(selector.revision, HubToolSelector(tools, RequestMode.WORK, discovery=True).revision)
        reviewed = self.exposure(tools, [*greeting.messages,
            ModelMessage(ModelRole.USER, LoopReview.FEEDBACK_PREFIX + 'Provide answer content')])
        self.assertEqual(await selector.select_tools(reviewed), ('ask_user',))

    def test_definition_subset_preserves_constraints_without_registration(self):
        tools = self.tools()
        with patch.object(ToolRegistry, 'register', side_effect=AssertionError('No schema re-registration')):
            definition, = tools.definitions(names=('file_read',), constraints={
                'file_read': {'count': {'mode': 'fixed', 'value': 3}}})
            self.assertEqual(tools.definitions(names=()), ())
        self.assertEqual(definition.name, 'file_read')
        self.assertIn('count', definition.parameters['required'])
        validator = Draft202012Validator(definition.parameters)
        validator.validate({'count': 3})
        with self.assertRaises(ValidationError):
            validator.validate({'count': 4})
        self.assertEqual(len(tools.names()), 5)

    def test_custom_native_selector_remains_owner(self):
        class CustomSelector:
            revision = 'custom.v1'
            async def select_tools(self, request):
                return ()
        engine = LoopEngine(tool_selector=CustomSelector())
        routing = RequestRouting({'engine': 'loop'}, RequestProfile(RequestMode.CONVERSATION))
        context = NS(output_step_id=None)
        self.assertFalse(routing.enabled(context, engine))
        self.assertIs(routing.engine(context, engine), engine)

    async def test_optional_processor_is_activated_after_steering_and_always_closed(self):
        observed = []
        class Session(CompletionSession):
            async def prepare(self, request):
                observed.append('prepare')
                if False:
                    yield
            async def after_completion(self, observation):
                observed.append('after')
                if False:
                    yield
            async def finish(self, observation):
                observed.append('finish')
                if False:
                    yield
            async def aclose(self, error):
                observed.append(('close', error))
        processor = NS(name='memory', priority=10, close_timeout=None, session=lambda context: Session())
        session = RoutedProcessor(processor, RequestMode.CONVERSATION).session(NS(run=NS(input_message_id='user')))
        request = NS(messages=[NS(source_id='user', message=ModelMessage(ModelRole.USER, 'hello'))])
        for stream in (session.prepare(request), session.after_completion(None)):
            self.assertEqual([event async for event in stream], [])
        self.assertEqual(observed, [])
        request.messages.append(NS(source_id='steering', message=ModelMessage(ModelRole.USER, 'Create a file')))
        for stream in (session.prepare(request), session.after_completion(None), session.finish(None)):
            self.assertEqual([event async for event in stream], [])
        await session.aclose(None)
        self.assertEqual(observed, ['prepare', 'after', 'finish', ('close', None)])
