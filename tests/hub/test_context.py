"""Failed turns remain durable but are omitted from subsequent model inputs."""

from copy import deepcopy
import tempfile
import unittest

from hub.backend.context import HubContextBuilder
from hub.backend.runtime import HubConfig, HubRuntime
from llm.core.models import Message, MessageRole, MessageStatus
from llm.engines.loop import LoopEngine
from llm.services.history.context import ContextPolicy
from tests.llm.test_loop import ScriptedCompletion, chunk


def message(identifier, role, status, run_id=None):
    return Message(id=identifier, role=MessageRole(role), content=identifier,
                   status=MessageStatus(status), run_id=run_id)


class ContextTests(unittest.IsolatedAsyncioTestCase):
    def test_filters_before_budget_and_retains_clone_records(self):
        builder = HubContextBuilder()
        history = [message("good", "user", "committed", "r1"),
                   message("answer", "assistant", "completed", "r1"),
                   message("bad", "user", "committed", "r2"),
                   message("partial", "assistant", "failed", "r2"),
                   message("now", "user", "committed", "r3")]
        original = deepcopy(history)
        for data in (history, builder.for_clone(history)):
            for policy in (None, ContextPolicy(mode="recent", max_turns=1),
                           ContextPolicy(mode="budget", max_chars=13)):
                self.assertEqual([m.id for m in builder.for_run(data, "now", policy=policy)],
                                 ["good", "answer", "now"])
        self.assertEqual(history, original)
        self.assertEqual(len(builder.for_clone(history)), 5)
        history[3].status = MessageStatus.INTERRUPTED
        self.assertEqual(len(builder.for_run(history, "now")), 5)

    def test_orders_queued_requests_before_grouping_failed_pairs(self):
        data = [message("failed-user", "user", "committed", "r1"),
                message("next-user", "user", "committed", "r2"),
                message("partial", "assistant", "failed", "r1")]
        self.assertEqual([m.id for m in HubContextBuilder().for_run(data, "next-user")], ["next-user"])

    async def test_real_loop_failure_reopen_and_new_request(self):
        provider = ScriptedCompletion([chunk("keep", finish="stop")],
            [chunk("bad partial"), RuntimeError("provider unavailable")], [chunk("new answer", finish="stop")])
        with tempfile.TemporaryDirectory() as root:
            config = HubConfig(root, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_fn=provider)})
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                sid = await runtime.new_session(title="Context")
                for text in ("successful request", "failed request"):
                    await runtime.submit(sid, text)
                    await runtime.sessions[sid].run.wait_idle()
                self.assertEqual((await runtime.snapshot()).run.status, "failed")
            finally:
                await runtime.close()
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                await runtime.submit(sid, "current request")
                await runtime.sessions[sid].run.wait_idle()
                contents = [m.get("content") for m in provider.requests[-1]["messages"]]
                self.assertIn("successful request", contents)
                self.assertIn("keep", contents)
                self.assertIn("current request", contents)
                self.assertNotIn("failed request", contents)
                self.assertNotIn("bad partial", contents)
                stored = await runtime.sessions[sid].aconversation()
                self.assertIn("failed request", [m.content for m in stored])
                self.assertTrue(any(str(m.status) == "failed" for m in stored))
            finally:
                await runtime.close()
