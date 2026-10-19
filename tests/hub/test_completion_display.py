"""Provider completion phases remain visible before and between text chunks."""
from llm.providers.litellm import LiteLLMCompletionAdapter

import asyncio
from dataclasses import replace
import tempfile
import threading
from types import SimpleNamespace
import unittest

from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.execution.thinking import ThinkingPhases
from hub.config.theme import HubTheme
from hub.locales import Language
from hub.model import ChatMessage
from hub.ui.presentation import present, present_message
from hub.widget.transcript.conversation import ConversationControl, render_messages
from llm.components.tools import ToolComponent
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel


class CompletionDisplayTests(unittest.IsolatedAsyncioTestCase):
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
            runtime = HubRuntime(HubConfig(directory, model='test/model', auto_title=False), factory)
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
        run = SimpleNamespace(id='r', status='running', metadata={'completions': [{'status': 'running'}]})
        self.assertEqual(phases.activity(run), 'thinking')
        run.metadata['completions'][-1]['status'] = 'completed'
        self.assertEqual(phases.activity(run), 'running')
        run.status = 'failed'
        self.assertEqual(phases.activity(run), '')
