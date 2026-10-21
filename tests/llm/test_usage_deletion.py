"""Deletion cannot free a live Project quota or an active Run's captured quota."""

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from tempfile import TemporaryDirectory
import unittest

from llm.llm import LargeLanguageModel, LoopEngine, Tool, ToolRegistry
from llm.services.infrastructure.storage import atomic_domain_json, record
from tests.llm.support.runtime_tools import RuntimeTools, TEST_CONTRACT
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class UsageDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def create(self, usage, *, responses=None, tools=None):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        model = ScriptedCompletion(*([[chunk("answer", finish="stop")]] * 4
                                    if responses is None else responses))
        app = LargeLanguageModel(temporary.name, engines={"loop": LoopEngine(completion_fn=model)},
                                 components=[] if tools is None else [RuntimeTools(ToolRegistry(tools))])
        self.addAsyncCleanup(app.shutdown)
        parameters = {"engines": {"loop": {"config": {"completion": {"model": "test", "max_tokens": 5}}}}}
        if tools is not None:
            parameters["components"] = {"tools": {"config": {"enabled": [tool.name for tool in tools]}}}
        project = await app.projects.acreate(components=[] if tools is None else ["tools"],
                                            config={"policies": {"usage": usage}, "parameters": parameters})
        session = await project.sessions.acreate()
        return app, project, session, model

    async def run_once(self, session):
        return await (await session.run.submit("request", engine="loop")).wait()

    async def test_permanent_delete_cannot_reset_project_call_quota(self):
        _, project, session, model = await self.create({"project_max_calls": 1})
        first = await self.run_once(session)
        self.assertEqual(first.data.status, "completed", first.data.error)
        blocked = await self.run_once(session)
        self.assertEqual(blocked.data.error_code, "usage_limit")
        await session.run.shutdown()
        run_path = first.data.paths.root / "run.json"
        original = run_path.read_bytes()
        rejected = False
        try:
            await session.adelete(permanent=True)
        except ValueError:
            rejected = True
        usage = await project.amodel_usage()
        other = await project.sessions.acreate()
        after = await self.run_once(other)
        # Observe the actual extra model call before asserting failure.
        self.assertEqual({
            "delete_rejected": rejected, "calls_accounted": usage["call_count"],
            "model_calls": len(model.requests), "next_error": after.data.error_code,
            "proof_preserved": run_path.exists() and run_path.read_bytes() == original,
        }, {"delete_rejected": True, "calls_accounted": 1, "model_calls": 1,
            "next_error": "usage_limit", "proof_preserved": True})

    async def test_active_run_quota_survives_policy_change_and_old_session_deletion(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def pause(arguments):
            entered.set()
            await release.wait()
        tool = Tool("pause", "pause", {"type": "object"}, pause, contract=TEST_CONTRACT)
        _, project, session, model = await self.create({"project_max_calls": 2}, tools=[tool], responses=[
            [chunk("first", finish="stop")],
            [chunk(calls=[call("{}", name="pause")], finish="tool_calls")],
            [chunk("escaped old quota", finish="stop")],
        ])
        self.assertEqual((await self.run_once(session)).data.status, "completed")
        await session.run.shutdown()
        other = await project.sessions.acreate()
        pending = await other.run.submit("request", engine="loop")
        try:
            await asyncio.wait_for(entered.wait(), 5)
            self.assertEqual(len(model.requests), 2)
            await project.aconfigure_policies({"usage": {"project_max_calls": None}})
            protected = {}
            for unit in ("session", "run"):
                plan = await project.aretention_plan(unit)
                protected[unit] = any(row["session_id"] == session.id and
                    "active_usage_accounting" in row["reasons"] for row in plan.protected)
            rejected = False
            try:
                await session.adelete(permanent=True)
            except ValueError:
                rejected = True
        finally:
            release.set()
        result = await pending.wait()
        self.assertEqual((protected, rejected, len(model.requests), result.data.error_code),
                         ({"session": True, "run": True}, True, 2, "usage_limit"))

    async def test_soft_delete_keeps_quota_and_permanent_delete_still_rejects(self):
        _, project, session, model = await self.create({"project_max_calls": 1})
        await self.run_once(session)
        await session.run.shutdown()
        await session.adelete()
        self.assertEqual((await project.amodel_usage())["call_count"], 1)
        with self.assertRaisesRegex(ValueError, "quota"):
            await session.adelete(permanent=True)
        other = await project.sessions.acreate()
        self.assertEqual((await self.run_once(other)).data.error_code, "usage_limit")
        self.assertEqual(len(model.requests), 1)

    async def test_current_policy_protects_unknown_tokens_without_new_aggregate(self):
        app, project, session, model = await self.create({})
        first = await self.run_once(session)
        await session.run.shutdown()
        app.policy_resolver.token_counters["fixed"] = lambda request: 10
        await project.aconfigure_policies({"usage": {"project_max_tokens": 100, "counter": "fixed"}})
        path = first.data.paths.root / "run.json"
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "quota"):
            await session.adelete(permanent=True)
        self.assertEqual(path.read_bytes(), before)
        other = await project.sessions.acreate()
        self.assertEqual((await self.run_once(other)).data.error_code, "usage_unknown")
        self.assertEqual(len(model.requests), 1)

    async def test_expired_receipts_allow_explicit_delete_without_freeing_current_period(self):
        app, project, session, model = await self.create({"project_max_calls": 1, "period_seconds": 60})
        first = await self.run_once(session)
        await session.run.shutdown()
        with app.project_manager.ownership.scope():
            run = app.run_repository.load(session.data, first.id)
            run.metadata["completions"][0]["started_at"] = "2000-01-01T00:00:00+00:00"
            # Time-shift the fixture; normal updates cannot rewrite completed evidence.
            atomic_domain_json(run.paths.root / "run.json", record(run))
        self.assertEqual((await project.amodel_usage())["call_count"], 0)
        other = await project.sessions.acreate()
        self.assertEqual((await self.run_once(other)).data.status, "completed")
        root = session.paths.root
        await session.adelete(permanent=True)
        self.assertFalse(root.exists())
        self.assertEqual((await project.amodel_usage())["call_count"], 1)
        self.assertEqual((await self.run_once(other)).data.error_code, "usage_limit")
        self.assertEqual(len(model.requests), 2)

    async def test_corrupt_period_evidence_blocks_delete_and_preserves_source(self):
        _, _, session, _ = await self.create({"project_max_calls": 1, "period_seconds": 60})
        first = await self.run_once(session)
        await session.run.shutdown()
        path = first.data.paths.root / "run.json"
        original = json.loads(path.read_text())
        for timestamp in (None, False, "", "2000-01-01T00:00:00"):
            with self.subTest(timestamp=timestamp):
                value = deepcopy(original)
                entry = value["metadata"]["completions"][0]
                if timestamp is None:
                    del entry["started_at"]
                else:
                    entry["started_at"] = timestamp
                path.write_text(json.dumps(value))
                before = path.read_bytes()
                with self.assertRaises((KeyError, ValueError, TypeError)):
                    await session.adelete(permanent=True)
                self.assertEqual(path.read_bytes(), before)


class UsagePeriodTests(unittest.TestCase):
    def test_period_boundary_is_shared_and_never_mutates_receipts(self):
        from llm.services.runtime.usage import period_entries
        clock = datetime(2026, 10, 20, tzinfo=timezone.utc)
        entries = [{"started_at": (clock - timedelta(seconds=seconds)).isoformat()}
                   for seconds in (61, 60, 59)]
        before = deepcopy(entries)
        self.assertEqual(period_entries(entries, {"period_seconds": 60}, at=clock), entries[1:])
        for policy in ({}, {"period_seconds": None}):
            self.assertEqual(period_entries(entries, policy, at=clock), entries)
        self.assertEqual(entries, before)


if __name__ == "__main__":
    unittest.main()
