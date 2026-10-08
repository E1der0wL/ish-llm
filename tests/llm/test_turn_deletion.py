"""재개 포기와 대화 삭제의 CAS, 영속성, 승인 및 rollback 경계."""

from dataclasses import replace
import asyncio
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from llm.core.models import RunStatus
from llm.core.plans import TurnDeletionPlan
from llm.engines.loop import LoopEngine
from llm.engines.base import EngineEventType
from llm.components.workflows import WorkflowComponent
from llm.llm import LargeLanguageModel
from tests.llm import test_conversation_turns as turn_fixtures
from tests.llm.test_loop import ScriptedCompletion


class TurnDeletionTests(unittest.IsolatedAsyncioTestCase):
    setup_graph = turn_fixtures.TurnResumeTests.setup_graph
    resume = turn_fixtures.TurnResumeTests.resume

    async def test_graph_abandonment_preserves_evidence_and_survives_reopen(self):
        for storage in ("file", "memory"):
            with self.subTest(storage=storage):
                run = await self.setup_graph(storage)
                original = await run.aget_data()
                checkpoint = await run.acheckpoint()
                steps = [s.id for s in await run.steps.alist()]
                request, = await run.ainteractions(pending_only=True)
                plan = await self.session.adelete_turn_plan(original.input_message_id)
                self.assertEqual(TurnDeletionPlan.from_dict(plan.to_dict()), plan)
                self.assertEqual([b.source.run_id for b in plan.blockers], [run.id])
                self.assertEqual(plan.blockers[0].details["status"], "paused")
                self.assertNotIn("resume_abandonment", (await run.aget_data()).metadata)
                with self.assertRaisesRegex(ValueError, "runtime is attached"):
                    await self.session.adelete_turn(original.input_message_id,
                        abandon_runs=[run.id], expected_revision=plan.revision)
                await self.session.run.shutdown()
                with self.assertRaisesRegex(ValueError, "unfinished resumable"):
                    await self.session.adelete_turn(original.input_message_id)
                await self.session.adelete_turn(original.input_message_id,
                    abandon_runs=[run.id], expected_revision=plan.revision)
                saved = await run.aget_data()
                marker = saved.metadata.pop("resume_abandonment")
                self.assertEqual(saved, original)
                self.assertEqual(marker["request_id"], original.input_message_id)
                self.assertEqual(marker["reason"], "conversation_deleted")
                self.assertEqual(await run.acheckpoint(), checkpoint)
                self.assertEqual([s.id for s in await run.steps.alist()], steps)
                self.assertFalse(await self.session.aconversation())
                self.assertEqual(len(await self.session.aconversation(include_deleted=True)), 2)
                self.assertFalse(self.effects)
                views = await run.ainteraction_views()
                self.assertTrue(all(v.status == "unavailable" and v.blocked_reason == "resume_abandoned" for v in views))
                self.assertFalse(await run.ainteractions(pending_only=True))
                with self.assertRaisesRegex(ValueError, "abandoned"):
                    await run.acancel_interaction(request)
                with self.assertRaisesRegex(ValueError, "abandoned"):
                    await run.arenew_interaction(request)
                with self.assertRaisesRegex(ValueError, "abandoned"):
                    await run.arespond(request.respond(request.options[0].id))
                preview = await self.session.run.resume_plan(run.id, engine="graph")
                self.assertFalse(preview.can_resume)
                self.assertTrue(any("abandoned" in b.message for b in preview.blockers))
                with self.assertRaisesRegex(ValueError, "abandoned"):
                    await self.resume(run, "first")
                self.assertEqual(len(await self.session.run.alist()), 1)
                await self.session.run.shutdown()
                clone = await self.session.aclone()
                self.assertFalse(await clone.aconversation())
                if storage == "file":
                    project_id, session_id = self.project.id, self.session.id
                    await self.app.shutdown()
                    self.app = LargeLanguageModel(self.root, components=[WorkflowComponent()], engines={"graph": self.engine})
                    self.addAsyncCleanup(self.app.shutdown)
                    self.session = (await self.app.projects.aload(project_id)).sessions.load(session_id)
                    with self.assertRaisesRegex(ValueError, "abandoned"):
                        await self.session.run.resume(run.id, engine="graph")
                    self.assertFalse(await self.session.aconversation())
                await self.app.shutdown()

    async def test_all_shared_dependencies_require_exact_confirmation_and_current_revision(self):
        first = await self.setup_graph()
        request_id = first.data.input_message_id
        old = await self.session.adelete_turn_plan(request_id)
        later = await (await self.session.run.submit("another", engine="graph", engine_options={"workflow": "flow"})).wait()
        plan = await self.session.adelete_turn_plan(request_id)
        self.assertEqual({b.source.run_id for b in plan.blockers}, {first.id, later.id})
        await self.session.run.shutdown()
        with self.assertRaisesRegex(ValueError, "plan changed"):
            await self.session.adelete_turn(request_id, abandon_runs=[first.id], expected_revision=old.revision)
        for ids in ([first.id], [first.id, later.id, "unrelated"]):
            with self.assertRaisesRegex(ValueError, "exactly"):
                await self.session.adelete_turn(request_id, abandon_runs=ids, expected_revision=plan.revision)
        self.assertNotIn("resume_abandonment", first.data.metadata)
        self.assertNotIn("resume_abandonment", later.data.metadata)
        await self.session.adelete_turn(request_id, abandon_runs=[first.id, later.id], expected_revision=plan.revision)
        self.assertEqual(len(await self.session.aconversation()), 2)
        self.assertTrue(later.data.metadata["resume_abandonment"])
        self.assertFalse(self.effects)

    async def test_latest_resume_only_is_abandoned_and_ancestor_cannot_replay(self):
        first = await self.setup_graph(pauses=("first", "second"))
        second = await self.resume(first, "first")
        plan = await self.session.adelete_turn_plan(first.data.input_message_id)
        self.assertEqual([b.source.run_id for b in plan.blockers], [second.id])
        first_record = first.data.paths.root.joinpath("run.json").read_bytes()
        await self.session.run.shutdown()
        await self.session.adelete_turn(first.data.input_message_id,
            abandon_runs=[second.id], expected_revision=plan.revision)
        self.assertEqual(first.data.paths.root.joinpath("run.json").read_bytes(), first_record)
        with self.assertRaises(ValueError):
            await self.resume(first, "first")
        with self.assertRaisesRegex(ValueError, "abandoned"):
            await self.resume(second, "second")
        self.assertEqual(self.effects, ["first"])

    async def test_write_failure_rolls_back_abandonment_messages_and_activity(self):
        for storage in ("file", "memory"):
            with self.subTest(storage=storage):
                run = await self.setup_graph(storage)
                request_id = run.data.input_message_id
                plan = await self.session.adelete_turn_plan(request_id)
                await self.session.run.shutdown()
                before = run.data.paths.root.joinpath("run.json").read_bytes()
                store = self.app.project_manager.sessions.conversations(self.session.data)
                original, calls = store.update_metadata, 0
                observed, delivered = [], asyncio.Event()
                async def observe(current, event):
                    if event.type == EngineEventType.INTERACTION_CHANGED:
                        observed.append(((await run.aget_data()).metadata.get("resume_abandonment"),
                                         await self.session.aconversation(), event.metadata["interactions"]))
                        delivered.set()
                self.app.events.subscribe(observe)
                def fail_second(*args):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise OSError("injected conversation write failure")
                    return original(*args)
                with patch.object(store, "update_metadata", side_effect=fail_second):
                    with self.assertRaisesRegex(OSError, "injected"):
                        await self.session.adelete_turn(request_id, abandon_runs=[run.id], expected_revision=plan.revision)
                self.assertEqual(run.data.paths.root.joinpath("run.json").read_bytes(), before)
                self.assertEqual(len(await self.session.aconversation()), 2)
                await asyncio.sleep(0)
                self.assertFalse(observed)
                self.assertFalse(any(e.event == "run.resume_abandoned" for e in await self.project.aactivity()))
                self.assertEqual((await self.session.adelete_turn_plan(request_id)).revision, plan.revision)
                await self.session.adelete_turn(request_id, abandon_runs=[run.id], expected_revision=plan.revision)
                await asyncio.wait_for(delivered.wait(), 5)
                self.assertEqual(len(observed), 1)
                self.assertTrue(observed[0][0])
                self.assertFalse(observed[0][1])
                self.assertEqual(observed[0][2][0]["blocked_reason"], "resume_abandoned")
                self.assertEqual(sum(e.event == "run.resume_abandoned" for e in await self.project.aactivity()), 1)
                await self.app.shutdown()

    async def test_process_death_between_abandonment_and_message_deletion_recovers_both(self):
        run = await self.setup_graph()
        project_id, session_id, request_id = self.project.id, self.session.id, run.data.input_message_id
        before = run.data.paths.root.joinpath("run.json").read_bytes()
        await self.app.shutdown()
        script = '''
import os, sys
from llm.llm import LargeLanguageModel
from llm.components.workflows import WorkflowComponent
app = LargeLanguageModel(sys.argv[1], components=[WorkflowComponent()], engines={})
session = app.projects.load(sys.argv[2]).sessions.load(sys.argv[3])
plan = session.delete_turn_plan(sys.argv[4])
store = app.project_manager.sessions.conversations(session.data)
def crash(*args):
    os._exit(73)
store.update_metadata = crash
session.delete_turn(sys.argv[4], abandon_runs=[b.source.run_id for b in plan.blockers], expected_revision=plan.revision)
'''
        result = await asyncio.to_thread(subprocess.run,
            [sys.executable, "-c", script, str(self.root), project_id, session_id, request_id],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 73, result.stderr)
        self.app = LargeLanguageModel(self.root, components=[WorkflowComponent()], engines={"graph": self.engine})
        self.addAsyncCleanup(self.app.shutdown)
        self.session = (await self.app.projects.aload(project_id)).sessions.load(session_id)
        restored = await self.session.run.aload(run.id)
        self.assertEqual(restored.data.paths.root.joinpath("run.json").read_bytes(), before)
        self.assertEqual(len(await self.session.aconversation()), 2)
        self.assertEqual([b.source.run_id for b in (await self.session.adelete_turn_plan(request_id)).blockers], [run.id])

    async def test_confirmation_rejects_invalid_ids_and_requires_revision(self):
        run = await self.setup_graph()
        await self.session.run.shutdown()
        for ids in (run.id, [run.id, run.id], [None], [""]):
            with self.assertRaisesRegex(ValueError, "distinct Run IDs"):
                await self.session.adelete_turn(run.data.input_message_id, abandon_runs=ids)
        with self.assertRaisesRegex(ValueError, "expected_revision"):
            await self.session.adelete_turn(run.data.input_message_id, abandon_runs=[run.id])
        self.assertNotIn("resume_abandonment", run.data.metadata)

    async def test_saved_decision_invalidates_plan_and_cannot_execute_after_abandonment(self):
        run = await self.setup_graph()
        request_id = run.data.input_message_id
        old = await self.session.adelete_turn_plan(request_id)
        request, = await run.ainteractions()
        await run.arespond(request.respond(request.options[0].id))
        plan = await self.session.adelete_turn_plan(request_id)
        self.assertNotEqual(old.revision, plan.revision)
        await self.session.run.shutdown()
        with self.assertRaisesRegex(ValueError, "plan changed"):
            await self.session.adelete_turn(request_id, abandon_runs=[run.id], expected_revision=old.revision)
        receipts = await run.ainteraction_responses()
        await self.session.adelete_turn(request_id, abandon_runs=[run.id], expected_revision=plan.revision)
        self.assertEqual(await run.ainteraction_responses(), receipts)
        # Admission and execution independently reject an abandoned source, even with a receipt.
        with self.assertRaisesRegex(ValueError, "abandoned"):
            await self.session.run.resume(run.id, engine="graph")
        manager = self.app._manager(self.session.data)
        candidate = replace(run.data, metadata={"resume": {"run_id": run.id}})
        with self.assertRaisesRegex(ValueError, "abandoned"):
            manager._resume_checkpoint(self.session.data, candidate)
        self.assertFalse(self.effects)

    async def test_failed_loop_can_be_abandoned_without_calling_provider_again(self):
        provider = ScriptedCompletion([RuntimeError("test provider failure")])
        with tempfile.TemporaryDirectory() as directory:
            async with LargeLanguageModel(directory, components=[], engines={"loop": LoopEngine(completion_fn=provider)}) as app:
                project = await app.projects.acreate(components=[], config={"parameters": {"engines": {
                    "loop": {"config": {"completion": {"model": "test/model"}}}}}})
                session = await project.sessions.acreate()
                run = await (await session.run.submit("fail", engine="loop")).wait()
                self.assertEqual(run.data.status, RunStatus.FAILED)
                checkpoint = await run.acheckpoint("loop")
                plan = await session.adelete_turn_plan(run.data.input_message_id)
                self.assertEqual([b.source.run_id for b in plan.blockers], [run.id])
                await session.run.shutdown()
                await session.adelete_turn(run.data.input_message_id, abandon_runs=[run.id], expected_revision=plan.revision)
                with self.assertRaisesRegex(ValueError, "abandoned"):
                    await session.run.resume(run.id, engine="loop")
                self.assertEqual(len(provider.requests), 1)
                self.assertEqual(await run.acheckpoint("loop"), checkpoint)

    async def test_checkpoint_corruption_does_not_become_permission_to_delete(self):
        run = await self.setup_graph()
        plan = await self.session.adelete_turn_plan(run.data.input_message_id)
        await self.session.run.shutdown()
        with patch.object(self.app.run_repository, "checkpoint", side_effect=FileNotFoundError("missing")):
            with self.assertRaises(FileNotFoundError):
                await self.session.adelete_turn(run.data.input_message_id,
                    abandon_runs=[run.id], expected_revision=plan.revision)
        self.assertNotIn("resume_abandonment", run.data.metadata)
        self.assertEqual(len(await self.session.aconversation()), 2)
