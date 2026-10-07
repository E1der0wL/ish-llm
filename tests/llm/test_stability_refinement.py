"""출력 상태 분리와 조회 최적화가 저장 경계·결과 순서를 바꾸지 않는지 검사한다."""

import random
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import contextmanager

from llm.core.results import EngineDelta, EngineOutput
from llm.engines.base import EngineEvent, EngineEventType
from llm.services.query import FileCatalog, Query
from llm.services.runtime.output import RunOutputState, OutputBuffer
from llm.llm import LargeLanguageModel, BackendServices
from tests.llm.support.fake_engine import FakeStreamingEngine


class OutputStateTests(unittest.TestCase):
    def test_rejected_ownership_and_finalization_do_not_advance_state(self):
        state = RunOutputState()
        delta = EngineEvent(EngineEventType.TEXT_DELTA, step_id="step",
                            delta=EngineDelta("out", "a", step_id="step"))
        first, change = state.accept(delta, {"step"})
        self.assertEqual((first.delta.sequence, change), (1, ("a", "append")))
        with self.assertRaisesRegex(ValueError, "running Step"):
            state.accept(delta, set())
        self.assertEqual((state.sequence, state.outputs["out"].text), (1, "a"))
        final = EngineEvent(EngineEventType.STEP_COMPLETED, step_id="step",
                            output=EngineOutput("out", "a", step_id="step", data={"large": "x" * 10000}))
        recorded, _ = state.accept(final, {"step"})
        self.assertEqual(recorded.output.data, {"large": "x" * 10000})
        self.assertIsNone(state.outputs["out"].data)
        self.assertEqual(state.outputs["out"].text, "")
        with self.assertRaisesRegex(ValueError, "finalized"):
            state.accept(delta, {"step"})
        self.assertEqual(state.sequence, 2)

    def test_interleaved_replace_keeps_internal_output_out_of_conversation(self):
        state = RunOutputState()
        changes = []
        for identifier, text, visibility, operation in (
                ("a", "a", "user", "append"), ("b", "b", "user", "append"),
                ("private", "hidden", "internal", "append"), ("a", "A", "user", "replace")):
            event = EngineEvent(EngineEventType.TEXT_DELTA,
                               delta=EngineDelta(identifier, text, visibility=visibility, operation=operation))
            _, change = state.accept(event, set())
            changes.append(change)
        self.assertEqual(changes, [("a", "append"), ("b", "append"), None, ("Ab", "replace")])


class CatalogTests(unittest.TestCase):
    def test_bounded_selection_matches_full_sort_for_all_query_modes(self):
        rows = [SimpleNamespace(id=str(i), created_at=str(i % 7), status="completed" if i % 2 else "failed")
                for i in range(113)]
        random.Random(123).shuffle(rows)
        ordered = sorted(rows, key=lambda row: (row.created_at, row.id))
        catalog = FileCatalog()
        with patch.object(catalog, "read", side_effect=lambda row: row):
            for status in (None, "completed", "missing"):
                for limit in (None, 0, 1, 7, 200):
                    for offset in (0, 3, 200, 10**30):
                        for descending in (False, True):
                            for after in (None, "42"):
                                query = Query(status=status, limit=limit, offset=offset,
                                              descending=descending, after=after)
                                self.assertEqual(catalog.select(iter(rows), query),
                                                 [row.id for row in query.apply(ordered)])
            with self.assertRaisesRegex(ValueError, "cursor"):
                catalog.select(iter(rows), Query(after="absent", limit=3))

    def test_small_page_still_detects_corruption_after_selected_rows(self):
        catalog = FileCatalog()
        with patch.object(catalog, "read", side_effect=[SimpleNamespace(id="a", created_at="0", status="ok"),
                                                        ValueError("corrupt metadata")]):
            with self.assertRaisesRegex(ValueError, "corrupt"):
                catalog.select([Path("a"), Path("b")], Query(limit=1))


@unittest.skipUnless(importlib.util.find_spec('rank_bm25'), 'Optional RAG dependency is not installed')
class RetrievalRankingTests(unittest.TestCase):
    def test_partial_sort_preserves_full_ranking_and_ties(self):
        from llm.components.rag.search import search
        documents = {}
        for i in range(40):
            identifier = str(i)
            text = 'alpha beta' if i % 2 else 'alpha gamma'
            documents[identifier] = {"title": identifier, "metadata": {}, "revision": 1,
                "content": text, "sections": {"s": {"text": text}},
                "chunks": [{"id": identifier, "document_id": identifier, "section_id": "s", "text": text}]}

        @contextmanager
        def vector_index(path):
            yield SimpleNamespace(query=lambda **kw: {"ids": [list(documents)[::-1][:kw['n_results']]]})

        with patch('llm.components.rag.search.collection', vector_index):
            for method in ('bm25', 'vector', 'hybrid'):
                for limit in (1, 7, 40, 50):
                    for query in ('alpha', 'beta', 'unmatched'):
                        args = dict(method=method, expand='section', limit=limit)
                        actual = search(Path('/unused'), documents, query, [1.0], candidate_count=20, rrf_constant=60, **args)
                        # 개선 전 전체 정렬을 독립 기준으로 사용한다. 동점의 입력 순서까지 같아야 한다.
                        with patch('llm.components.rag.search.nlargest',
                                   side_effect=lambda n, rows, key: sorted(rows, key=lambda r: -key(r))[:n]):
                            expected = search(Path('/unused'), documents, query, [1.0], candidate_count=20, rrf_constant=60, **args)
                        self.assertEqual(actual, expected)


class PersistenceBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_output_batch_is_not_published_and_next_request_runs(self):
        with tempfile.TemporaryDirectory() as root:
            observed = []
            async with LargeLanguageModel(root, components=[], engines={"echo": FakeStreamingEngine(chunks=("a", "b"))},
                    on_event=lambda run, event: observed.append((run.id, event.type)),
                    services=BackendServices(output_buffer=OutputBuffer(batch_size=8))) as backend:
                project = await backend.projects.acreate()
                session = await project.sessions.acreate()
                with patch.object(backend.run_repository, "record_outputs", side_effect=OSError("disk full")):
                    failed = await (await session.run.submit("fail", engine="echo")).wait(timeout=10)
                self.assertEqual((await failed.aget_data()).status, "failed")
                self.assertNotIn((failed.id, EngineEventType.TEXT_DELTA), observed)
                completed = await (await session.run.submit("next", engine="echo")).wait(timeout=10)
                self.assertEqual((await completed.aget_data()).status, "completed")
                self.assertEqual((await completed.aresponse()).content, "ab")
