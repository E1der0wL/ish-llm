"""공통 출력 계약: 저장 후 알림, 재조회, 중단, 병렬 Agent 및 결과 소유권."""
import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from llm.core.models import RunStatus
from llm.core.results import EngineOutput, EngineDelta
from llm.engines.base import BaseEngine, EngineEvent, EngineEventType
from llm.engines.graph.agent import AgentNode
from llm.engines.graph import GraphEngine
from llm.engines.loop import LoopEngine
from llm.engines.pipeline import PipelineEngine
from llm.core.models import new_id
from llm.components.workflows import WorkflowGraph
from llm.llm import LargeLanguageModel
from tests.llm.test_loop import ScriptedCompletion, chunk


class OutputValueTests(unittest.TestCase):
    def test_json_roundtrip_copy_and_validation(self):
        data = {"items": [1, {"ok": True}], "future_field": None}
        output = EngineOutput("id", text="한글", data=data, metadata={"custom": 1})
        data["items"].clear()
        self.assertEqual(len(output.data["items"]), 2)
        encoded = json.loads(json.dumps(output.to_dict()))
        self.assertEqual(EngineOutput.from_dict(encoded), output)
        encoded["data"]["items"].clear()
        self.assertEqual(len(output.data["items"]), 2)
        for value in (object(), float("nan"), {"value": object()}):
            with self.assertRaises((ValueError, TypeError)):
                EngineOutput(data=value)
        with self.assertRaises(ValueError):
            EngineDelta("id", "text", visibility="unknown")
        # 새 값의 자동 ID와 저장된 값의 복원은 구분한다. 손상된 ID를 발명하지 않는다.
        for codec in (EngineDelta, EngineOutput):
            with self.assertRaises(ValueError):
                codec.from_dict({"text": "missing identity"})

    def test_apply_replace_identity_and_order(self):
        output = EngineOutput("id", final=False)
        delta = EngineDelta("id", "one", sequence=1)
        self.assertEqual(EngineDelta.from_dict(delta.to_dict()), delta)
        output = output.apply(delta).apply(EngineDelta("id", "two", operation="replace", sequence=3))
        self.assertEqual((output.text, output.sequence), ("two", 3))
        for delta in (EngineDelta("other", "bad", sequence=4),
                      EngineDelta("id", "bad", sequence=3),
                      EngineDelta("id", "bad", sequence=0),
                      EngineDelta("id", "bad", visibility="internal", sequence=4)):
            with self.assertRaises(ValueError):
                output.apply(delta)
        with self.assertRaises(ValueError):
            replace(output, final=True).apply(EngineDelta("id", "bad", sequence=4))


class OutputIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    async def backend(self, engine, *, storage="file"):
        self.app = LargeLanguageModel(self.root, engines={"test": engine})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("outputs", conversation_storage=storage)
        self.session = await self.project.sessions.acreate("session")

    async def request(self):
        return await (await self.session.run.submit("go", engine="test")).wait(timeout=20)

    async def test_structured_base_output_final_query_and_reopen(self):
        async def work(context):
            yield "draft"
            yield EngineOutput(text="final", data={"items": [1, 2]}, metadata={"format": "example"})
        await self.backend(BaseEngine(action=work))
        seen, errors = [], []
        def observe(run, event):
            value = event.delta or event.output
            if value is not None:
                try:
                    stored = self.app.run_repository.output_events(run, after=value.sequence - 1)
                    self.assertEqual(stored[0], value)
                    seen.append(value)
                except Exception as error:
                    errors.append(error)
        self.app.events.subscribe(observe)
        run = await self.request()
        self.assertEqual((await run.aresult()).status, RunStatus.COMPLETED)
        result = await run.aresult()
        self.assertEqual((result.output.text, result.output.data), ("final", {"items": [1, 2]}))
        self.assertIsNone(result.output.step_id)
        self.assertEqual((await run.aresponse()).content, "final")
        step, = await run.steps.alist()
        self.assertEqual(step.output.text, "final")
        self.assertEqual(step.output.step_id, step.id)
        self.assertEqual(errors, [])
        self.assertEqual([item.sequence for item in seen], [1, 2, 3])
        self.assertEqual(await run.aoutput_events(after=1, limit=1), [seen[1]])
        self.assertEqual(await run.aoutput_events(after=3), [])
        # 재시작은 저장된 출력을 읽으며 Engine을 다시 호출하지 않는다.
        project_id, session_id, run_id = self.project.id, self.session.id, run.id
        await self.app.shutdown()
        async with LargeLanguageModel(self.root) as reopened:
            session = await (await reopened.projects.aload(project_id)).sessions.aload(session_id)
            handle = await session.run.aload(run_id)
            self.assertEqual((await handle.aresult()).output, result.output)
            self.assertEqual(await handle.aoutput_events(), seen)

    async def test_partial_output_survives_interrupt_and_queue_continues(self):
        ready, release = asyncio.Event(), asyncio.Event()
        async def work(context):
            yield EngineDelta(text="partial")
            if context.messages[-1].content == "hold":
                ready.set()
                await release.wait()
            yield " finished"
        await self.backend(BaseEngine(action=work))
        first = await self.session.run.submit("hold", engine="test")
        await asyncio.wait_for(ready.wait(), 10)
        queued = await self.session.run.submit("next", engine="test")
        run = (await self.session.run.alist())[0]
        partial, = await run.aoutputs()
        self.assertEqual((partial.text, partial.final), ("partial", False))
        await self.session.run.interrupt()
        first_run, second_run = await first.wait(timeout=10), await queued.wait(timeout=10)
        self.assertEqual((await first_run.aresult()).status, RunStatus.INTERRUPTED)
        self.assertIsNone((await first_run.aresult()).output)
        self.assertEqual((await first_run.aoutputs())[0], partial)
        self.assertEqual((await second_run.aresult()).output.text, "partial finished")

    async def test_replace_delta_and_truncated_tail(self):
        class ReplaceEngine:
            async def execute(self, context):
                yield EngineEvent(EngineEventType.TEXT_DELTA, delta=EngineDelta("answer", "old"))
                yield EngineEvent(EngineEventType.TEXT_DELTA,
                                  delta=EngineDelta("answer", "new", operation="replace"))
                yield EngineEvent(EngineEventType.OUTPUT, output=EngineOutput("answer", text="new"))
        await self.backend(ReplaceEngine(), storage="memory")
        run = await self.request()
        self.assertEqual((await run.aresponse()).content, "new")
        self.assertEqual((await run.aoutputs())[0].text, "new")
        path = run.data.paths.state / "outputs.jsonl"
        with path.open("ab") as stream:
            stream.write(b'{"type":')
        self.assertEqual(len(await run.aoutput_events()), 3)
        self.assertFalse(self.session.data.paths.conversation.exists())
        for kwargs in ({"after": -1}, {"limit": -1}, {"after": True}):
            with self.assertRaises(ValueError):
                await run.aoutput_events(**kwargs)

    async def test_base_typed_delta_persists_replace_and_reconstructs_final_text(self):
        async def work(context):
            yield EngineDelta(text="draft")
            yield EngineDelta(text="revised", operation="replace")
            yield " answer"
        await self.backend(BaseEngine(action=work))
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual((await run.aresult()).output.text, "revised answer")
        self.assertEqual((await run.aresponse()).content, "revised answer")
        steps = await run.steps.alist()
        self.assertEqual(steps[0].output.text, "revised answer")
        deltas = [item for item in await run.aoutput_events() if isinstance(item, EngineDelta)]
        self.assertEqual([item.operation for item in deltas], ["append", "replace", "append"])
        self.assertTrue(all(item.step_id == steps[0].id for item in deltas))

    async def test_visibility_switch_fails_without_exposing_internal_text(self):
        async def work(context):
            yield EngineDelta(text="internal", visibility="internal")
            yield EngineDelta(text="public")
        await self.backend(BaseEngine(action=work))
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual((await run.aresponse()).content, "")
        recorded, = await run.aoutput_events()
        self.assertEqual((recorded.text, recorded.visibility), ("internal", "internal"))

    async def test_mistyped_step_output_is_rejected_before_journal_write(self):
        class BadEngine:
            async def execute(self, context):
                step_id = new_id()
                yield EngineEvent(EngineEventType.STEP_STARTED, step_id=step_id)
                yield EngineEvent(EngineEventType.STEP_COMPLETED, step_id=step_id,
                                  output=EngineDelta(text="invalid", step_id=step_id))
        await self.backend(BadEngine())
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(await run.aoutput_events(), [])

    async def test_output_identity_cannot_be_changed(self):
        class BadEngine:
            async def execute(self, context):
                yield EngineEvent(EngineEventType.TEXT_DELTA, delta=EngineDelta("answer", "valid"))
                yield EngineEvent(EngineEventType.OUTPUT,
                                  output=EngineOutput("answer", text="invalid", visibility="internal"))
        await self.backend(BadEngine())
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertIsNone((await run.aresult()).output)
        self.assertEqual((await run.aoutputs())[0].text, "valid")

    async def test_pipeline_preserves_stage_results_and_uses_last_result(self):
        async def first(context):
            yield "intermediate"
        async def second(context):
            return EngineOutput(text="final", data={"verified": True})
        await self.backend(PipelineEngine([BaseEngine(action=first), BaseEngine(action=second)]))
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual((await run.aresult()).output.data, {"verified": True})
        stages = [step for step in await run.steps.alist() if step.kind == "engine"]
        self.assertEqual([step.output.text for step in stages], ["intermediate", "final"])
        self.assertEqual((await run.aresponse()).content, "final")
        roots = [item for item in await run.aoutputs() if item.step_id is None]
        self.assertEqual(len(roots), 1)

    async def test_closed_step_cannot_emit_more_deltas(self):
        class BadEngine:
            async def execute(self, context):
                step_id = new_id()
                yield EngineEvent(EngineEventType.STEP_STARTED, step_id=step_id)
                yield EngineEvent(EngineEventType.TEXT_DELTA, step_id=step_id,
                                  delta=EngineDelta(step_id, "valid", step_id=step_id))
                yield EngineEvent(EngineEventType.STEP_COMPLETED, step_id=step_id)
                yield EngineEvent(EngineEventType.TEXT_DELTA, step_id=step_id,
                                  delta=EngineDelta(step_id, "invalid", step_id=step_id))
        await self.backend(BadEngine())
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.FAILED)
        self.assertEqual(len(await run.aoutput_events()), 1)
        self.assertEqual((await run.aresponse()).content, "valid")

    async def test_dropped_ui_events_can_be_reconciled_from_the_journal(self):
        async def work(context):
            for text in ("a", "b", "c", "d"):
                yield text
        await self.backend(BaseEngine(action=work))
        release = asyncio.Event()
        seen = []
        async def observe(run, event):
            await release.wait()
            if event.delta or event.output:
                seen.append(event.delta or event.output)
        subscription = self.app.events.subscribe(observe, delivery="queued", buffer_size=1,
                                                 overflow="drop_oldest")
        try:
            run = await self.request()
            self.assertEqual(run.data.status, RunStatus.COMPLETED)
            self.assertGreater(subscription.stats["dropped"], 0)
        finally:
            release.set()
        await self.app.events.flush()
        persisted = await run.aoutput_events()
        self.assertGreater(len(persisted), len(seen))
        cursor, values = 0, {}
        while True:
            batch = await run.aoutput_events(after=cursor, limit=2)
            if not batch:
                break
            for value in batch:
                self.assertEqual(value.sequence, cursor + 1)
                if isinstance(value, EngineDelta):
                    previous = values.get(value.output_id) or EngineOutput(value.output_id,
                        step_id=value.step_id, visibility=value.visibility, final=False)
                    values[value.output_id] = previous.apply(value)
                else:
                    values[value.output_id] = value
                cursor = value.sequence
        self.assertEqual(list(values.values()), await run.aoutputs())
        self.assertEqual(values[run.id].text, "abcd")

    async def test_parallel_agents_have_distinct_internal_streams_and_one_root_result(self):
        engines = {name: LoopEngine(completion_fn=ScriptedCompletion(
            [chunk(name + "1"), chunk(name + "2", finish="stop")])) for name in ("a", "b")}
        graph = (WorkflowGraph(entry="fork").node("fork", "parallel", join="join")
                 .node("a", "agent", agent="a").node("b", "agent", agent="b")
                 .node("join", "join").node("end", "end")
                 .connect("fork", "a").connect("fork", "b")
                 .connect("a", "join").connect("b", "join").connect("join", "end").to_dict())
        self.app = LargeLanguageModel(self.root, engines={"test": GraphEngine("flow",
            handlers={"agent": AgentNode(engines=engines)})})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("parallel", components=["agents", "workflows", "tools"])
        agents = await self.project.components.aget("agents")
        for name in engines:
            await agents.acreate({"engine": name, "purpose": name, "completion": {"model": "test/model"},
                                  "tools": []}, identifier=name)
        await (await self.project.components.aget("workflows")).acreate(graph, identifier="flow")
        self.session = await self.project.sessions.acreate("parallel")
        run = await self.request()
        self.assertEqual(run.data.status, RunStatus.COMPLETED, run.data.error)
        self.assertEqual((await run.aresponse()).content, "")
        result = (await run.aresult()).output
        self.assertEqual(result.data["branches"]["a"]["text"], "a1a2")
        self.assertEqual(result.data["branches"]["b"]["text"], "b1b2")
        events = await run.aoutput_events()
        deltas = [item for item in events if isinstance(item, EngineDelta)]
        self.assertEqual(len({item.output_id for item in deltas}), 2)
        self.assertTrue(all(item.visibility == "internal" and item.step_id for item in deltas))
        self.assertEqual([item for item in events if isinstance(item, EngineOutput) and item.step_id is None], [result])
        self.assertEqual([item.sequence for item in events], list(range(1, len(events) + 1)))
