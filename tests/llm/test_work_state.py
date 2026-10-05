"""구조화 문맥은 파생 캐시이며 원본/Goal 버전/토큰 예산을 대체하지 않는다."""

import json
import unittest
from copy import deepcopy
from types import SimpleNamespace

from tests.llm import test_memory_processing as memory_tests
from tests.llm.test_goals import goal
from llm.components.goals import GoalComponent
from llm.components.memory.processing import MemorySession
from llm.components.memory.work_state import FIELDS
from llm.components.processing import CompletionRequest, CompletionMessage
from llm.policies import CompletionPolicy
from llm.core.models import RunStatus


def work_state():
    return {"objective": "Finish safely", **{name: [name + " evidence"] for name in FIELDS}}


class WorkStateTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = memory_tests.MemoryProcessingTests.asyncSetUp
    configure = memory_tests.MemoryProcessingTests.configure
    run_loop = memory_tests.MemoryProcessingTests.run_loop
    build_history = memory_tests.MemoryProcessingTests.build_history

    def model(self, **request):
        self.requests.append(deepcopy(request))
        yield from memory_tests.answer(json.dumps({"work_state": work_state()}))

    async def prepare_work(self):
        await self.build_history()
        self.requests = []
        self.component.completion_fn = self.model
        await self.configure(summarize=True, summary_format="work_state", keep_turns=1, summary_after_chars=1)

    async def test_structured_state_provenance_and_original_history(self):
        await self.prepare_work()
        original = await self.session.aconversation()
        run, model = await self.run_loop("current request")
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        summary = await self.memory.asummary(self.session.id)
        self.assertEqual(summary["metadata"]["work_state"], work_state())
        self.assertEqual(summary["metadata"]["summary_format_version"], 1)
        self.assertEqual(summary["metadata"]["sources"]["messages"][0], original[0].id)
        self.assertIn(original[1].run_id, summary["metadata"]["sources"]["runs"])
        sent = model.requests[0]["messages"]
        self.assertEqual(sent[0]["content"], "third work")
        self.assertIn("current request", sent[-1]["content"])
        self.assertEqual((await self.session.aconversation())[:len(original)], original)

    async def test_goal_revision_invalidates_cache_and_is_only_a_reference(self):
        self.app.project_manager.components.register(GoalComponent())
        await self.project.components.aselect(["memory", "goals"])
        goals = self.project.components.goals
        await goals.acreate(goal(), identifier="work")
        await self.prepare_work()
        await self.configure(summarize=True, summary_format="work_state", keep_turns=1,
                             summary_after_chars=1, goal_ids=["work"])
        await self.run_loop()
        prior = await self.memory.asummary(self.session.id)
        snapshot = await goals.asnapshot("work")
        await goals.aupdate("work", {"objective": "Changed objective"}, expected_version=snapshot["version"])
        run, _ = await self.run_loop()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        current = await self.memory.asummary(self.session.id)
        self.assertNotEqual(prior["metadata"]["profile"], current["metadata"]["profile"])
        ref = current["metadata"]["sources"]["goals"][0]
        self.assertEqual(ref["objective"], "Changed objective")
        self.assertNotIn("run_refs", ref)
        payload = json.loads(self.requests[-1]["messages"][1]["content"])
        self.assertEqual(payload["previous_summary"], "")

    async def test_goal_changed_during_summary_does_not_publish(self):
        self.app.project_manager.components.register(GoalComponent())
        await self.project.components.aselect(["memory", "goals"])
        goals = self.project.components.goals
        await goals.acreate(goal(), identifier="work")
        await self.prepare_work()
        await self.configure(summarize=True, summary_format="work_state", keep_turns=1, summary_after_chars=1,
                             goal_ids=["work"], failure_mode="continue")
        def mutate(**request):
            snapshot = goals.snapshot("work")
            goals.update("work", {"objective": "Changed concurrently"}, expected_version=snapshot["version"])
            yield from self.model(**request)
        self.component.completion_fn = mutate
        run, model = await self.run_loop()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertIsNone(await self.memory.asummary(self.session.id))
        self.assertEqual(model.requests[0]["messages"][0]["content"], "first requirement")

    async def test_invalid_work_state_retains_raw_context(self):
        await self.prepare_work()
        await self.configure(summarize=True, summary_format="work_state", keep_turns=1,
                             summary_after_chars=1, failure_mode="continue")
        self.component.completion_fn = lambda **kw: iter(memory_tests.answer('{"work_state":{"completed":"unsupported"}}'))
        run, model = await self.run_loop()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertIsNone(await self.memory.asummary(self.session.id))
        self.assertEqual(model.requests[0]["messages"][0]["content"], "first requirement")

    async def test_token_threshold_uses_full_request_counter_without_guessing(self):
        seen = []
        def counter(request):
            seen.append(request)
            return 90
        processor = MemorySession(SimpleNamespace(data=None), SimpleNamespace(
            completion_policy=CompletionPolicy(200, counter=counter)))
        processor.config = {"summary_after_tokens": 80}
        request = CompletionRequest({"tools": [{"name": "test"}]}, [CompletionMessage({"role": "user", "content": "tiny"})], 0)
        self.assertTrue(await processor._threshold(request, 1))
        self.assertIn("tools", seen[0])
        processor.context.completion_policy = None
        with self.assertRaisesRegex(ValueError, "counter"):
            await processor._threshold(request, 90000)
        processor.config["summary_after_chars"] = 100
        self.assertFalse(await processor._threshold(request, 99))

    async def test_incomplete_tool_pair_and_steering_are_not_compacted(self):
        processor = MemorySession(SimpleNamespace(data=None), SimpleNamespace(
            run=SimpleNamespace(input_message_id="input", id="run"), completion_policy=None))
        processor.config = {"active_keep_iterations": 1, "summary_after_chars": 1}
        original = [CompletionMessage({"role": "user", "content": "request"}, "input"),
            CompletionMessage({"role": "assistant", "tool_calls": [{"id": "pending"}]}),
            CompletionMessage({"role": "assistant", "tool_calls": [{"id": "current"}]})]
        for steering in (False, True):
            messages = deepcopy(original)
            if steering:
                messages.insert(2, CompletionMessage({"role": "user", "content": "Do not delete"}, "steer"))
            request = CompletionRequest({}, deepcopy(messages), 0)
            async for _ in processor._compact_active(request, {}, {}):
                pass
            self.assertEqual(request.messages, messages)
