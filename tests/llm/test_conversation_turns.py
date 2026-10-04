"""Turn deletion and branching retain execution evidence and storage contracts."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llm.core.models import MessageRole as Role, MessageStatus as Status, RunStatus
from llm.components.workflows import WorkflowGraph, WorkflowComponent
from llm.engines.base import BaseEngine
from llm.engines.graph import GraphEngine
from llm.llm import LargeLanguageModel
from llm.services.history.context import ConversationContextBuilder
from llm.services.runtime.checkpoints import checkpoint_message_ids
from tests.llm import test_loop_steering as steering_fixtures
from tests.llm.test_loop_steering import Model
from tests.llm.test_loop import chunk


class ConversationTurnTests(unittest.IsolatedAsyncioTestCase):
    async def test_interleaved_turns_branch_delete_and_reopen(self):
        for storage in ("file", "memory"):
            with self.subTest(storage=storage), tempfile.TemporaryDirectory() as directory:
                app = LargeLanguageModel(directory, components=[], engines={})
                try:
                    project = app.projects.create("P", components=[], conversation_storage=storage)
                    session = project.sessions.create("S")
                    store = app.project_manager.sessions.conversations(session.data)
                    first = store.create(Role.USER, "first", Status.COMMITTED, run_id="first-run")
                    second = store.create(Role.USER, "second", Status.COMMITTED, run_id="second-run")
                    store.create(Role.ASSISTANT, "first reply", Status.COMPLETED, run_id="first-run")
                    store.create(Role.ASSISTANT, "second reply", Status.COMPLETED, run_id="second-run")
                    self.assertEqual([[m.content for m in turn] for turn in session.turns()],
                                     [["first", "first reply"], ["second", "second reply"]])
                    clone = session.clone(through_message_id=first.id, title="branch")
                    self.assertEqual([m.content for m in clone.conversation()], ["first", "first reply"])
                    self.assertTrue(all(m.run_id is None for m in clone.conversation()))
                    self.assertEqual(len(session.conversation()), 4)
                    before = session.paths.conversation.read_bytes() if storage == "file" else b""
                    session.delete_turn(first.id)
                    self.assertEqual([m.content for m in session.conversation()], ["second", "second reply"])
                    self.assertEqual(len(session.conversation(include_deleted=True)), 4)
                    context = ConversationContextBuilder().for_run(store.list(), second.id)
                    self.assertEqual([m.content for m in context], ["second"])
                    self.assertEqual(len(session.clone().conversation()), 2)
                    if storage == "file":
                        self.assertTrue(session.paths.conversation.read_bytes().startswith(before))
                        app.project_manager.sessions.close_conversations()
                        self.assertEqual(len(project.sessions.load(session.id).conversation()), 2)
                    with self.assertRaises(ValueError):
                        session.clone(through_message_id=first.id)
                finally:
                    await app.shutdown()

    async def test_deletion_is_atomic_and_rejects_pending_work(self):
        with tempfile.TemporaryDirectory() as directory:
            app = LargeLanguageModel(directory, components=[], engines={})
            try:
                project = app.projects.create("P", components=[], conversation_storage="file")
                session = project.sessions.create("S")
                store = app.project_manager.sessions.conversations(session.data)
                user = store.create(Role.USER, "question", Status.COMMITTED)
                store.create(Role.ASSISTANT, "answer", Status.COMPLETED)
                original = store.update_metadata
                calls = 0
                def fail_second(*args):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        raise OSError("write failed")
                    return original(*args)
                with patch.object(store, "update_metadata", side_effect=fail_second):
                    with self.assertRaises(OSError):
                        session.delete_turn(user.id)
                self.assertEqual(len(session.conversation()), 2)
                store.create(Role.USER, "queued", Status.QUEUED)
                with self.assertRaises(ValueError):
                    session.delete_turn(user.id)
                self.assertEqual(len(session.conversation()), 3)
                self.assertFalse(hasattr(project, "logs"))
                self.assertFalse(hasattr(project, "alogs"))
            finally:
                await app.shutdown()


class TurnResumeTests(unittest.IsolatedAsyncioTestCase):
    async def setup_graph(self, storage="file", pauses=("first",)):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.effects = []
        async def work(node):
            self.effects.append(node.node_id)
            return {}
        self.engine = GraphEngine(handlers={"work": work})
        self.app = LargeLanguageModel(self.root, components=[WorkflowComponent()], engines={"graph": self.engine})
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate("P", components=["workflows"], conversation_storage=storage)
        workflow = (WorkflowGraph(entry="first").node("first", "work", pause_before="first" in pauses)
                    .node("second", "work", pause_before="second" in pauses).node("end", "end")
                    .connect("first", "second").connect("second", "end").to_dict())
        await self.project.components.workflows.acreate(workflow, identifier="flow")
        self.session = await self.project.sessions.acreate("S")
        return await (await self.session.run.submit("original", engine="graph",
                      engine_options={"workflow": "flow"})).wait(timeout=10)

    async def resume(self, run, node):
        return await (await self.session.run.resume(run.id, engine="graph",
                      decisions={'["' + node + '"]': {}})).wait(timeout=10)

    async def test_completed_resume_allows_delete_without_changing_original_evidence(self):
        for storage in ("file", "memory"):
            with self.subTest(storage=storage):
                original = await self.setup_graph(storage)
                checkpoint = await original.acheckpoint()
                original_record = (original.data.paths.root / "run.json").read_bytes()
                resumed = await self.resume(original, "first")
                self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
                await self.session.run.shutdown()
                await self.session.adelete_turn(original.data.input_message_id)
                self.assertEqual((await original.aresponse()).status, Status.PAUSED)
                self.assertEqual((original.data.paths.root / "run.json").read_bytes(), original_record)
                self.assertEqual(await original.acheckpoint(), checkpoint)
                self.assertTrue(await resumed.steps.alist())
                self.assertNotIn(original.data.input_message_id, [m.id for m in await self.session.aconversation()])
                self.assertIn(original.data.input_message_id, [m.id for m in await self.session.aconversation(include_deleted=True)])
                clone = await self.session.aclone(through_message_id=resumed.data.input_message_id)
                self.assertEqual(len(await clone.aconversation()), 2)
                if storage == "file":
                    project_id, session_id = self.project.id, self.session.id
                    await self.app.shutdown()
                    self.app = LargeLanguageModel(self.root, components=[WorkflowComponent()], engines={"graph": self.engine})
                    self.addAsyncCleanup(self.app.shutdown)
                    self.session = (await self.app.projects.aload(project_id)).sessions.load(session_id)
                    self.assertEqual(len(await self.session.aconversation()), 2)
                await self.app.shutdown()

    async def test_multi_resume_chain_is_protected_until_last_attempt_completes(self):
        first = await self.setup_graph(pauses=("first", "second"))
        second = await self.resume(first, "first")
        self.assertEqual(second.data.status, RunStatus.PAUSED)
        await self.session.run.shutdown()
        for run in (first, second):
            with self.assertRaisesRegex(ValueError, "unfinished resumable Run"):
                await self.session.adelete_turn(run.data.input_message_id)
        last = await self.resume(second, "second")
        self.assertEqual(last.data.status, RunStatus.COMPLETED, last.data.error)
        await self.session.run.shutdown()
        for run in (first, second, last):
            await self.session.adelete_turn(run.data.input_message_id)
        self.assertEqual(await self.session.aconversation(), [])
        self.assertEqual(len(await self.session.run.alist()), 3)
        self.assertEqual(self.effects, ["first", "second"])

    async def test_paused_checkpoint_only_protects_referenced_turns(self):
        paused = await self.setup_graph()
        await self.session.run.shutdown()
        store = self.app.project_manager.sessions.conversations(self.session.data)
        with self.app.project_manager.ownership.scope():
            unrelated = store.create(Role.USER, "unrelated", Status.COMMITTED)
            store.create(Role.ASSISTANT, "independent", Status.COMPLETED)
        with self.assertRaisesRegex(ValueError, "unfinished resumable Run"):
            await self.session.adelete_turn(paused.data.input_message_id)
        await self.session.adelete_turn(unrelated.id)
        self.assertEqual([m.content for m in await self.session.aconversation()], ["original", ""])

    async def test_another_unfinished_run_can_protect_a_completed_chain(self):
        original = await self.setup_graph()
        done = await self.resume(original, "first")
        later = await (await self.session.run.submit("follow up", engine="graph",
                       engine_options={"workflow": "flow"})).wait(timeout=10)
        self.assertEqual(later.data.status, RunStatus.PAUSED)
        await self.session.run.shutdown()
        for run in (original, done):
            with self.assertRaisesRegex(ValueError, "unfinished resumable Run"):
                await self.session.adelete_turn(run.data.input_message_id)
        completed = await self.resume(later, "first")
        self.assertEqual(completed.data.status, RunStatus.COMPLETED, completed.data.error)
        await self.session.run.shutdown()
        await self.session.adelete_turn(original.data.input_message_id)

    async def test_failed_resume_before_checkpoint_copy_preserves_ancestor_references(self):
        first = await self.setup_graph()
        repository = self.app.run_repository
        original = repository.record_checkpoint
        def failure(run, event):
            if "resume" in run.metadata and event.metadata["operation"] == "initialize":
                raise OSError("failure before checkpoint copy")
            return original(run, event)
        with patch.object(repository, "record_checkpoint", failure):
            failed = await self.resume(first, "first")
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        self.assertFalse(failed.data.metadata.get("checkpoints"))
        await self.session.run.shutdown()
        for run in (first, failed):
            with self.assertRaisesRegex(ValueError, "unfinished resumable Run"):
                await self.session.adelete_turn(run.data.input_message_id)
        completed = await self.resume(failed, "first")
        self.assertEqual(completed.data.status, RunStatus.COMPLETED, completed.data.error)
        await self.session.run.shutdown()
        await self.session.adelete_turn(first.data.input_message_id)

    async def test_failed_run_without_checkpoint_does_not_block_deletion(self):
        await self.setup_graph(pauses=())
        class Failure(BaseEngine):
            async def run(self, context):
                raise RuntimeError("no checkpoint")
                yield
        self.app.engines.register("failure", Failure())
        failed = await (await self.session.run.submit("fail", engine="failure")).wait(timeout=10)
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        await self.session.run.shutdown()
        await self.session.adelete_turn(failed.data.input_message_id)

    async def test_resume_plan_and_admission_reject_hidden_checkpoint_messages(self):
        first = await self.setup_graph()
        store = self.app.project_manager.sessions.conversations(self.session.data)
        with self.app.project_manager.ownership.scope():
            # 공개 삭제는 거부된다. 외부 편집/사용자 Store도 재개 경계에서 방어한다.
            store.update_metadata(first.data.input_message_id, {"conversation_deleted": True})
        before = await first.acheckpoint()
        plan = await self.session.run.resume_plan(first.id, engine="graph")
        self.assertFalse(plan.can_resume)
        self.assertTrue(any("Deleted conversation" in d.message for d in plan.blockers))
        with self.assertRaisesRegex(ValueError, "Deleted conversation"):
            await self.resume(first, "first")
        self.assertEqual(len(await self.session.run.alist()), 1)
        self.assertFalse(any(m.status == Status.QUEUED for m in store.list()))
        self.assertEqual(await first.acheckpoint(), before)
        self.assertEqual(self.effects, [])

    async def test_missing_checkpoint_fails_closed_without_deleting_messages(self):
        paused = await self.setup_graph()
        await self.session.run.shutdown()
        repository = self.app.run_repository
        with patch.object(repository, "checkpoint", side_effect=FileNotFoundError("missing")):
            with self.assertRaises(FileNotFoundError):
                await self.session.adelete_turn(paused.data.input_message_id)
        self.assertEqual(len(await self.session.aconversation()), 2)

    async def test_cyclic_resume_lineage_fails_closed(self):
        first = await self.setup_graph()
        completed = await self.resume(first, "first")
        await self.session.run.shutdown()
        with self.app.project_manager.ownership.scope():
            current = first.data
            current.metadata["resume"] = {"run_id": completed.id}
            self.app.run_repository.save(current)
        with self.assertRaises(ValueError):
            await self.session.adelete_turn(first.data.input_message_id)
        self.assertEqual(len(await self.session.aconversation()), 4)

    async def test_memory_deletion_rolls_back_and_attached_runtime_is_rejected(self):
        completed = await self.setup_graph("memory", pauses=())
        with self.assertRaisesRegex(ValueError, "runtime is attached"):
            await self.session.adelete_turn(completed.data.input_message_id)
        await self.session.run.shutdown()
        store = self.app.project_manager.sessions.conversations(self.session.data)
        original, calls = store.update_metadata, 0
        def fail_second(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("write failed")
            return original(*args)
        with patch.object(store, "update_metadata", side_effect=fail_second):
            with self.assertRaises(OSError):
                await self.session.adelete_turn(completed.data.input_message_id)
        self.assertEqual(len(await self.session.aconversation()), 2)

    async def test_activity_does_not_load_all_runs_or_steps_and_logs_api_is_absent(self):
        await self.setup_graph(pauses=())
        with patch.object(self.app.run_repository, "list", side_effect=AssertionError("full Run scan")), \
             patch.object(self.app.step_manager, "list", side_effect=AssertionError("full Step scan")):
            rows = await self.project.aactivity(limit=2)
        self.assertEqual([r.event for r in rows], ["run.completed", "run.started"])
        self.assertFalse(hasattr(self.project, "logs"))
        self.assertFalse(hasattr(self.project, "alogs"))

    def test_nested_instruction_references_are_included(self):
        checkpoint = {"header": {"input_message_id": "input", "message_ids": ["prior", "input"]}, "records": {
            "child": {"node_type": "engine_record", "engine_scope": "child",
                      "payload": {"kind": "instruction", "status": "input", "target_scope": "child",
                                  "message_ids": ["instruction"]}}}}
        self.assertEqual(checkpoint_message_ids(checkpoint), {"prior", "input", "instruction"})


class TurnInstructionTests(unittest.IsolatedAsyncioTestCase):
    setup_backend = steering_fixtures.LoopSteeringTests.setup_backend

    async def test_failed_loop_protects_and_rejects_deleted_instruction(self):
        model = Model([chunk("draft", finish="stop")], [ConnectionError("offline")])
        app, session, request, run = await self.setup_backend(model)
        instruction = await session.run.steer(run.id, "correction")
        model.release.set()
        failed = await request.wait(timeout=10)
        self.assertEqual(failed.data.status, RunStatus.FAILED)
        checkpoint = await failed.acheckpoint("loop")
        self.assertNotIn(instruction.id, checkpoint["header"]["message_ids"])
        await session.run.shutdown()
        self.assertIn(instruction.id, app.run_repository.resumable_message_ids(session.data))
        with self.assertRaisesRegex(ValueError, "unfinished resumable Run"):
            await session.adelete_turn(failed.data.input_message_id)
        with app.project_manager.ownership.scope():
            app.project_manager.sessions.conversations(session.data).update_metadata(
                instruction.id, {"conversation_deleted": True})
        with self.assertRaisesRegex(ValueError, "Deleted conversation"):
            await session.run.resume(failed.id, engine="worker")
        self.assertEqual(len(model.requests), 2)
