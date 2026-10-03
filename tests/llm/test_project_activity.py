"""Activity는 확정된 lifecycle의 참조만 기록하며 실행/저장 실패의 원인이 되지 않는다."""

import asyncio
from dataclasses import FrozenInstanceError
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import warnings

from llm.core.contracts import ProjectActivityEvent, ResourceRef
from llm.core.models import RunStatus, now
from llm.core.paths import ProjectPaths
from llm.engines.base import BaseEngine
from llm.engines.graph import GraphEngine
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.llm import LargeLanguageModel
from llm.providers.requests import ProviderError, invoke
from llm.services.infrastructure.activity import activity_event, read_activity, _recent_lines
from llm.services.infrastructure.storage import atomic_json
from llm.services.infrastructure.transactions import TransactionManager, current_transaction


class VendorError(Exception):
    code = "provider_rate_limit"


class ActivityStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = SimpleNamespace(id="project", paths=ProjectPaths(self.root))
        self.run = SimpleNamespace(id="run", session_id="session")
        self.path = self.project.paths.logs / "activity.jsonl"

    def append(self):
        activity_event(self.project, self.run, "run.started", time=now(), status="running")

    def test_contract_is_frozen_and_allowlisted(self):
        self.append()
        value = read_activity(self.project.paths)[0]
        self.assertEqual(set(value.to_dict()), {"time", "event", "source", "status", "code"})
        self.assertEqual(ProjectActivityEvent.from_dict(value.to_dict()), value)
        with self.assertRaises(FrozenInstanceError):
            value.status = "failed"
        with self.assertRaises(FrozenInstanceError):
            value.source.run_id = "changed"
        with self.assertRaises(ValueError):
            ProjectActivityEvent.from_dict({**value.to_dict(), "metadata": {}})
        for changes in ({"event": "provider_retry"}, {"status": "failed"}, {"time": "invalid"},
                        {"time": "2026-10-01T00:00:00"}, {"code": ""}):
            with self.assertRaises(ValueError):
                ProjectActivityEvent.from_dict({**value.to_dict(), **changes})

    def test_after_commit_and_rollback(self):
        transaction = TransactionManager(self.root)
        domain = self.root / "domain.json"
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with transaction.scope():
                atomic_json(domain, {"status": "running"})
                self.append()
                self.assertFalse(self.path.exists())
                raise RuntimeError("rollback")
        self.assertFalse(domain.exists())
        self.assertFalse(self.path.exists())
        from llm.services.infrastructure.activity import append_bytes
        def committed(path, payload):
            self.assertIsNone(current_transaction())
            self.assertEqual(json.loads(domain.read_text())["status"], "running")
            append_bytes(path, payload)
        with patch("llm.services.infrastructure.activity.append_bytes", side_effect=committed):
            with transaction.scope():
                atomic_json(domain, {"status": "running"})
                self.append()
                self.assertFalse(self.path.exists())
        self.assertEqual(len(read_activity(self.project.paths)), 1)

    def test_write_failures_are_best_effort_even_without_transaction(self):
        for target in ("_path", "append_bytes"):
            with self.subTest(target=target), warnings.catch_warnings():
                warnings.simplefilter("error")
                with patch("llm.services.infrastructure.activity." + target, side_effect=OSError("PRIVATE")):
                    self.append()
        with warnings.catch_warnings(record=True) as records:
            warnings.simplefilter("always")
            with patch.object(ProjectActivityEvent, "to_dict", side_effect=ValueError("PRIVATE")):
                self.append()
        self.assertTrue(records)
        self.assertNotIn("PRIVATE", str(records[0].message))
        self.assertFalse(self.path.exists())

    def test_fsync_failure_does_not_escape(self):
        self.append()
        with warnings.catch_warnings(record=True), patch("llm.services.infrastructure.storage.os.fsync",
                side_effect=OSError("PRIVATE")):
            self.append()
        self.assertTrue(self.path.exists())

    def test_recent_query_order_limits_and_missing(self):
        self.assertEqual(read_activity(self.project.paths), ())
        for number in range(7):
            self.run.id = str(number)
            self.append()
        self.assertEqual([e.source.id for e in read_activity(self.project.paths, limit=2)], ["6", "5"])
        self.assertEqual([e.source.id for e in read_activity(self.project.paths, limit=2, newest_first=False)], ["5", "6"])
        self.assertEqual(len(read_activity(self.project.paths)), 7)
        self.assertEqual(read_activity(self.project.paths, limit=0), ())
        for invalid in (-1, True, 1.5):
            with self.assertRaises(ValueError):
                read_activity(self.project.paths, limit=invalid)

    def test_reverse_reader_is_bounded_and_preserves_utf8(self):
        class Counted(io.BytesIO):
            consumed = 0
            def read(self, size=-1):
                self.consumed += size
                return super().read(size)
        source = Counted(("문서" * 3000 + "\n").encode() * 1000 + b"last\n")
        rows = _recent_lines(source)
        self.assertEqual(next(rows), b"last")
        self.assertLess(source.consumed, 10000)
        self.assertEqual(next(rows).decode(), "문서" * 3000)

    def test_corruption_is_reported_without_repair(self):
        self.append()
        for tail in (b'{"PRIVATE":', b'{"PRIVATE":}\n'):
            self.path.write_bytes(tail)
            with self.assertRaises(ValueError) as error:
                read_activity(self.project.paths)
            self.assertNotIn("PRIVATE", str(error.exception))
            self.assertEqual(self.path.read_bytes(), tail)
        self.path.write_bytes(b"partial")
        with warnings.catch_warnings(record=True):
            self.append()
        self.assertEqual(self.path.read_bytes(), b"partial")

    def test_file_and_directory_symlinks_are_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        target = outside / "target"
        target.write_text("untouched")
        self.project.paths.logs.mkdir()
        self.path.symlink_to(target)
        with self.assertRaises(ValueError):
            read_activity(self.project.paths)
        with warnings.catch_warnings(record=True):
            self.append()
        self.assertEqual(target.read_text(), "untouched")
        self.path.unlink()
        self.project.paths.logs.rmdir()
        self.project.paths.logs.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            read_activity(self.project.paths)
        with warnings.catch_warnings(record=True):
            self.append()
        self.assertFalse((outside / "activity.jsonl").exists())


class ActivityRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def setUpBackend(self, engine, *, components=()):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.backend = LargeLanguageModel(temporary.name, engines={"test": engine}, components=list(components))
        self.addAsyncCleanup(self.backend.shutdown)
        self.project = await self.backend.projects.acreate(components=[c.name for c in components])
        self.session = await self.project.sessions.acreate()
        return temporary.name

    async def run_request(self, session=None, **options):
        return await (await (session or self.session).run.submit("SECRET-PROMPT", engine="test", **options)).wait(timeout=20)

    async def test_success_many_steps_two_sessions_reopen_and_no_raw_content(self):
        from llm.engines.pipeline import PipelineEngine
        async def action(context):
            yield "SECRET-OUTPUT"
        engine = PipelineEngine([BaseEngine(action=action), BaseEngine(action=action)])
        root = await self.setUpBackend(engine)
        second = await self.project.sessions.acreate()
        handles = await asyncio.gather(self.run_request(), self.run_request(second))
        events = await self.project.aactivity()
        self.assertEqual(len(events), 4)
        self.assertEqual({e.event for e in events}, {"run.started", "run.completed"})
        self.assertEqual({e.source.session_id for e in events}, {self.session.id, second.id})
        self.assertTrue(all(e.source.project_id == self.project.id and e.code is None for e in events))
        self.assertEqual({e.source.run_id for e in events}, {h.id for h in handles})
        self.assertEqual(self.project.activity(), events)
        text = (self.project.paths.logs / "activity.jsonl").read_text()
        self.assertNotIn("SECRET", text)
        self.assertTrue((self.project.paths.logs / "service.log").exists())
        await self.backend.shutdown()
        async with LargeLanguageModel(root, engines={"test": engine}) as reopened:
            project = await reopened.projects.aload(self.project.id)
            with patch.object(reopened.run_repository, "list", side_effect=AssertionError("No Run loads")):
                self.assertEqual(await project.aactivity(), events)

    async def test_known_unknown_and_vendor_codes(self):
        failure = [ProviderError("provider_rate_limit")]
        async def action(context):
            raise failure[0]
        await self.setUpBackend(BaseEngine(action=action))
        for error, step_code, run_code in ((failure[0], "provider_rate_limit", "provider_rate_limit"),
                (RuntimeError("SECRET-RAW-MESSAGE"), "step_failed", "engine_failed"),
                (VendorError("SECRET-RAW-MESSAGE"), "step_failed", "engine_failed")):
            failure[0] = error
            handle = await self.run_request()
            events = [e for e in await self.project.aactivity() if e.source.run_id == handle.id]
            self.assertEqual([e.event for e in events], ["run.failed", "step.failed", "run.started"])
            self.assertEqual(events[0].code, run_code)
            self.assertEqual(events[1].code, step_code)
            steps = await handle.steps.alist()
            self.assertEqual(events[1].source.step_id, steps[0].id)
            if isinstance(error, VendorError):
                self.assertEqual(steps[0].diagnostic.code, "provider_rate_limit")
        self.assertNotIn("SECRET", (self.project.paths.logs / "activity.jsonl").read_text())

    async def test_component_failure_via_graph_and_provider_retry_not_duplicated(self):
        calls = []
        async def component(**request):
            calls.append(True)
            raise ProviderError("provider_unavailable")
        async def handler(node):
            await invoke("aembedding", {}, component, {"max_attempts": 2})
        await self.setUpBackend(GraphEngine(handlers={"component": handler}), components=[WorkflowComponent()])
        workflow = (WorkflowGraph(entry="component").node("component", "component")
                    .node("end", "end").connect("component", "end").to_dict())
        await self.project.components.workflows.acreate(workflow, identifier="flow")
        handle = await self.run_request(engine_options={"workflow": "flow"})
        self.assertEqual(len(calls), 2)
        events = await self.project.aactivity()
        self.assertEqual([e.event for e in events].count("step.failed"), 2)
        self.assertTrue(all(e.event in ("run.started", "run.failed", "step.failed") for e in events))
        self.assertTrue(all(e.code == "provider_unavailable" for e in events if e.event != "run.started"))
        self.assertEqual((await handle.aresult()).error_code, "provider_unavailable")

    async def test_activity_failure_cannot_change_success_or_failure(self):
        failure = [None]
        async def action(context):
            if failure[0]:
                raise failure[0]
        await self.setUpBackend(BaseEngine(action=action))
        with patch("llm.services.infrastructure.activity.append_bytes", side_effect=OSError("SECRET")), warnings.catch_warnings():
            warnings.simplefilter("error")
            for error, status, code in ((None, RunStatus.COMPLETED, None),
                    (ProviderError("provider_rate_limit"), RunStatus.FAILED, "provider_rate_limit")):
                failure[0] = error
                result = await (await self.run_request()).aresult()
                self.assertEqual((result.status, result.error_code), (status, code))
        self.assertEqual(await self.project.aactivity(), ())

    async def test_tool_arguments_are_absent_and_activity_observes_committed_files(self):
        from llm.components.tools import Tool
        from llm.services.runtime.tools import ToolExecutor
        from llm.services.infrastructure.activity import append_bytes
        async def fail(arguments):
            raise ProviderError("provider_rate_limit")
        class ToolEngine:
            async def execute(self, context):
                tool = Tool("lookup", "SECRET-DESCRIPTION", {"type": "object",
                    "properties": {"query": {"type": "string"}}}, fail)
                async for event in ToolExecutor().execute(tool, {"query": "SECRET-TOOL-ARG"}, result={}):
                    yield event
        await self.setUpBackend(ToolEngine())
        observed = []
        def committed(path, payload):
            value = json.loads(payload)
            source = value["source"]
            run_path = self.project.paths.sessions / source["session_id"] / "runs" / source["run_id"]
            record = (run_path / "steps" / source["step_id"] / "step.json"
                      if source["kind"] == "step" else run_path / "run.json")
            self.assertIsNone(current_transaction())
            self.assertEqual(json.loads(record.read_text())["status"], value["status"])
            observed.append(value["event"])
            append_bytes(path, payload)
        with patch("llm.services.infrastructure.activity.append_bytes", side_effect=committed):
            handle = await self.run_request()
        self.assertEqual(observed, ["run.started", "step.failed", "run.failed"])
        self.assertEqual((await handle.aresult()).error_code, "provider_rate_limit")
        self.assertNotIn("SECRET", (self.project.paths.logs / "activity.jsonl").read_text())

    async def test_corruption_does_not_block_project_load_or_execution(self):
        async def action(context):
            return None
        await self.setUpBackend(BaseEngine(action=action))
        path = self.project.paths.logs / "activity.jsonl"
        path.write_bytes(b"broken")
        await self.backend.projects.aload(self.project.id)
        with self.assertRaises(ValueError):
            await self.project.aactivity()
        with warnings.catch_warnings(record=True):
            self.assertEqual((await (await self.run_request()).aresult()).status, RunStatus.COMPLETED)
        self.assertEqual(path.read_bytes(), b"broken")

    async def test_interrupted_and_paused_lifecycle(self):
        async def cancel(context):
            raise asyncio.CancelledError()
        await self.setUpBackend(BaseEngine(action=cancel))
        handle = await self.run_request()
        events = await self.project.aactivity()
        self.assertEqual((events[0].event, events[0].code), ("run.interrupted", "interrupted"))
        self.assertEqual((await handle.aresult()).status, RunStatus.INTERRUPTED)
        async def work(node):
            return {}
        await self.backend.shutdown()
        await self.setUpBackend(GraphEngine(handlers={"work": work}), components=[WorkflowComponent()])
        graph = WorkflowGraph(entry="work").node("work", "work", pause_before=True).node("end", "end").connect("work", "end")
        await self.project.components.workflows.acreate(graph.to_dict(), identifier="flow")
        handle = await self.run_request(engine_options={"workflow": "flow"})
        self.assertEqual((await handle.aresult()).status, RunStatus.PAUSED)
        events = await self.project.aactivity()
        self.assertEqual([e.event for e in events], ["run.paused", "run.started"])
        self.assertEqual(events[0].code, (await handle.aresult()).error_code)
