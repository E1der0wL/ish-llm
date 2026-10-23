"""Failed turns remain durable but are omitted from subsequent model inputs."""

from llm.providers.litellm import LiteLLMCompletionAdapter

from copy import deepcopy
import tempfile
import unittest

from hub.backend.execution.context import HubContextBuilder
from hub.backend.runtime import HubConfig, HubRuntime
from llm.core.models import Message, MessageRole, MessageStatus
from llm.engines.loop import LoopEngine
from llm.services.history.context import ContextPolicy
from tests.llm.test_loop import ScriptedCompletion, chunk



from tests.llm.support.admission import uid, admitted_message
from tests.llm.support.continuation import resume_record

message = admitted_message


class ContextTests(unittest.IsolatedAsyncioTestCase):
    def test_filters_before_budget_and_retains_clone_records(self):
        builder = HubContextBuilder()
        history = [message(uid('good'), "user", "committed", uid('r1')),
                   message(uid('answer'), "assistant", "completed", uid('r1')),
                   message(uid('bad'), "user", "committed", uid('r2')),
                   message(uid('partial'), "assistant", "failed", uid('r2')),
                   message(uid('now'), "user", "committed", uid('r3'))]
        original = deepcopy(history)
        for data in (history, builder.for_clone(history)):
            for policy in (None, ContextPolicy(mode="recent", max_turns=1),
                           ContextPolicy(mode="budget", max_chars=13)):
                self.assertEqual([m.id for m in builder.for_run(data, uid('now'), policy=policy)],
                                 [uid('good'), uid('answer'), uid('now')])
        self.assertEqual(history, original)
        self.assertEqual(len(builder.for_clone(history)), 5)
        for status in ("interrupted", "cancelled", "paused", "streaming", "queued", "committed"):
            history[3].status = MessageStatus(status)
            self.assertEqual([m.id for m in builder.for_run(history, uid('now'))], [uid('good'), uid('answer'), uid('now')])
        self.assertEqual([m.id for m in builder.for_run([*history[:2], history[2], history[4]], uid('now'))],
                         [uid('good'), uid('answer'), uid('now')])

    def test_completed_checkpoint_lineage_and_current_request_are_preserved(self):
        builder = HubContextBuilder()
        history = [message(uid('u1'), "user", "committed", uid('r1')), message(uid('a1'), "assistant", "paused", uid('r1')),
                   message(uid('u2'), "user", "committed", uid('r2')), message(uid('a2'), "assistant", "completed", uid('r2')),
                   message(uid('now'), "user", "committed", uid('r3'))]
        history[2].metadata["resume"] = resume_record(run_id=uid('r1'))
        self.assertEqual([m.id for m in builder.for_run(history, uid('now'))], [m.id for m in history])
        history[3].status = MessageStatus.INTERRUPTED
        self.assertEqual([m.id for m in builder.for_run(history, uid('now'))], [uid('now')])
        self.assertEqual([m.id for m in builder.for_run(history, uid('u2'))], [uid('u1'), uid('a1'), uid('u2')])

    def test_orders_queued_requests_before_grouping_failed_pairs(self):
        data = [message(uid('failed-user'), "user", "committed", uid('r1')),
                message(uid('next-user'), "user", "committed", uid('r2')),
                message(uid('partial'), "assistant", "failed", uid('r1'))]
        self.assertEqual([m.id for m in HubContextBuilder().for_run(data, uid('next-user'))], [uid('next-user')])

    async def test_real_loop_failure_reopen_and_new_request(self):
        provider = ScriptedCompletion([chunk("keep", finish="stop")],
            [chunk("bad partial"), RuntimeError("provider unavailable")], [chunk("new answer", finish="stop")])
        with tempfile.TemporaryDirectory() as root:
            config = HubConfig(root, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))})
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
