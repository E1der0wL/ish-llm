"""One admitted User owns an atomic recipient group and one shared history."""

import asyncio
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.core.models import MessageRole, MessageStatus, RunStatus
from llm.engines.base import BaseEngine
from llm.llm import LargeLanguageModel
from llm.services.infrastructure.storage import tree_snapshot


class MultiAgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.contexts = []
        self.effects = self.root / "effects.txt"
        async def action(context):
            self.contexts.append(context)
            with self.effects.open("a") as stream:
                stream.write(context.run.agent_id + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            yield context.run.agent_id
        self.engine = BaseEngine(action=action)
        self.app = LargeLanguageModel(self.root / "workspace", engines={"work": self.engine})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate()
        for identifier in ("a", "b"):
            await self.project.agents.acreate({"engine": "work"}, identifier=identifier)
        self.session = await self.project.sessions.acreate(agent_ids=["a", "b"])

    async def hold_dispatch(self):
        manager = self.app._manager(self.session._snapshot)
        release = asyncio.Event()
        async def worker(runtime):
            await release.wait()
        change = patch.object(manager, "_worker", worker)
        change.start()
        self.addCleanup(change.stop)
        # Release before app shutdown drains the intentionally held worker.
        self.addAsyncCleanup(self.release_worker, release)
        return manager, release

    async def release_worker(self, release):
        release.set()

    async def test_single_and_multiple_requests_have_uniform_results_and_actual_effects(self):
        request = await self.session.run.submit("shared input", recipients="all")
        self.assertEqual(request.recipients, ["a", "b"])
        runs = await request.wait(timeout=10)
        self.assertEqual([run.data.agent_id for run in runs], ["a", "b"])
        self.assertTrue(all(run.data.status == RunStatus.COMPLETED for run in runs),
                        [run.data.error for run in runs])
        self.assertCountEqual(self.effects.read_text().splitlines(), ["a", "b"])
        self.assertEqual(len({run.id for run in runs}), 2)
        self.assertEqual([run.id for run in request.runs], [run.id for run in runs])
        self.assertEqual(len(request.results), 2)
        self.assertIsNone(request.data.run_id)
        self.assertEqual([tuple(message.content for message in context.messages) for context in self.contexts],
                         [("shared input",), ("shared input",)])
        messages = await self.session.aconversation()
        self.assertEqual(sum(message.role == MessageRole.USER for message in messages), 1)
        self.assertEqual(sum(message.role == MessageRole.ASSISTANT for message in messages), 2)
        single = await self.session.run.submit("single input", recipients=["b"])
        selected = await single.wait(timeout=10)
        self.assertIsInstance(selected, list)
        self.assertEqual([run.data.agent_id for run in selected], ["b"])
        self.assertCountEqual(self.effects.read_text().splitlines(), ["a", "b", "b"])

    async def test_invalid_or_missing_selection_is_rejected_before_persistence(self):
        with self.assertRaises(TypeError):
            await self.session.run.submit("missing")
        for value in (None, [], "a", ["missing"], ["a", "a"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await self.session.run.submit("bad", recipients=value)
        self.assertEqual(await self.session.aconversation(), [])
        self.assertEqual(await self.session.run.alist(), [])
        self.assertFalse(self.effects.exists())

    def history_snapshot(self):
        root = self.session.paths.root
        return tree_snapshot(root, exclude={str(path.relative_to(root)) for path in root.rglob("*")
                                             if "logs" in path.relative_to(root).parts})

    async def test_attached_runtime_rejects_lost_history_before_new_admission_or_effect(self):
        for missing in ("assistant", "user", "admission"):
            with self.subTest(missing=missing):
                self.session = await self.project.sessions.acreate(agent_ids=["a", "b"])
                request = await self.session.run.submit("original", recipients=["a"])
                run, = await request.wait(timeout=10)
                manager = self.app._manager(self.session.data)
                store = manager._store(self.session.data)
                if missing == "admission":
                    admission = deepcopy(request.data.metadata["admission"])
                    del admission["dispatch"]
                    await self.app._storage_call(store.update_metadata, request.id, {"admission": admission})
                else:
                    identifier = run.data.assistant_message_id if missing == "assistant" else request.id
                    await self.app._storage_call(store.prune, [identifier])
                before, effects = self.history_snapshot(), self.effects.read_bytes()
                with self.assertRaises(ValueError):
                    await self.session.run.submit("must not execute", recipients=["b"])
                self.assertEqual(self.history_snapshot(), before)
                self.assertEqual(self.effects.read_bytes(), effects)

    async def test_history_loss_after_ack_rejects_dispatch_without_repair_or_effect(self):
        original = await self.session.run.submit("original", recipients=["a"])
        run, = await original.wait(timeout=10)
        manager = self.app._manager(self.session.data)
        pump, release = manager._pump, asyncio.Event()
        async def held(runtime):
            await release.wait()
            await pump(runtime)
        self.addCleanup(release.set)
        with patch.object(manager, "_pump", side_effect=held):
            request = await self.session.run.submit("acknowledged", recipients=["b"])
            self.assertEqual(request.data.status, MessageStatus.QUEUED)
            store = manager._store(self.session.data)
            await self.app._storage_call(store.prune, [run.data.assistant_message_id])
            before, effects = self.history_snapshot(), self.effects.read_bytes()
            release.set()
            with self.assertRaises(ValueError):
                await request.wait(timeout=10)
            self.assertEqual(self.history_snapshot(), before)
            self.assertEqual(self.effects.read_bytes(), effects)
            with self.assertRaises(ValueError):
                await manager.shutdown()

    async def test_retention_requires_whole_group_and_real_pruning_rolls_back(self):
        request = await self.session.run.submit("group", recipients="all")
        runs = await request.wait(timeout=10)
        await self.session.run.shutdown()
        plan = await self.project.aretention_plan("run")
        self.assertEqual({row["run_id"] for row in plan.candidates}, {run.id for run in runs})
        self.assertTrue(all(set(row["group_run_ids"]) == {run.id for run in runs} for row in plan.candidates))
        targets = [{"session_id": self.session.id, "run_id": run.id} for run in runs]
        before = self.history_snapshot()
        with self.assertRaisesRegex(ValueError, "complete recipient"):
            await self.project.aprune_history("run", targets[:1], expected_version=plan.version)
        self.assertEqual(self.history_snapshot(), before)
        original = self.app.run_repository.delete_history
        removed = []
        def fail(session, identifier):
            original(session, identifier)
            removed.append(identifier)
            raise OSError("after actual group member removal")
        with patch.object(self.app.run_repository, "delete_history", side_effect=fail):
            with self.assertRaisesRegex(OSError, "actual group"):
                await self.project.aprune_history("run", targets, expected_version=plan.version)
        self.assertEqual(len(removed), 1)
        self.assertEqual(self.history_snapshot(), before)
        self.assertEqual((await self.project.aretention_plan("run")).version, plan.version)
        await self.project.aprune_history("run", targets, expected_version=plan.version)
        self.assertEqual(await self.session.run.alist(), [])
        self.assertEqual(await self.session.aconversation(), [])
        self.assertEqual((await self.project.arecovery()).issues, [])
        self.assertCountEqual(self.effects.read_text().splitlines(), ["a", "b"])

    async def test_failed_recipient_protects_completed_sibling_from_retention(self):
        async def action(context):
            if context.run.agent_id == "b":
                raise RuntimeError("recipient failed")
            yield "done"
        self.engine.action = action
        request = await self.session.run.submit("mixed result", recipients="all")
        runs = await request.wait(timeout=10)
        self.assertEqual([run.data.status for run in runs], [RunStatus.COMPLETED, RunStatus.FAILED])
        await self.session.run.shutdown()
        plan = await self.project.aretention_plan("run")
        self.assertEqual(plan.candidates, [])
        self.assertEqual({row["run_id"] for row in plan.protected}, {run.id for run in runs})
        self.assertIn("protected_request_group", next(row for row in plan.protected
                                                     if row["run_id"] == runs[0].id)["reasons"])

    async def test_clone_and_turn_deletion_preserve_complete_original_evidence(self):
        request = await self.session.run.submit("one shared turn", recipients="all")
        runs = await request.wait(timeout=10)
        await self.session.run.shutdown()
        original = self.history_snapshot()
        clone = await self.session.aclone(through_message_id=request.id)
        copied = await clone.aconversation()
        self.assertEqual(len(copied), 3)
        self.assertTrue(all(message.run_id is None for message in copied))
        self.assertNotIn("admission", copied[0].metadata)
        self.assertEqual(copied[0].metadata["cloned_request"]["recipients"], ["a", "b"])
        self.assertEqual(await clone.run.alist(), [])
        self.assertEqual(self.history_snapshot(), original)
        plan = await self.session.adelete_turn_plan(request.id)
        self.assertEqual(len(plan.message_ids), 3)
        self.assertEqual(plan.blockers, [])
        await self.session.adelete_turn(request.id, expected_revision=plan.revision)
        self.assertEqual(await self.session.aconversation(), [])
        self.assertEqual([run.id for run in request.runs], [run.id for run in runs])
        self.assertEqual(len(self.app.project_manager.sessions.conversations(self.session.data).list()), 3)
        self.assertCountEqual(self.effects.read_text().splitlines(), ["a", "b"])

    async def test_partial_fanout_write_rolls_back_every_run_and_assistant(self):
        await self.verify_partial_dispatch()

    async def use_memory_session(self):
        self.project = await self.app.projects.acreate(conversation_storage="memory")
        for identifier in ("a", "b"):
            await self.project.agents.acreate({"engine": "work"}, identifier=identifier)
        self.session = await self.project.sessions.acreate(agent_ids=["a", "b"])

    async def test_memory_partial_dispatch_rolls_back_journal_and_run_files(self):
        await self.use_memory_session()
        await self.verify_partial_dispatch()
        self.assertFalse(self.session.paths.conversation.exists())

    async def verify_partial_dispatch(self):
        manager, release = await self.hold_dispatch()
        request = await self.session.run.submit("atomic", recipients="all")
        message = request.data
        store = manager._store(manager._active.session)
        original = deepcopy(message.metadata)
        create = manager.repository.create
        reached = []
        def create_then_fail(run):
            create(run)
            reached.append((run.agent_id, (run.paths.root / "run.json").read_bytes()))
            if len(reached) == 2:
                raise OSError("after second real Run write")
        with patch.object(manager.repository, "create", create_then_fail):
            with self.assertRaisesRegex(OSError, "second real Run"):
                await manager._io.run(manager._dispatch_request, manager._active, message)
        self.assertEqual([agent for agent, _ in reached], ["a", "b"])
        self.assertTrue(all(raw for _, raw in reached))
        self.assertEqual(request.data.metadata, original)
        self.assertEqual(request.data.status, MessageStatus.QUEUED)
        self.assertEqual(request.runs, [])
        self.assertEqual(await self.session.run.alist(), [])
        self.assertEqual(len(store.list()), 1)
        self.assertFalse(self.effects.exists())
        release.set()

    async def test_cancelled_admission_does_not_dispatch_or_reset_its_bindings(self):
        _, release = await self.hold_dispatch()
        request = await self.session.run.submit("cancel", recipients="all")
        original = deepcopy(request.data.metadata)
        self.assertTrue(await request.cancel())
        self.assertFalse(await request.cancel())
        self.assertEqual(request.data.metadata, original)
        self.assertEqual(request.data.status, MessageStatus.CANCELLED)
        self.assertEqual(request.runs, [])
        self.assertFalse(self.effects.exists())
        release.set()

    async def test_missing_admission_is_rejected_without_repair_or_effect(self):
        manager, release = await self.hold_dispatch()
        request = await self.session.run.submit("lost evidence", recipients="all")
        store = manager._store(manager._active.session)
        damaged = deepcopy(request.data.metadata["admission"])
        damaged.pop("bindings")
        await manager._io.run(store.update_metadata, request.id, {"admission": damaged})
        before = self.session.paths.conversation.read_bytes()
        with self.assertRaises(ValueError):
            await manager._io.run(manager._dispatch_request, manager._active, request.data)
        self.assertEqual(self.session.paths.conversation.read_bytes(), before)
        self.assertEqual(list(self.session.paths.runs.glob("*/run.json")), [])
        self.assertFalse(self.effects.exists())
        release.set()

    async def test_reopen_returns_all_results_without_repeating_effects(self):
        request = await self.session.run.submit("persisted", recipients="all")
        runs = await request.wait(timeout=10)
        self.assertTrue(all(run.data.status == RunStatus.COMPLETED for run in runs))
        original = self.effects.read_bytes()
        await self.app.shutdown()
        async with LargeLanguageModel(self.root / "workspace", engines={"work": self.engine}) as reopened:
            project = await reopened.projects.aload(self.project.id)
            session = await project.sessions.aload(self.session.id)
            loaded = session.run.request(request.id)
            self.assertEqual([run.id for run in await loaded.wait(timeout=10)], [run.id for run in runs])
            self.assertEqual(len(loaded.results), 2)
        self.assertEqual(self.effects.read_bytes(), original)

    async def test_memory_group_executes_once_and_missing_conversation_is_not_reconstructed(self):
        await self.use_memory_session()
        request = await self.session.run.submit("memory group", recipients="all")
        runs = await request.wait(timeout=10)
        self.assertTrue(all(run.data.status == RunStatus.COMPLETED for run in runs),
                        [run.data.error for run in runs])
        self.assertCountEqual(self.effects.read_text().splitlines(), ["a", "b"])
        self.assertFalse(self.session.paths.conversation.exists())
        original = self.effects.read_bytes()
        await self.app.shutdown()
        async with LargeLanguageModel(self.root / "workspace", engines={"work": self.engine}) as reopened:
            project = await reopened.projects.aload(self.project.id)
            session = await project.sessions.aload(self.session.id)
            self.assertEqual(await session.aconversation(), [])
            self.assertEqual(len(await session.run.alist()), 2)
            with self.assertRaises(KeyError):
                await session.run.request(request.id).wait(timeout=10)
            await session.run.start()
            await session.run.wait_idle()
            self.assertEqual(await session.aconversation(), [])
            self.assertFalse(session.paths.conversation.exists())
        self.assertEqual(self.effects.read_bytes(), original)
