"""Provider completion phases remain visible before and between text chunks."""

from llm.providers.litellm import LiteLLMCompletionAdapter

import asyncio
from dataclasses import asdict, replace
import tempfile
import threading
from types import SimpleNamespace
import unittest

from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.execution.thinking import ThinkingPhases
from hub.backend.execution.recovery_model import RecoveryStepMetadata
from hub.config.theme import HubTheme
from hub.locales import Language
from hub.model import ChatMessage, ResponseActivity, RetrySummary, TraceItem
from hub.widget.feedback.retry_status import retry_text
from llm.engines.events import EngineEvent, EngineEventType
from llm.components.memory.processing import MemorySession
from llm.components.memory.tool_result_values import ToolSummaryExecution
from hub.ui.presentation import present, present_message
from hub.widget.transcript.conversation import ConversationControl, render_messages
from llm.components.tools import ToolComponent
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel
from llm.core.results import CompletionResult
from llm.core.models import now


class CompletionDisplayTests(unittest.IsolatedAsyncioTestCase):
    def test_compaction_caption_uses_selected_icon_and_comment_color(self):
        from hub.asset.icon import for_style
        from hub.config.theme_style import ThemeSelector
        from hub.widget.transcript.thought_trace import render_trace_item
        theme = HubTheme(comment='#456789')
        for style, glyph in (('nerd',''), ('unicode','⇲')):
            language = Language('ko', icons=for_style(style))
            rows = render_trace_item(TraceItem('summary',TraceItem.SUMMARY,''),80,theme,language)
            fragments = [part for row in rows for part in row if part[1].strip()]
            self.assertEqual(''.join(part[1] for part in fragments).strip(), glyph+' 컨텍스트 압축')
            self.assertTrue(all(part[0] == ThemeSelector.MUTED.style for part in fragments))
            activity = retry_text(RetrySummary(1,4,kind=RetrySummary.RECOVERY,phase=RetrySummary.COMPACTING),language)
            rows = render_messages((ChatMessage('assistant','',activity=activity,status='streaming'),),80,theme,language,section='header')
            fragment = next(part for row in rows for part in row if '컨텍스트 압축' in part[1])
            self.assertIn('fg:#456789',fragment[0])

    def test_reasoning_heading_contains_only_agent_name_and_title(self):
        message = ChatMessage('reasoning', 'Visible thought', author='도우미', status='running',
                              phase='thinking', time='2026-10-27 10:00', elapsed_seconds=42)
        rows = render_messages((message,), 80, HubTheme(), Language('ko'), section='header')
        self.assertEqual(''.join(part[1] for row in rows for part in row).strip(), '도우미 • 생각 과정')

    async def test_empty_completed_answer_keeps_received_reasoning_after_reload(self):
        def model(**request):
            yield {'choices':[{'index':0, 'delta':{'reasoning_content':'Visible provider reasoning'},
                               'finish_reason':'stop'}]}
        with tempfile.TemporaryDirectory() as root:
            config = HubConfig(root, model='test/empty', auto_title=False,
                project_config={'parameters':{'engines':{'loop':{'policy':{'final_review':None}}}}},
                engine_factories={'loop':lambda: LoopEngine(
                    completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))})
            runtime = HubRuntime(config)
            await runtime.start()
            sid = await runtime.new_session()
            run, = await (await runtime.sessions[sid].run.submit('Hello', recipients=['assistant'])).wait(timeout=20)
            self.assertEqual(run.data.status, 'completed')
            for restarted in (False, True):
                if restarted:
                    await runtime.close()
                    runtime = HubRuntime(config)
                    await runtime.start()
                    await runtime.select(sid)
                try:
                    snapshot = await runtime.snapshot()
                    self.assertTrue(any(m.role == 'reasoning' and 'Visible provider reasoning' in m.text
                                        for m in snapshot.messages))
                    self.assertIn('최종 답변 본문', present_message(snapshot.messages[-1], runtime.t).text)
                finally:
                    if restarted:
                        await runtime.close()

    def test_memory_summary_activity_is_not_error_recovery(self):
        phases = ThinkingPhases()
        run = SimpleNamespace(id='summary-run', status='running', metadata={})
        phases.observe(run, EngineEvent(EngineEventType.STEP_STARTED, step_id='s',
                                       kind='memory', name=MemorySession.ACTIVE_STEP))
        phases.observe(run, EngineEvent(EngineEventType.COMPLETION, completion=CompletionResult()))
        self.assertEqual(phases.activity(run), ResponseActivity.SUMMARIZING)
        self.assertEqual(Language('ko').status(ResponseActivity.SUMMARIZING), '요약 중')
        label = retry_text(RetrySummary(1, 4, kind=RetrySummary.RECOVERY,
                                       phase=RetrySummary.COMPACTING), Language('ko'))
        self.assertIn('컨텍스트 압축', label)
        self.assertNotIn('복구', label)
        phases.observe(run, EngineEvent(EngineEventType.STEP_COMPLETED, step_id='s'))
        self.assertNotEqual(phases.activity(run), ResponseActivity.SUMMARIZING)

    def test_summary_helpers_do_not_leak_into_agent_reasoning(self):
        for name, kind, metadata in (
                (MemorySession.SUMMARIZE_STEP, 'memory', {}),
                (MemorySession.ACTIVE_STEP, 'memory', {}),
                (ToolSummaryExecution.STEP, 'memory', {}),
                ('custom label', 'llm', RecoveryStepMetadata(RecoveryStepMetadata.SUMMARY).to_record())):
            with self.subTest(name=name):
                phases = ThinkingPhases()
                run = SimpleNamespace(id='r', status='running', metadata={'completions': []})
                phases.observe(run, EngineEvent(EngineEventType.STEP_STARTED, step_id='helper',
                    kind=kind, name=name, metadata=metadata))
                phases.observe(run, EngineEvent(EngineEventType.COMPLETION, step_id='helper',
                    completion=CompletionResult(id='hidden', step_id='helper', reasoning_content='Internal recovery details')))
                self.assertEqual(phases.activity(run), ResponseActivity.SUMMARIZING)
                self.assertEqual(phases.visible('r', '', active=True, content=''), '')
                self.assertEqual(phases.trace('r', ()), ())
                phases.observe(run, EngineEvent(EngineEventType.STEP_COMPLETED, step_id='helper'))
                self.assertNotEqual(phases.activity(run), ResponseActivity.SUMMARIZING)
                phases.observe(run, EngineEvent(EngineEventType.COMPLETION,
                    completion=CompletionResult(id='agent', step_id='main', reasoning_content='Continue the actual task')))
                self.assertEqual(phases.visible('r', '', active=True, content=''), 'Continue the actual task')

    async def test_real_stream_shows_thinking_then_each_chunk_without_waiting_for_finish(self):
        gates = [threading.Event() for _ in range(4)]
        entered = threading.Event()
        def completion(**request):
            self.assertTrue(request['stream'])
            entered.set()
            chunks = [({'reasoning_content': 'Visible summary'}, None),
                      ({'content': 'First block'}, None),
                      ({'reasoning_content': 'Second summary'}, None),
                      ({'content': ' and second block'}, 'stop')]
            for gate, (delta, finish) in zip(gates, chunks):
                if not gate.wait(15):
                    raise TimeoutError('Test did not release provider chunk')
                yield {'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
        def factory(config):
            return LargeLanguageModel(config.workspace, components=[ToolComponent()],
                engines={'loop': LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=completion))})
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(directory, auto_title=False, agent_definition={
                'engine': 'loop', 'completion': {'model': 'test/model'},
                'metadata': {'title': '코딩 도우미'},
            }), factory)
            async def observe(predicate):
                async with asyncio.timeout(15):
                    while True:
                        snapshot = await runtime.snapshot()
                        if predicate(snapshot):
                            return snapshot
                        await asyncio.sleep(.01)
            try:
                await runtime.start()
                await runtime.new_session()
                await runtime.submit(runtime.selected_id, 'task')
                snapshot = await observe(lambda s: entered.is_set() and s.messages[-1].phase == 'thinking')
                projected = present(snapshot, runtime.t)
                self.assertEqual(projected.messages[-1].phase, 'thinking')
                self.assertEqual(projected.messages[-1].text, '')
                header = render_messages(projected.messages[-1:], 80, HubTheme(), runtime.t, section="header")
                self.assertIn('생각 중', ''.join(text for line in header for _, text in line))
                gates[0].set()
                snapshot = await observe(lambda s: any(m.role == 'reasoning' for m in s.messages))
                self.assertEqual(snapshot.messages[-2].text, 'Visible summary')
                self.assertEqual(snapshot.messages[-2].author, '코딩 도우미')
                reasoning_header = render_messages(snapshot.messages[-2:-1], 80, HubTheme(), runtime.t, section='header')
                heading = ''.join(text for line in reasoning_header for _, text in line)
                self.assertIn('코딩 도우미 • 생각 과정', heading)
                self.assertNotIn('test/model', heading)
                gates[1].set()
                snapshot = await observe(lambda s: s.messages[-1].text == 'First block')
                self.assertEqual(snapshot.messages[-2].role, 'reasoning')
                self.assertEqual(snapshot.messages[-2].text, 'Visible summary')
                self.assertEqual(snapshot.messages[-1].phase, 'streaming')
                self.assertTrue(snapshot.run.active)
                gates[2].set()
                snapshot = await observe(lambda s: any('Second summary' in m.text for m in s.messages))
                self.assertIn('Visible summary', snapshot.messages[-2].text)
                self.assertEqual(snapshot.messages[-2].role, 'reasoning')
                self.assertEqual(snapshot.messages[-1].phase, 'thinking')
                gates[3].set()
                await runtime.sessions[runtime.selected_id].run.wait_idle()
                snapshot = await runtime.snapshot()
                self.assertEqual(snapshot.messages[-1].text, 'First block and second block')
                self.assertEqual(snapshot.messages[-1].phase, '')
                self.assertFalse(any(m.role == 'reasoning' for m in snapshot.messages))
            finally:
                for gate in gates:
                    gate.set()
                await runtime.close()

    def test_phase_only_refresh_updates_header_without_rendering_body_again(self):
        message = ChatMessage('assistant', '', status='streaming', id='a', phase='preparing')
        control = ConversationControl((message,), HubTheme())
        try:
            control.create_content(80, 20)
            body_renders = control._message_cache.body_renders
            control.messages = (replace(message, phase='thinking'),)
            control.create_content(80, 20)
            rendered = '\n'.join(''.join(fragment[1] for fragment in row) for row in control._lines)
            self.assertIn('생각 중', rendered)
            self.assertNotIn('준비 중', rendered)
            self.assertEqual(control._message_cache.body_renders, body_renders)
            projected = present_message(control.messages[0], Language())
            self.assertEqual(projected.status, 'streaming')
        finally:
            control.renderers.close()

    def test_reconciliation_and_tool_gap_do_not_claim_preparation(self):
        phases = ThinkingPhases()
        run = SimpleNamespace(id='r', status='running', metadata={'completions': [asdict(CompletionResult())]})
        self.assertEqual(phases.activity(run), 'thinking')
        run.metadata['completions'][-1]['status'] = 'completed'
        run.metadata['completions'][-1]['ended_at'] = now()
        self.assertEqual(phases.activity(run), 'running')
        run.status = 'failed'
        self.assertEqual(phases.activity(run), '')
