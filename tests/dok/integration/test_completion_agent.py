"""Offline dok adapter checks against the optional Hub transport record contracts."""
import asyncio
from importlib.util import find_spec
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from dok.features.completion.analysis import HelpAnalysis
from dok.integrations.completion_agent import CompletionAgent
from dok.locales import Locale


@unittest.skipUnless(find_spec("hub") and find_spec("llm"), "optional Hub + llm contracts")
class CompletionAgentTests(unittest.IsolatedAsyncioTestCase):
    def batch(self, *, terminal=False, outputs=()):
        from hub.backend.plugins.protocol import PluginBatch, PluginRun, PluginOutput, PluginOutputKind
        from llm.core.models import MessageStatus, RunStatus
        run = PluginRun("run", HelpAnalysis.AGENT_ID, "loop",
                        RunStatus.COMPLETED if terminal else RunStatus.RUNNING, None, None, (), ())
        return PluginBatch("request", MessageStatus.COMPLETED if terminal else MessageStatus.COMMITTED,
            (run,), tuple(PluginOutput("run", HelpAnalysis.AGENT_ID, PluginOutputKind.OUTPUT, item.to_dict())
                          for item in outputs), {}, 1, terminal).to_dict()

    def request(self, batch):
        async def events():
            yield batch
            if not batch["terminal"]:
                await asyncio.Event().wait()
        return SimpleNamespace(events=events, cancel=AsyncMock(return_value=False), interrupt=AsyncMock())

    def client(self, request):
        from hub.backend.plugins.protocol import PluginSession
        return SimpleNamespace(open_session=AsyncMock(return_value=PluginSession("project", "session").to_dict()),
                               submit=AsyncMock(return_value=request))

    async def test_only_final_public_run_output_is_decoded_and_context_is_not_silently_cut(self):
        from llm.core.results import EngineOutput, OutputVisibility
        source = "Example " * 3000 + "\n  --all  All records\n"
        answer = json.dumps({"items": [{"name": "--all", "evidence": "  --all  All records"}]})
        request = self.request(self.batch(terminal=True, outputs=(
            EngineOutput(text="not JSON", final=False),
            EngineOutput(text="not JSON", step_id="step"),
            EngineOutput(text="not JSON", visibility=OutputVisibility.INTERNAL),
            EngineOutput(text=answer))))
        client = self.client(request)
        result = await CompletionAgent(SimpleNamespace(client=lambda: client)).analyze(source, 1)
        self.assertEqual([item.name for item in result.items], ["--all"])
        supplied = client.submit.call_args.kwargs
        self.assertEqual(supplied["recipients"], [HelpAnalysis.AGENT_ID])
        self.assertEqual(supplied["context"].text, source)
        self.assertEqual(supplied["context"].max_chars, len(source))
        request.cancel.assert_not_awaited()
        request.interrupt.assert_not_awaited()

    async def test_timeout_cancels_request_and_interrupts_only_its_active_run(self):
        request = self.request(self.batch())
        client = self.client(request)
        with self.assertRaises(TimeoutError):
            await CompletionAgent(SimpleNamespace(client=lambda: client)).analyze("--all", .01)
        request.cancel.assert_awaited_once()
        request.interrupt.assert_awaited_once_with("run")

    async def test_each_analysis_requests_current_language_and_preserves_literal_evidence(self):
        from llm.core.results import EngineOutput
        from dok.features.completion.models import Argument, Candidate, Join, Analysis

        source = "  --color=always  Always use color"
        selected = [Locale.resolve("ko")]
        client = self.client(None)
        agent = CompletionAgent(SimpleNamespace(client=lambda: client), locale=lambda: selected[0])
        for language, explanation in (("ko", "항상 색상을 사용"), ("en", "Always use color")):
            selected[0] = Locale.resolve(language)
            expected = Candidate("--color", explanation, argument=Argument.REQUIRED,
                                 join=Join.EQUAL, values=("always",), evidence=source)
            answer = json.dumps(Analysis((expected,)).to_dict(), ensure_ascii=False)
            client.submit.return_value = self.request(self.batch(terminal=True, outputs=(EngineOutput(text=answer),)))
            result = await agent.analyze(source, 1)
            instruction = client.submit.call_args.args[1]
            self.assertIn(f"Explanation language (ISO 639-1): {language}.", instruction)
            self.assertIn("Translate only the help field", instruction)
            self.assertIn("Keep name, values, and evidence exactly", instruction)
            self.assertEqual(result.items, (expected,))
            self.assertEqual(client.submit.call_args.kwargs["context"].text, source)

    async def test_cancel_during_admission_keeps_receipt_and_drains_repeated_cancellation(self):
        request = self.request(self.batch())
        client = self.client(request)
        admitting, admitted, stopping, stopped = (asyncio.Event() for _ in range(4))

        async def submit(*args, **kwargs):
            admitting.set()
            await admitted.wait()
            return request

        async def cancel():
            stopping.set()
            await stopped.wait()
            return False

        client.submit.side_effect = submit
        request.cancel.side_effect = cancel
        task = asyncio.create_task(CompletionAgent(SimpleNamespace(client=lambda: client)).analyze("--all", 1))
        try:
            await asyncio.wait_for(admitting.wait(), 1)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            admitted.set()
            await asyncio.wait_for(stopping.wait(), 1)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
        finally:
            admitted.set()
            stopped.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        request.cancel.assert_awaited_once()
        request.interrupt.assert_awaited_once_with("run")
