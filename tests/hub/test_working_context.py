"""Model admission uses public processing contracts without rewriting Tool effects."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import tempfile
from types import SimpleNamespace as NS
import unittest

from llm.components.processing import CompletionInput, CompletionMessage, CompletionRequest
from llm.components.tools import Tool, ToolRegistry
from llm.components.memory.tool_values import MemoryToolSchemas
from llm.core.tool_contracts import ToolContract, ToolEffect, ToolIsolation
from llm.core.contracts import ResourceRef
from llm.core.models import ProjectConfig
from llm.core.model_protocol import ModelMessage, ModelRole, ToolCall, ToolResult, ToolDefinition
from llm.engines.base import EngineContext
from llm.policies import CompletionPolicy, ExecutionLimitError
from hub.backend.execution.working_context import WorkingContext
from hub.backend.execution.tool_references import ToolReferences


def counter(request):
    return sum(len(m.text) + len(json.dumps([c.to_dict() for c in m.calls]))
               for m in request.messages) + len(json.dumps([t.to_dict() for t in request.tools]))


class WorkingContextTests(unittest.IsolatedAsyncioTestCase):
    def test_hub_processor_follows_explicit_memory_priority(self):
        from hub.backend.execution.completion import FollowupProcessor
        for priority in (10, 1000, 2000):
            config = ProjectConfig()
            config.set_component_parameters('memory', {'config': {'processing': {'priority': priority}}})
            processor = FollowupProcessor.for_project(NS(components=('memory',), config=config))
            self.assertGreater(processor.priority, priority)
            self.assertEqual(config.parameter_targets.component('memory').config['processing']['priority'], priority)

    def setup_request(self, *, turns=4, chars=7000, arguments=0, budget=None, reader=True):
        context = EngineContext(
            NS(id='p', components=('memory',), config=ProjectConfig()), NS(id='s'),
            NS(id='run', input_message_id='input'), (),
            completion_policy=CompletionPolicy(budget, counter=counter) if budget else None)
        messages = [CompletionMessage(ModelMessage(ModelRole.SYSTEM, 'Keep instructions')),
                    CompletionMessage(ModelMessage(ModelRole.USER, 'Analyze safely'), 'input')]
        for i in range(turns):
            messages.extend([
                CompletionMessage(ModelMessage(ModelRole.ASSISTANT,
                    calls=(ToolCall(f'c{i}', 'source_read', {'query': 'q' * arguments}),))),
                CompletionMessage(ModelMessage(ModelRole.TOOL,
                    result=ToolResult(f'c{i}', f'result-{i} ' + 'x' * chars)),
                    result_ref=ResourceRef(kind='step', id=f'step{i}', run_id='run', step_id=f'step{i}'))])
        registry = ToolRegistry((Tool('memory_tool_result', 'Read saved results',
            MemoryToolSchemas().tool_result(), lambda args: None,
            contract=ToolContract(revision='test', effect=ToolEffect.READ_ONLY, isolation=ToolIsolation.NONE,
                approval_required=False, operation_key_required=False, retry_fields=())),)) if reader else ToolRegistry()
        context = replace(context, tools=registry)
        tools = registry.definitions()
        return context, CompletionRequest({}, messages, turns, tools=tools)

    async def prepare(self, context, request):
        original = deepcopy(request.messages)
        invocation = request.to_model()
        events = [event async for event in WorkingContext.prepare(context, request)]
        CompletionInput.validate(request, original, invocation, 'input')
        return events

    async def test_accumulated_results_keep_latest_and_leave_source_unchanged(self):
        context, request = self.setup_request()
        original = deepcopy(request)
        events = await self.prepare(context, request)
        self.assertLess(WorkingContext.tool_chars(request), 24000)
        self.assertEqual(request.messages[-1], original.messages[-1])
        self.assertEqual(request.compacted_tool_calls, ())
        self.assertEqual(json.loads(request.messages[3].message.text)['arguments']['step_id'], 'step0')
        self.assertGreater(len(original.messages[3].message.text), 7000)
        self.assertTrue(any(e.metadata.get('hub_context') for e in events))
        self.assertEqual(request.tools, original.tools)

    async def test_native_budget_compacts_complete_prefix_including_large_arguments(self):
        context, request = self.setup_request(chars=1000, arguments=3000, budget=6000)
        original = deepcopy(request)
        await self.prepare(context, request)
        self.assertEqual(request.compacted_tool_calls, ('c0', 'c1', 'c2'))
        self.assertTrue(request.messages[1].message.text.startswith('Analyze safely'))
        self.assertIn('step0', request.messages[1].message.text)
        self.assertEqual(request.messages[-2:], original.messages[-2:])
        self.assertTrue(await WorkingContext.fits(context, request))

    async def test_steering_is_not_removed_or_crossed_by_prefix_compaction(self):
        context, request = self.setup_request(turns=5, chars=1000, arguments=3000, budget=16000)
        request.messages.insert(6, CompletionMessage(
            ModelMessage(ModelRole.USER, 'Do not change files'), 'steer', True))
        await self.prepare(context, request)
        self.assertEqual(request.compacted_tool_calls, ('c0',))
        steer = next(m for m in request.messages if m.source_id == 'steer')
        self.assertEqual(steer.message.text, 'Do not change files')
        self.assertTrue(steer.continuation)

    async def test_missing_reader_or_reference_never_fabricates_access(self):
        for reader in (True, False):
            context, request = self.setup_request(chars=30000, turns=1, reader=reader)
            if reader:
                request.messages[-1] = replace(request.messages[-1], result_ref=None)
            original = deepcopy(request)
            with self.assertRaises(ExecutionLimitError):
                await self.prepare(context, request)
            self.assertEqual(request, original)

    async def test_protected_current_instruction_is_not_truncated(self):
        context, request = self.setup_request(turns=0, budget=1000)
        request.messages[1] = request.messages[1].with_text('important ' * 1000)
        original = deepcopy(request)
        with self.assertRaises(ExecutionLimitError):
            await self.prepare(context, request)
        self.assertEqual(request, original)

    async def test_explicit_active_compaction_choice_and_retained_iterations_are_preserved(self):
        for policy, expected in (({'compact_active': False}, ()),
                                 ({'active_keep_iterations': 3}, ('c0',))):
            context, request = self.setup_request(chars=1000, arguments=3000)
            context.project.config.set_component_parameters('memory', {'policy': {'processing': policy}})
            original, invocation = deepcopy(request.messages), request.to_model()
            WorkingContext.compact_prefix(context, request)
            CompletionInput.validate(request, original, invocation, 'input')
            self.assertEqual(request.compacted_tool_calls, expected)

    async def test_policy_cancellation_and_counter_errors_are_not_swallowed(self):
        context, request = self.setup_request(turns=0)
        for error in (asyncio.CancelledError(), ValueError('invalid counter')):
            def fail(_):
                raise error
            context = replace(context, completion_policy=CompletionPolicy(10000, counter=fail))
            with self.assertRaises(type(error)):
                await self.prepare(context, request)

    async def test_existing_memory_compaction_declaration_is_preserved(self):
        context, request = self.setup_request(chars=1000, arguments=3000, budget=6000)
        original, invocation = deepcopy(request.messages), request.to_model()
        request.messages[1] = request.messages[1].with_text('Analyze safely\nMemory summary of c0.')
        del request.messages[2:4]
        request.compacted_tool_calls = ('c0',)
        _ = [e async for e in WorkingContext.prepare(context, request)]
        CompletionInput.validate(request, original, invocation, 'input')
        self.assertEqual(request.compacted_tool_calls, ('c0', 'c1', 'c2'))
        self.assertIn('Memory summary of c0.', request.messages[1].message.text)


class WorkingContextIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_failure_still_bounds_accumulated_results_without_reexecution(self):
        from llm.components.memory import MemoryComponent
        from llm.components.data import DataComponent
        from llm.providers.litellm import LiteLLMCompletionAdapter
        from hub.backend.runtime import HubConfig, HubRuntime
        from hub.backend.execution.retries import HubLoopEngine
        from tests.llm.test_large_data import SourceTools, answer
        from tests.llm.test_loop import ScriptedCompletion, call, chunk

        source = SourceTools()
        source.content = 'original evidence ' * 380
        summary_inputs, main_inputs = [], []
        def summary(**request):
            summary_inputs.append(request)
            raise ValueError('Summary provider failed')
        scripted = ScriptedCompletion(*[
            [chunk(calls=[call('{}', name='source_read', call_id=f'c{i}')], finish='tool_calls')]
            for i in range(8)], answer('Analysis complete.'))
        def model(**request):
            main_inputs.append(request)
            self.assertLess(len(json.dumps(request['messages'])), 50000)
            yield from scripted(**request)
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model='test/main', auto_title=False,
                postprocess=False,
                project_config={'parameters':{'engines':{'loop':{'policy':{'final_review':None}}}}},
                engine_factories={'loop':lambda: HubLoopEngine(
                    completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))},
                component_factories=(lambda: MemoryComponent(completion_adapter=
                    LiteLLMCompletionAdapter(completion_fn=summary)), DataComponent, lambda: source)))
            self.addAsyncCleanup(runtime.close)
            await runtime.start()
            service = runtime.settings_service
            record = await service.load_project(runtime.project.id)
            values = deepcopy(record['values'])
            memory = values['config']['parameters']['components']['memory']
            memory['config']['processing']['completion'] = {'model':'test/summary'}
            memory['policy']['processing']['max_summary_calls'] = 1
            await service.save_project(runtime.project.id, values, record['config_version'])
            sid = await runtime.new_session()
            run, = await (await runtime.sessions[sid].run.submit(
                'Analyze eight results', recipients=['assistant'])).wait(timeout=45)
            self.assertEqual(run.data.status, 'completed', run.data.error)
            self.assertEqual((source.calls, len(main_inputs)), (8, 9))
            self.assertTrue(summary_inputs)
            self.assertIn(ToolReferences.NOTICE, str(main_inputs[-1]))
            steps = await run.steps.alist()
            self.assertTrue(any(s.name == WorkingContext.STEP for s in steps))
            self.assertFalse(any(s.metadata.get('hub_recovery') for s in steps))
