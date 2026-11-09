"""Common cards, durable question evidence, language and instruction decisions."""

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
import asyncio
import json
import tempfile
import threading

from llm.core.models import Message, RunStatus
from llm.core.results import EngineOutput
from llm.core.execution_observations import ExecutionObservations
from llm.core.agents import AgentBinding
from llm.core.steering import SteeringTarget, SteeringMode
from llm.core.instruction_records import InstructionReceipt, InstructionMessageRecord
from llm.providers.litellm import LiteLLMCompletionAdapter
from llm.components.processing import CompletionRequest
from tests.llm.test_loop import ScriptedCompletion, chunk

from hub.backend.runtime import HubRuntime, HubConfig
from hub.backend.execution.instruction_routing import InstructionRouting, InstructionEngine
from hub.backend.execution.language import LanguageSession
from hub.backend.execution.tool_details import ToolDetails, ValuePreview
from hub.backend.execution.continuations import continued_messages
from hub.backend.execution.trace import tool_trace
from hub.backend.execution.steps import display_steps
from hub.backend.execution.instruction_display import UserInstructions
from hub.widget.feedback.answer_feedback import AnswerFeedback, project_answers
from hub.widget.transcript.thought_trace import render_trace_item
from hub.widget.transcript.instruction_caption import instruction_caption, target_captions
from hub.model import ChatMessage, ReasoningMessage, TraceItem, InstructionDisplay, InstructionTargetDisplay
from hub.config.theme import HubTheme
from hub.locales import Language
from hub.locales.keys import MessageKey


class CardTests(TestCase):
    def test_graph_judge_uses_selected_admitted_agent_model_not_another_nodes_model(self):
        from llm.engines.agent_values import AgentExecutionBinding
        from llm.engines.graph.node_records import GraphStepMetadata
        binding = AgentBinding.create('a' * 32, 'planner', {'engine': 'graph'})
        source = NS(engine='graph', agent_id='planner', metadata=AgentBinding.metadata(binding))
        def step(path, model):
            return NS(status='running', metadata={GraphStepMetadata.NODE_PATH: path,
                AgentExecutionBinding.Field.AGENT: {'engine': 'loop', 'completion': {'model': model}}})
        steps = [step('selected', 'test/selected'), step('other', 'test/other')]
        definition = InstructionRouting.model_definition(source, steps, [NS(node_path='selected')])
        self.assertEqual(definition['completion']['model'], 'test/selected')
        with self.assertRaises(ValueError):
            InstructionRouting.model_definition(source, steps, [NS(node_path='missing')])

    def test_unknown_tool_and_large_nested_values_remain_literal_bounded_and_unchanged(self):
        value = {'custom': {'data': ['\x1b[2J[bold]raw[/bold]\n' * 300000]}, 'other': 3}
        output = ValuePreview.render(value)
        self.assertNotIn('\x1b', output)
        self.assertLessEqual(len(output), ValuePreview.CHARACTERS + 1)
        self.assertLessEqual(len(output.splitlines()), ValuePreview.LINES)
        self.assertIn('\x1b', value['custom']['data'][0])
        metadata = {'arguments': {'path': '/does/not/exist'}}
        ExecutionObservations.store_output(metadata, EngineOutput(data=value))
        item = TraceItem('t', TraceItem.TOOL, 'custom_plugin', 'completed',
                         details=ToolDetails.project(metadata))
        for width in (20, 80, 120):
            rows = render_trace_item(item, width, HubTheme(), Language('ko'))
            text = '\n'.join(''.join(part for _, part in row) for row in rows)
            self.assertIn('custom', text)
            self.assertNotIn('\x1b', text)

    def test_question_same_card_pending_local_answer_and_stored_completion(self):
        from hub.backend.interactions.question_values import QuestionTool
        from hub.backend.execution.question_trace import QuestionTrace
        request = QuestionTool.request({'question': 'Which format?', 'description': 'Needed for export'})
        observation = NS(request=request, response=None, status='pending', can_respond=True)
        pending, = QuestionTrace.project([observation])
        self.assertEqual((pending.kind, pending.status), (TraceItem.QUESTION, 'waiting_input'))
        before = ReasoningMessage.create('a', '', trace=(pending,))
        feedback = AnswerFeedback((), None, pending.id, 'a', False, False, answer='Markdown')
        local, = project_answers([feedback], (before,))
        self.assertEqual(local.trace[0].status, 'answer_sending')
        self.assertIn((MessageKey.CARD_ANSWER, 'Markdown'), local.trace[0].details)
        observation.response = request.respond('reply', value='Markdown')
        observation.status, observation.can_respond = 'answered', False
        done, = QuestionTrace.project([observation])
        self.assertEqual(done.id, pending.id)
        stored = replace(before, trace=(done,))
        self.assertIs(project_answers([feedback], (stored,))[0], stored)
        for status in ('committed', 'streaming', 'paused'):
            with self.subTest(status=status):
                visible = continued_messages([], (stored, ChatMessage('assistant', 'Working', id='a', status=status)))
                self.assertEqual(visible[0].trace, (done,))
        for status in ('completed', 'failed', 'interrupted', 'cancelled'):
            with self.subTest(status=status):
                answer = ChatMessage('assistant', 'Finished', id='a', status=status)
                self.assertEqual(continued_messages([], (stored, answer)), (answer,))
        # The existing empty-answer fallback keeps all thought evidence together.
        for status in ('completed', 'failed'):
            visible = continued_messages([], (stored, ChatMessage('assistant', '', id='a', status=status)))
            self.assertEqual(visible[0].trace, (done,))

    def test_targets_report_individually_without_partial_aggregate(self):
        item = InstructionDisplay('i', 'Change plan', 'partially_applied', targets=(
            InstructionTargetDisplay('analysis', 'applied'),
            InstructionTargetDisplay('writer', 'unapplied', 'completed')))
        self.assertEqual(instruction_caption(item, Language('ko')), '')
        rows = target_captions(item, Language('ko'))
        self.assertIn('analysis', rows[0])
        self.assertIn('writer', rows[1])
        self.assertIn('실행 종료', rows[1])
        self.assertNotIn('일부', str(rows))


