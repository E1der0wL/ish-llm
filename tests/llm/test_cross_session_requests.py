"""Transport ownership, required evidence, cancellation and actual file effects."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from tests.llm.support.runtime_tools import TEST_CONTRACT
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.core.models import RunStatus
from llm.engines.base import BaseEngine
from llm.llm import LargeLanguageModel
from llm.services.composition import BackendServices
from llm.services.runtime.transfers import TransferDenied, TransferExpired, TransferLedger


class CrossSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.effects = self.root / "effects.txt"

    async def setup_app(self, action=None, authorize=None):
        async def effect(context):
            self.write_effect(context.run.agent_id)
            yield "received"
        self.app = LargeLanguageModel(self.root / "workspace", engines={"work": BaseEngine(action=action or effect)},
                                     services=BackendServices(transfer_authorize=authorize))
        self.addAsyncCleanup(self.app.shutdown)
        self.project = await self.app.projects.acreate()
        for agent in ("a", "b"):
            await self.project.agents.acreate({"engine": "work"}, identifier=agent)
        self.source = await self.project.sessions.acreate(agent_ids=["a"])
        self.target = await self.project.sessions.acreate(agent_ids=["a", "b"])

    def write_effect(self, value):
        with self.effects.open("a") as stream:
            stream.write(value + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def effects_read(self):
        return self.effects.read_text().splitlines() if self.effects.exists() else []

    async def send(self, content="hello", *, key="once", recipients=("a",), **kwargs):
        return await self.source.exchange.send(self.target.id, content, recipients=list(recipients), key=key, **kwargs)

    async def test_duplicate_concurrent_send_returns_same_frozen_admission_and_one_effect(self):
        await self.setup_app()
        handles = await asyncio.gather(*(self.send() for _ in range(5)))
        results = await asyncio.gather(*(handle.wait(timeout=10) for handle in handles))
        identifiers = {result["receipt"]["target_message_id"] for result in results}
        self.assertEqual(len(identifiers), 1)
        self.assertEqual(len(await self.target.run.alist()), 1)
        self.assertEqual(self.effects_read(), ["a"])
        self.assertEqual(results[0]["runs"][0]["status"], "completed")
        with self.assertRaisesRegex(ValueError, "conflicts"):
            await self.send("different")
        self.assertEqual(self.effects_read(), ["a"])

    async def test_ack_precedes_execution_and_caller_cancel_does_not_lose_receipt(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def action(context):
            entered.set()
            await release.wait()
            self.write_effect("one")
            yield "done"
        await self.setup_app(action)
        self.addCleanup(release.set)
        handle = await asyncio.wait_for(self.send(), 10)
        await asyncio.wait_for(entered.wait(), 10)
        snapshot = await handle.asnapshot()
        self.assertEqual(snapshot["runs"][0]["status"], "running")
        waiting = asyncio.create_task(handle.wait())
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiting
        self.assertEqual(self.effects_read(), [])
        release.set()
        result = await (await self.source.exchange.aget("once")).wait(timeout=10)
        self.assertEqual(result["runs"][0]["status"], "completed")
        self.assertEqual(self.effects_read(), ["one"])

    async def test_authorizer_is_per_action_and_ids_do_not_grant_access(self):
        denied, calls = set(), []
        def authorize(request):
            calls.append(deepcopy(request))
            return request["action"] not in denied
        await self.setup_app(authorize=authorize)
        denied.add("send")
        with self.assertRaises(TransferDenied):
            await self.send()
        self.assertEqual(await self.target.aconversation(), [])
        denied.clear()
        result = await (await self.send()).wait(timeout=10)
        for action, operation in (("query", lambda: self.source.exchange.aget("once")),
                                  ("inspect", lambda: self.source.exchange.ainspect(self.target.id))):
            denied.add(action)
            with self.assertRaises(TransferDenied):
                await operation()
            denied.clear()
        handle = await self.source.exchange.aget("once")
        denied.add("cancel")
        with self.assertRaises(TransferDenied):
            await handle.cancel()
        self.assertTrue({"send", "execute", "query", "inspect", "cancel"} <= {v["action"] for v in calls})
        other = await self.app.projects.acreate()
        other_session = await other.sessions.acreate()
        with self.assertRaises((ValueError, FileNotFoundError)):
            await self.source.exchange.send(other_session.id, "no", recipients=["a"], key="foreign")
        self.assertEqual(self.effects_read(), ["a"])
        self.assertNotIn("policies", result["receipt"])

    async def test_execute_revocation_has_zero_effect_and_no_implicit_retry(self):
        await self.setup_app(authorize=lambda request: request["action"] != "execute")
        handle = await self.send()
        result = await handle.wait(timeout=10)
        self.assertEqual(result["runs"][0]["status"], "failed")
        self.assertEqual(result["runs"][0]["error_code"], "transfer_denied")
        await (await self.send()).wait(timeout=10)
        self.assertEqual(len(await self.target.run.alist()), 1)
        self.assertEqual(self.effects_read(), [])

    async def test_deadline_rejects_before_ack_and_interrupts_active_work(self):
        entered = asyncio.Event()
        async def action(context):
            entered.set()
            await asyncio.Event().wait()
            self.write_effect("forbidden")
            yield "forbidden"
        await self.setup_app(action)
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with self.assertRaises(TransferExpired):
            await self.send(deadline=expired)
        self.assertEqual(await self.target.aconversation(), [])
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
        handle = await self.send(deadline=deadline)
        await asyncio.wait_for(entered.wait(), 10)
        result = await handle.wait(timeout=10)
        self.assertEqual(result["runs"][0]["status"], "failed")
        self.assertEqual(self.effects_read(), [])
        # Idempotent observation after expiry returns the accepted request.
        self.assertEqual((await (await self.send(deadline=deadline)).asnapshot())["receipt"], result["receipt"])

    async def test_ancestry_rejects_cycle_with_actual_source_run(self):
        await self.setup_app()
        first = await (await self.send()).wait(timeout=10)
        run_id = first["runs"][0]["run_id"]
        with self.assertRaisesRegex(ValueError, "cycle"):
            await self.target.exchange.send(self.source.id, "cycle", recipients=["a"], key="cycle", source_run_id=run_id)
        third = await self.project.sessions.acreate(agent_ids=["a"])
        next_handle = await self.target.exchange.send(third.id, "next", recipients=["a"], key="next", source_run_id=run_id)
        value = (await next_handle.wait(timeout=10))["receipt"]
        self.assertEqual(value["chain"], [self.source.id, self.target.id, third.id])
        self.assertEqual(value["source"]["agent_id"], "a")
        self.assertEqual(self.effects_read(), ["a", "a"])

    async def test_selected_pending_cancel_preserves_running_sibling_and_fifo(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def action(context):
            if context.run.agent_id == "a" and context.messages[-1].content == "occupy":
                entered.set()
                await release.wait()
            self.write_effect(context.run.agent_id + ":" + context.messages[-1].content)
            yield "done"
        await self.setup_app(action)
        self.addCleanup(release.set)
        occupying = await self.target.run.submit("occupy", recipients=["a"])
        await asyncio.wait_for(entered.wait(), 10)
        handle = await self.send(recipients=("a", "b"))
        # B completion proves the atomic group exists while A remains pending.
        await self.app._manager(self.target._snapshot)._io.run(lambda: None)
        for _ in range(100):
            snapshot = await handle.asnapshot()
            if any(r["agent_id"] == "b" and r["status"] == "completed" for r in snapshot["runs"]):
                break
            await asyncio.sleep(0.01)
        self.assertEqual(len(snapshot["runs"]), 2)
        pending = next(r for r in snapshot["runs"] if r["agent_id"] == "a")
        self.assertEqual(pending["status"], "pending")
        self.assertTrue(await handle.cancel(run_id=pending["run_id"]))
        result = await handle.wait(timeout=10)
        self.assertCountEqual([r["status"] for r in result["runs"]], ["cancelled", "completed"])
        self.assertEqual(self.effects_read(), ["b:hello"])
        release.set()
        await occupying.wait(timeout=10)
        self.assertEqual(self.effects_read(), ["b:hello", "a:occupy"])

    async def test_active_selected_cancel_does_not_cancel_other_recipient(self):
        entered = {name: asyncio.Event() for name in ("a", "b")}
        release = asyncio.Event()
        async def action(context):
            entered[context.run.agent_id].set()
            await release.wait()
            self.write_effect(context.run.agent_id)
            yield "done"
        await self.setup_app(action)
        self.addCleanup(release.set)
        handle = await self.send(recipients=("a", "b"))
        await asyncio.wait_for(asyncio.gather(*(v.wait() for v in entered.values())), 10)
        rows = (await handle.asnapshot())["runs"]
        self.assertTrue(await handle.cancel(run_id=next(r["run_id"] for r in rows if r["agent_id"] == "a")))
        release.set()
        result = await handle.wait(timeout=10)
        self.assertCountEqual([r["status"] for r in result["runs"]], ["interrupted", "completed"])
        self.assertEqual(self.effects_read(), ["b"])
        self.assertFalse(await handle.cancel())

    async def test_required_ledger_loss_is_not_empty_and_source_bytes_are_preserved(self):
        await self.setup_app()
        await (await self.send()).wait(timeout=10)
        await self.target.run.shutdown()
        path = TransferLedger.path(self.source.data)
        original = path.read_bytes()
        path.unlink()
        before = {p: p.read_bytes() for p in self.project.paths.root.rglob("*") if p.is_file() and "logs" not in p.parts}
        for operation in (lambda: self.source.exchange.aget("once"), lambda: self.send(), lambda: self.source.adelete(permanent=True)):
            with self.assertRaises((ValueError, FileNotFoundError, KeyError)):
                await operation()
        after = {p: p.read_bytes() for p in self.project.paths.root.rglob("*") if p.is_file() and "logs" not in p.parts}
        self.assertEqual(before, after)
        path.write_bytes(original)
        self.assertEqual(self.effects_read(), ["a"])

    async def test_inventory_entry_loss_or_original_edit_blocks_query_and_duplicate(self):
        await self.setup_app()
        handle = await self.send()
        result = await handle.wait(timeout=10)
        await self.target.run.shutdown()
        source = self.source.data
        path = TransferLedger.path(source)
        original = path.read_bytes()
        ledger = TransferLedger.read(source).to_dict()
        ledger["outgoing"].clear()
        with self.app.project_manager.ownership.scope():
            TransferLedger.write(source, ledger)
        with self.assertRaisesRegex(ValueError, "inventories disagree"):
            await self.send()
        path.write_bytes(original)
        store = self.app.project_manager.sessions.conversations(self.target.data)
        original_journal = store.path.read_bytes()
        changed = original_journal.replace(b'"content": "hello"', b'"content": "other"', 1)
        self.assertNotEqual(changed, original_journal)
        self.assertEqual(len(changed), len(original_journal))
        store.path.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, "target differs"):
            await handle.asnapshot()
        self.assertEqual(self.effects_read(), ["a"])

    async def test_retained_links_protect_delete_and_clone_only_copies_observations(self):
        await self.setup_app()
        handle = await self.send()
        result = await handle.wait(timeout=10)
        await self.target.run.shutdown()
        for session in (self.source, self.target):
            for permanent in (False, True):
                with self.assertRaisesRegex(ValueError, "retained transfer"):
                    await session.adelete(permanent=permanent)
        with self.assertRaisesRegex(ValueError, "retained transfer"):
            await self.target.adelete_turn(result["receipt"]["target_message_id"])
        clone = await self.target.aclone()
        messages = await clone.aconversation()
        self.assertTrue(any("cloned_transfer" in m.metadata for m in messages))
        self.assertFalse(any("transfer" in m.metadata for m in messages))
        self.assertEqual(TransferLedger.read(clone.data).to_dict()["incoming"], {})
        await clone.run.start()
        await clone.run.wait_idle()
        self.assertEqual(self.effects_read(), ["a"])

    async def test_ledger_write_failure_rolls_back_input_and_both_owners(self):
        await self.setup_app()
        write = TransferLedger.write
        def fail(session, value):
            if value["incoming"]:
                raise OSError("injected target ledger write failure")
            return write(session, value)
        with patch.object(TransferLedger, "write", side_effect=fail), self.assertRaises(OSError):
            await self.send()
        self.assertEqual(await self.target.aconversation(), [])
        self.assertEqual(TransferLedger.read(self.source.data).to_dict()["outgoing"], {})
        self.assertEqual(TransferLedger.read(self.target.data).to_dict()["incoming"], {})
        self.assertEqual(self.effects_read(), [])
        await (await self.send()).wait(timeout=10)
        self.assertEqual(self.effects_read(), ["a"])

    async def test_cancel_during_ack_drains_the_transaction_and_preserves_one_effect(self):
        import threading
        from llm.services.runtime.transfers import TransferAdmission
        await self.setup_app()
        entered, release = threading.Event(), threading.Event()
        persist = TransferAdmission.persist
        def held(admission, message):
            result = persist(admission, message)
            entered.set()
            if not release.wait(10):
                raise AssertionError("ACK test was not released")
            return result
        with patch.object(TransferAdmission, "persist", held):
            sending = asyncio.create_task(self.send())
            self.assertTrue(await asyncio.to_thread(entered.wait, 10))
            sending.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await sending
        result = await (await self.source.exchange.aget("once")).wait(timeout=10)
        self.assertEqual(len(result["runs"]), 1)
        self.assertEqual(self.effects_read(), ["a"])

    async def test_backup_roundtrip_and_retention_keep_both_required_owners(self):
        await self.setup_app()
        result = await (await self.send()).wait(timeout=10)
        await self.target.run.shutdown()
        for unit in ("run", "session"):
            plan = await self.project.aretention_plan(unit)
            self.assertEqual(plan.candidates, [])
        archive = await self.project.abackup(self.root / "archive")
        async with LargeLanguageModel(self.root / "restored", engines={"work": BaseEngine()}) as restored:
            project = await restored.projects.arestore_backup(archive)
            source = await project.sessions.aload(self.source.id)
            handle = await source.exchange.aget("once")
            self.assertEqual((await handle.asnapshot())["receipt"], result["receipt"])
            self.assertEqual((await handle.wait(timeout=10))["runs"], result["runs"])
        self.assertEqual(self.effects_read(), ["a"])

    async def test_target_approval_and_resume_preserve_receipt_and_actual_effect_once(self):
        from llm.components.tools.registry import ToolContract
        from tests.llm.test_long_running import LongRunningTests
        from tests.llm.test_loop import chunk, call
        async def effect(arguments):
            self.write_effect("approved")
            return "saved"
        app, target, model = await LongRunningTests.setup_app(self, effect, [
            [chunk(calls=[call('{}', name='act')], finish='tool_calls')], [chunk('done', finish='stop')]],
            tool_contract=replace(TEST_CONTRACT, approval_required=True))
        source = await target.project.sessions.acreate()
        handle = await source.exchange.send(target.id, "save", recipients=["loop"], key="approval")
        initial = await handle.wait(timeout=10)
        self.assertEqual(initial["runs"][0]["status"], "paused")
        self.assertEqual(self.effects_read(), [])
        run = await target.run.aload(initial["runs"][0]["run_id"])
        interaction, = await run.ainteractions(pending_only=True)
        await run.arespond(interaction.respond("approve"))
        await handle.resume(run.id, agent_id="loop")
        result = await handle.wait(timeout=10)
        self.assertEqual([r["status"] for r in result["runs"]], ["paused", "completed"])
        self.assertEqual(result["receipt"], initial["receipt"])
        self.assertEqual(self.effects_read(), ["approved"])
        self.assertEqual(len(model.requests), 2)
        with self.assertRaises(ValueError):
            await handle.resume(run.id, agent_id="loop")
        with self.assertRaisesRegex(ValueError, "cycle"):
            await target.exchange.send(source.id, "back", recipients=["loop"], key="cycle", source_run_id=result["runs"][-1]["run_id"])
        self.assertEqual(self.effects_read(), ["approved"])

    async def test_expiry_cannot_be_reset_by_direct_resume_after_approval(self):
        from llm.components.tools.registry import ToolContract
        from tests.llm.test_long_running import LongRunningTests
        from tests.llm.test_loop import chunk, call
        async def effect(arguments):
            self.write_effect("forbidden")
            return "saved"
        app, target, model = await LongRunningTests.setup_app(self, effect,
            [[chunk(calls=[call('{}', name='act')], finish='tool_calls')]],
            tool_contract=replace(TEST_CONTRACT, approval_required=True))
        source = await target.project.sessions.acreate()
        deadline = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
        handle = await source.exchange.send(target.id, "save", recipients=["loop"], key="expiry", deadline=deadline)
        initial = await handle.wait(timeout=10)
        run = await target.run.aload(initial["runs"][0]["run_id"])
        self.assertEqual(run.data.status, "paused")
        interaction, = await run.ainteractions(pending_only=True)
        await run.arespond(interaction.respond("approve"))
        # The durable deadline is unchanged; only the clock crossing is injected.
        with patch("llm.services.runtime.transfer_values.TransferDeadline.remaining",
                   side_effect=TransferExpired("expired")) as clock:
            for operation in (lambda: handle.resume(run.id, agent_id="loop"),
                              lambda: target.run.resume(run.id, agent_id="loop")):
                with self.assertRaises(ValueError):
                    await operation()
            self.assertFalse((await target.run.resume_plan(run.id, agent_id="loop")).can_resume)
            self.assertGreaterEqual(clock.call_count, 3, "Expiry was not checked on every resume path")
        self.assertEqual(len(await target.run.alist()), 1)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(self.effects_read(), [])

    async def test_lost_provenance_cannot_turn_transfer_into_ordinary_execution(self):
        await self.setup_app()
        manager = self.app._manager(self.target._snapshot)
        original_dispatch = manager._dispatch_request
        def damaged(runtime, message):
            runs = original_dispatch(runtime, message)
            for run in runs:
                run.metadata.pop("transfer")
                manager.repository.save(run)
            store = manager._store(runtime.session)
            store.update_metadata(message.id, {"transfer": None})
            return runs
        with patch.object(manager, "_dispatch_request", side_effect=damaged):
            handle = await self.send()
            # Query must reject; direct owner waiting still proves execution was blocked.
            rows = await self.target.run.alist()
            while not rows:
                await asyncio.sleep(0.01)
                rows = await self.target.run.alist()
            runs = await manager.wait_request(rows[0].data.input_message_id, timeout=10)
            self.assertEqual([r.status for r in runs], [RunStatus.FAILED])
            with self.assertRaises(ValueError):
                await handle.asnapshot()
        self.assertEqual(self.effects_read(), [])

    async def test_invalid_authorizer_and_nonmember_do_not_admit(self):
        await self.setup_app(authorize=lambda request: 1)
        with self.assertRaises(TypeError):
            await self.send()
        self.app.project_manager.sessions.transfers.authorize = None
        with self.assertRaises(ValueError):
            await self.send(recipients=("missing",))
        for recipients in ([], ["a", "a"], None, "all"):
            with self.subTest(recipients=recipients), self.assertRaises((ValueError, TypeError)):
                await self.source.exchange.send(self.target.id, "no", recipients=recipients, key="bad")
        self.assertEqual(await self.target.aconversation(), [])
        self.assertEqual(TransferLedger.read(self.source.data).to_dict()["outgoing"], {})
        self.assertEqual(self.effects_read(), [])

    async def test_schema_fields_versions_and_cross_owner_evidence_are_required(self):
        from llm.services.runtime.transfers import TransferReceipt
        from llm.services.infrastructure.storage import revision_token
        await self.setup_app()
        value = (await (await self.send()).wait(timeout=10))["receipt"]
        for field in value:
            candidate = deepcopy(value)
            del candidate[field]
            with self.subTest(field=field), self.assertRaises((ValueError, TypeError)):
                TransferReceipt.read(candidate)
        for version in (True, 1.0, None, "1"):
            candidate = {**value, "schema_version": version}
            candidate["revision"] = revision_token({k: v for k, v in candidate.items() if k != "revision"})
            with self.subTest(version=version), self.assertRaises(ValueError):
                TransferReceipt.read(candidate)
        self.assertEqual(TransferReceipt.read(value).to_dict(), value)
        with self.assertRaises(ValueError):
            TransferLedger.validate(self.target.data, TransferLedger.read(self.source.data).to_dict())

    async def test_memory_storage_cannot_claim_durable_transport(self):
        await self.setup_app()
        project = await self.app.projects.acreate(conversation_storage="memory")
        await project.agents.acreate({"engine": "work"}, identifier="a")
        source = await project.sessions.acreate()
        target = await project.sessions.acreate(agent_ids=["a"])
        with self.assertRaisesRegex(ValueError, "portable file"):
            await source.exchange.send(target.id, "no", recipients=["a"], key="volatile")
        self.assertEqual(await target.aconversation(), [])
        self.assertEqual(self.effects_read(), [])

    async def test_frozen_target_selection_survives_membership_change_on_duplicate(self):
        await self.setup_app()
        result = await (await self.send(recipients=("a", "b"))).wait(timeout=10)
        await self.target.run.shutdown()
        await self.target.asave(agent_ids=["a"])
        duplicate = await self.send(recipients=("a", "b"))
        self.assertEqual((await duplicate.asnapshot())["receipt"], result["receipt"])
        with self.assertRaises(ValueError):
            await self.send(recipients=("a", "b"), key="new")
        self.assertCountEqual(self.effects_read(), ["a", "b"])

    async def test_checksum_valid_archive_with_missing_transfer_inventory_is_rejected(self):
        import shutil
        from llm.services.infrastructure.backups import DirectoryBackups
        await self.setup_app()
        await (await self.send()).wait(timeout=10)
        await self.target.run.shutdown()
        archive = await self.project.abackup(self.root / "archive-original")
        damaged = self.root / "damaged"
        shutil.copytree(archive / "project", damaged)
        (damaged / "sessions" / self.target.id / "state" / TransferLedger.FILE).unlink()
        archive = DirectoryBackups().create(damaged, self.root / "archive-damaged", metadata={"project_id": self.project.id})
        DirectoryBackups().verify(archive)
        async with LargeLanguageModel(self.root / "destination", engines={"work": BaseEngine()}) as destination:
            existing = await destination.projects.acreate("keep")
            before = {p.relative_to(destination.workspace): p.read_bytes() for p in destination.workspace.rglob("*") if p.is_file()}
            with self.assertRaises((ValueError, FileNotFoundError)):
                await destination.projects.arestore_backup(archive)
            after = {p.relative_to(destination.workspace): p.read_bytes() for p in destination.workspace.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(existing.data.title, "keep")
        self.assertEqual(self.effects_read(), ["a"])

    async def test_repository_update_requires_original_revision_and_existing_ledger(self):
        await self.setup_app()
        source = self.source.data
        repository = self.app.project_manager.sessions.repository
        stale = repository.read_transfers(source)
        await (await self.send()).wait(timeout=10)
        await self.target.run.shutdown()
        path = TransferLedger.path(source)
        original = path.read_bytes()
        current = repository.read_transfers(source)
        with self.app.project_manager.ownership.scope(), self.assertRaisesRegex(ValueError, "changed"):
            repository.write_transfers(source, stale)
        self.assertEqual(path.read_bytes(), original)
        path.unlink()
        with self.app.project_manager.ownership.scope(), self.assertRaises(FileNotFoundError):
            repository.write_transfers(source, current)
        self.assertFalse(path.exists(), "Update silently initialized a missing required ledger")
        path.write_bytes(original)
        self.assertEqual(self.effects_read(), ["a"])
