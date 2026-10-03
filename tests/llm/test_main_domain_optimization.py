"""누적 출력·순차 복구·역방향 페이지·Graph 스냅샷의 동등성과 확장 계약 검사."""

import json
import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llm.components.tools import ToolRegistry
from llm.components.workflows import WorkflowGraph
from llm.core.models import ProjectConfig, MessageRole, MessageStatus
from llm.core.results import EngineDelta, EngineOutput
from llm.engines.graph import GraphEngine
from llm.services.infrastructure.journal import OutputJournal
from llm.services.query import Query
from llm.services.runtime.output import OutputProjection
from llm.services.runtime.runs import RunRepository
from llm.services.history.conversation import MemoryConversationStore, ConversationStore
from llm.services.infrastructure.transactions import TransactionManager


class OutputProjectionTests(unittest.TestCase):
    def test_random_interleaving_matches_immutable_contract(self):
        rng = random.Random(23)
        projection, expected = OutputProjection(), {}
        for sequence in range(1, 1501):
            identifier = str(rng.randrange(6))
            value = EngineDelta(identifier, rng.choice(("", "한글", "abc", "🙂")),
                operation=rng.choice(("append", "append", "replace")), sequence=sequence,
                visibility="internal" if identifier == "0" else "user")
            previous = expected.get(identifier) or EngineOutput(identifier, visibility=value.visibility, final=False)
            expected[identifier] = previous.apply(value)
            projection.add(value)
            if sequence % 71 == 0:
                self.assertEqual(projection.snapshot(), expected)
        final = EngineOutput("2", "final", data={"result": [1, 2]}, sequence=1501)
        projection.add(final)
        expected["2"] = final
        self.assertEqual(projection.snapshot(), expected)
        for value in (EngineDelta("2", "late", sequence=1502),
                      EngineDelta("0", "wrong owner", sequence=1503),
                      EngineDelta("1", "old", sequence=1)):
            with self.assertRaises(ValueError):
                projection.add(value)
            self.assertEqual(projection.snapshot(), expected)

    def test_partial_output_then_delta_and_replace_preserves_metadata(self):
        values = [EngineOutput("out", "initial", data={"value": 1}, metadata={"tag": "x"}, final=False),
                  EngineDelta("out", " appended", sequence=1),
                  EngineDelta("out", "new", operation="replace", sequence=2),
                  EngineDelta("out", " text", sequence=3)]
        projection, expected = OutputProjection(), values[0]
        for value in values:
            projection.add(value)
        for value in values[1:]:
            expected = expected.apply(value)
        self.assertEqual(projection.snapshot(), {"out": expected})

    def test_repository_streams_and_closes_journal_on_contract_error(self):
        closed = []
        class Journal(OutputJournal):
            def iter_events(self, *args, **kwargs):
                try:
                    yield EngineOutput("out", "done", sequence=1)
                    yield EngineDelta("out", "invalid", sequence=2)
                finally:
                    closed.append(True)
        repository = RunRepository()
        repository.output_journal = Journal()
        run = SimpleNamespace(paths=SimpleNamespace(state=Path("unused")))
        with self.assertRaises(ValueError):
            repository.outputs(run)
        self.assertEqual(closed, [True])

    def test_custom_repository_and_journal_read_are_not_bypassed(self):
        value = EngineDelta("out", "custom", sequence=1)
        class Repository(RunRepository):
            def output_events(self, run, **kwargs):
                return [value]
        class Journal(OutputJournal):
            def read(self, *args, **kwargs):
                return [value]
            def iter_events(self, *args, **kwargs):
                raise AssertionError("custom read must be used")
        run = SimpleNamespace(paths=SimpleNamespace(state=Path("unused")))
        custom_journal_repository = RunRepository()
        custom_journal_repository.output_journal = Journal()
        for repository in (Repository(), custom_journal_repository):
            self.assertEqual(repository.outputs(run)[0].text, "custom")

    def test_journal_cursor_boundaries_and_incomplete_tail(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "outputs.jsonl"
            values = [EngineDelta("out", str(i), sequence=i) for i in range(1, 34)]
            path.write_text("".join(json.dumps({"type": "delta", "value": v.to_dict()}) + "\n" for v in values), encoding="utf-8")
            with path.open("ab") as stream:
                stream.write(b'{"type":')
            journal = OutputJournal(index_stride=3)
            for after in (0, 1, 2, 3, 6, 31, 33, 100):
                for limit in (None, 0, 1, 5):
                    expected = values[after:] if limit is None else values[after:after + limit]
                    self.assertEqual(journal.read(path, after=after, limit=limit), expected)
            iterator = journal.iter_events(path)
            self.assertEqual(next(iterator), values[0])
            iterator.close()
            self.assertIsNone(iterator.gi_frame)


class ReverseQueryTests(unittest.TestCase):
    def test_reversible_collection_is_not_copied(self):
        class Rows:
            def __iter__(self):
                raise AssertionError("forward materialization is unnecessary")
            def __reversed__(self):
                yield SimpleNamespace(id="last", status="completed")
                raise AssertionError("page should stop at limit")
        self.assertEqual(Query(descending=True, limit=1).apply(Rows())[0].id, "last")

    def test_generator_fallback_matches_dict_view(self):
        rows = {str(i): SimpleNamespace(id=str(i), status="ok") for i in range(9)}
        query = Query(descending=True, after="7", offset=1, limit=3)
        self.assertEqual(query.apply(iter(rows.values())), query.apply(rows.values()))


class ConversationBufferTests(unittest.TestCase):
    def test_memory_rollback_restores_buffer_after_append_replace_and_finalize(self):
        with tempfile.TemporaryDirectory() as root:
            transactions = TransactionManager(Path(root))
            store = MemoryConversationStore()
            message = store.create(MessageRole.ASSISTANT, "start", MessageStatus.STREAMING)
            store.delta(message.id, " 원본")
            original = store.get(message.id)
            for changes in (("append",), ("replace",), ("append", "replace", "append")):
                with self.assertRaisesRegex(RuntimeError, "abort"):
                    with transactions.scope():
                        for operation in changes:
                            store.delta(message.id, "🙂", operation=operation)
                        store.set_status(message.id, MessageStatus.COMPLETED)
                        store.reconcile(message.id, "reconciled", run_id=None)
                        raise RuntimeError("abort")
                self.assertEqual(store.get(message.id), original)
                self.assertEqual(store.list(), [original])
                self.assertEqual(store.count(status=MessageStatus.STREAMING), 1)
            store.delta(message.id, " tail")
            self.assertEqual(store.get(message.id).content, "start 원본 tail")

    def test_file_replay_prune_reconcile_and_partial_tail_preserve_text(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "conversation.jsonl"
            store = ConversationStore(path)
            deleted = store.create(MessageRole.USER, "discard", MessageStatus.COMPLETED)
            message = store.create(MessageRole.ASSISTANT, "seed", MessageStatus.STREAMING, run_id="run")
            store.deltas(message.id, [("a", "append"), ("새 본문", "replace"), ("🙂", "append")])
            copy = store.get(message.id)
            copy.content = "outside"
            self.assertEqual(ConversationStore(path).get(message.id).content, "새 본문🙂")
            store.set_status(message.id, MessageStatus.COMPLETED)
            store.reconcile(message.id, "복구 결과", run_id="run")
            store.prune([deleted.id])
            with path.open("ab") as stream:
                stream.write(b'{"type":')
            reopened = ConversationStore(path)
            self.assertEqual([m.content for m in reopened.list()], ["복구 결과"])
            reopened.update_metadata(message.id, {"checked": True})
            self.assertEqual(ConversationStore(path).get(message.id).content, "복구 결과")


class GraphPreparationTests(unittest.TestCase):
    def test_definition_snapshot_is_shared_but_not_cached_between_requests(self):
        child = WorkflowGraph(entry="done").node("done", "end").to_dict()
        parent = (WorkflowGraph(entry="child").node("child", "workflow", workflow="child")
                  .node("done", "end").connect("child", "done").to_dict())
        records = {"main": parent, "child": child}
        context = SimpleNamespace(capabilities={"workflows": [{"records": records}]},
            project=SimpleNamespace(config=ProjectConfig(), components=["workflows"]),
            session=SimpleNamespace(config={}), run=SimpleNamespace(engine="graph"),
            tools=ToolRegistry(), tool_scope=None)
        engine = GraphEngine(handlers={}).for_request({"workflow": "main"}).configured(context)
        calls = []
        original = GraphEngine._definition
        def tracked(owner, capabilities):
            calls.append(owner.workflow)
            return original(owner, capabilities)
        with patch.object(GraphEngine, "_definition", tracked):
            graph, prepared = engine._prepare(context)
            binding = engine._binding(context, graph, prepared)
        self.assertEqual(calls, ["main", "child"])
        records["child"]["initial_state"] = {"new": True}
        self.assertNotIn("new", binding["nested_graphs"][1]["definition"].get("initial_state", {}))
        header = {"format": "workflow-nodes-v1", "binding": binding}
        with self.assertRaisesRegex(ValueError, "changed"):
            engine.validate_resume({"header": header, "records": {}}, context=context)
        records["child"] = (WorkflowGraph(entry="back").node("back", "workflow", workflow="main")
                            .node("done", "end").connect("back", "done").to_dict())
        with self.assertRaisesRegex(RuntimeError, "Cyclic"):
            engine.validate_resume({"header": header, "records": {}}, context=context)