class RoutingTests(IsolatedAsyncioTestCase):
    async def test_real_graph_target_exposes_its_admitted_model_for_the_judge(self):
        from llm.components.workflows import WorkflowGraph
        from llm.engines.loop import LoopEngine
        from tests.llm.test_loop import call
        from tests.hub.support import backend_ready
        model = ScriptedCompletion([chunk(calls=[call('{"question":"Which target?"}', name='ask_user_async')],
                                           finish='tool_calls')], [chunk('Done', finish='stop')])
        entered, release = threading.Event(), threading.Event()
        def provider(**kwargs):
            if len(model.requests) == 1:
                entered.set()
                if not release.wait(30):
                    raise TimeoutError('Graph question test gate')
            return model(**kwargs)
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, model='test/graph-child', auto_title=False,
                engine_factories={'loop': lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
            try:
                await runtime.start()
                graph = WorkflowGraph(entry='worker').node('worker', 'agent', agent='assistant').node('end', 'end').connect('worker', 'end')
                await runtime.project.components.workflows.acreate(graph.to_dict(), identifier='questions')
                await runtime.project.agents.acreate({'engine': 'graph', 'engine_options': {'workflow': 'questions'}}, identifier='graph-room')
                sid = await runtime.new_session()
                await runtime.invite_agent(runtime.project.id, sid, 'graph-room')
                await runtime.submit(sid, 'Work, ask first', 'graph-room')
                self.assertTrue(await asyncio.to_thread(entered.wait, 15))
                handle, = await runtime.sessions[sid].run.alist()
                targets = [target for target in await handle.ainstruction_targets() if target.accepting]
                definition = InstructionRouting.model_definition(await handle.aget_data(), await handle.steps.alist(), targets)
                self.assertEqual(definition['completion']['model'], 'test/graph-child')
            finally:
                release.set()
                await runtime.close()

    async def test_language_guidance_is_transient_and_no_translation_call(self):
        from llm.components.processing import CompletionMessage
        from llm.core.model_protocol import ModelMessage
        original = CompletionMessage(ModelMessage('user', '줄 번호 표시도 추가해줘.'), 'u')
        request = CompletionRequest({}, [original], 1)
        async for _ in LanguageSession('ko').prepare(request):
            self.fail('Language guidance must not invoke a model')
        guidance = request.messages[0]
        self.assertIn('Korean (ko)', guidance.message.text)
        self.assertIn('thinking/reasoning channel', guidance.message.text)
        self.assertIn('unless the user explicitly', guidance.message.text)
        self.assertIsNone(guidance.source_id)
        self.assertIs(request.messages[1], original)
        self.assertEqual(original.message.text, '줄 번호 표시도 추가해줘.')

    async def test_decision_dispatch_receipt_replay_and_failure_do_not_resume_or_replace(self):
        native = NS(steer=AsyncMock(return_value=NS(id='s')), redirect=AsyncMock(return_value=NS(id='r')),
                    get_receipt=AsyncMock(return_value=None))
        session = NS(project=NS(id='p'), id='s', run=native)
        runtime = NS(_instruction_run=AsyncMock(return_value=NS(id='run')))
        router = InstructionRouting(runtime)
        router.assess = AsyncMock(return_value='redirect')
        self.assertEqual((await router.send(session, 'run', 'Change direction', ['root'], 'key')).id, 'r')
        native.redirect.assert_awaited_once_with('run', 'Change direction', targets=['root'], request_key='key')
        native.get_receipt.return_value = NS(kind='redirect')
        await router.send(session, 'run', 'Change direction', ['root'], 'key')
        self.assertEqual(router.assess.await_count, 1)
        native.get_receipt.return_value = None
        router.assess.side_effect = ValueError('provider failure')
        with self.assertRaises(ValueError):
            await router.send(session, 'run', 'Change direction', ['root'])
        self.assertEqual(native.redirect.await_count, 2)
        native.steer.assert_not_awaited()

    async def test_assessment_uses_isolated_native_job_and_bound_model_without_tools(self):
        model = ScriptedCompletion([chunk('{"action":"redirect","reason":"User changed direction"}', finish='stop')])
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False, model='test/model'))
            try:
                await runtime.start()
                sid = await runtime.new_session()
                session = runtime.sessions[sid]
                engine = runtime.backend.engines.get(InstructionEngine.IDENTITY)
                engine.completion_adapter = LiteLLMCompletionAdapter(completion_fn=model)
                definition = await runtime.project.agents.aload('assistant')
                binding = AgentBinding.create(runtime.project.id, 'assistant', definition)
                source = NS(id='f' * 32, input_message_id='a' * 32, session_id=sid,
                    agent_id='assistant', engine='loop', metadata=AgentBinding.metadata(binding))
                handle = NS(aget_data=AsyncMock(return_value=source), steps=NS(alist=AsyncMock(return_value=[])))
                # Only the source request lookup is stubbed. Helper Agent/Session/Run,
                # model invocation, completion, and cleanup use the public backend.
                request = NS(aget_data=AsyncMock(return_value=NS(content='Inspect files')))
                from unittest.mock import patch
                with patch.object(type(session.run), 'arequest', new=AsyncMock(return_value=request)):
                    action = await runtime.instruction_routing.assess(session, handle, 'Stop inspecting and write tests')
                self.assertEqual(action, 'redirect')
                self.assertEqual(len(model.requests), 1)
                self.assertFalse(model.requests[0].get('tools'))
                self.assertIn('Stop inspecting', str(model.requests[0]['messages']))
                self.assertEqual(len(await session.run.alist()), 0)
            finally:
                await runtime.close()

    async def test_native_redirect_replans_read_tool_but_never_replays_uncertain_effect(self):
        from llm.components.tools import Tool, ToolRegistry
        from llm.core.tool_contracts import ToolEffect
        from llm.engines.loop import LoopEngine
        from tests.llm.support.runtime_tools import RuntimeTools, TEST_CONTRACT
        from tests.llm.test_loop import call
        class ExplicitToolLoop(LoopEngine):
            pass  # Keep the exact test Agent selection; no app tool-discovery exercise.
        for effect in (ToolEffect.READ_ONLY, ToolEffect.EXTERNAL):
            with self.subTest(effect=effect), tempfile.TemporaryDirectory() as root:
                entered, cleaned = asyncio.Event(), asyncio.Event()
                executions = []
                async def act(arguments):
                    executions.append('started')
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cleaned.set()
                tool = Tool('long_action', 'test', {'type': 'object'}, act,
                            contract=replace(TEST_CONTRACT, effect=effect))
                provider = ScriptedCompletion(
                    [chunk(calls=[call('{}', name='long_action')], finish='tool_calls')],
                    [chunk('Revised answer', finish='stop')])
                runtime = HubRuntime(HubConfig(root, auto_title=False, model='test/model',
                    agent_definition={'engine': 'loop', 'tools': ['long_action']},
                    project_config={'parameters': {'components': {'tools': {'config': {'enabled': ['long_action']}}}}},
                    component_factories=[lambda: RuntimeTools(ToolRegistry([tool]))],
                    engine_factories={'loop': lambda: ExplicitToolLoop(
                        completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
                try:
                    await runtime.start()
                    runtime.instruction_routing.assess = AsyncMock(return_value='redirect')
                    sid = await runtime.new_session()
                    await runtime.submit(sid, 'Run long_action')
                    await asyncio.wait_for(entered.wait(), 20)
                    session = runtime.sessions[sid]
                    handle, = await session.run.alist()
                    receipt = await runtime.steer(sid, handle.id, 'Change direction', ['root'], 'redirect-key')
                    await asyncio.wait_for(session.run.wait_idle(), 20)
                    self.assertTrue(cleaned.is_set())
                    self.assertEqual(executions, ['started'])
                    self.assertEqual(len(await session.run.alist()), 1)
                    self.assertEqual((await handle.ainstructions())[0].id, receipt)
                    final = await handle.aget_data()
                    if effect == ToolEffect.READ_ONLY:
                        self.assertEqual(final.status, RunStatus.COMPLETED, final.error)
                        self.assertIn('Change direction', str(provider.requests[-1]['messages']))
                    else:
                        self.assertEqual(final.status, RunStatus.FAILED)
                        self.assertEqual(len(provider.requests), 1)
                        with self.assertRaises(ValueError):
                            await session.run.resume(handle.id, agent_id='assistant')
                    snapshot = await runtime.snapshot()
                    self.assertTrue(any(m.instructions for m in snapshot.messages))
                finally:
                    await runtime.close()
