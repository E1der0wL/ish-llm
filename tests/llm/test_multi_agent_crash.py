"""Real exit/ACK/fsync/fan-out interruption and original evidence protection."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from llm.core.models import RunStatus
from llm.engines.base import BaseEngine
from llm.llm import LargeLanguageModel


class MultiAgentCrashTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    async def child(self, boundary):
        root = self.root / boundary
        process = await asyncio.to_thread(subprocess.run,
            [sys.executable, "-m", "tests.llm.support.multi_agent_crash_worker", str(root), boundary],
            cwd=Path(__file__).parents[2], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=35)
        self.assertEqual(process.returncode, 91, process.stdout + process.stderr)
        case = json.loads((root / "case.json").read_text())
        self.assertEqual(case["reached"], boundary)
        return root, case

    def engine(self, root):
        async def action(context):
            with (root / "effects.txt").open("a") as stream:
                stream.write(context.run.agent_id + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            yield context.run.agent_id
        return BaseEngine(action=action)

    def effects(self, root):
        path = root / "effects.txt"
        return path.read_text().splitlines() if path.exists() else []

    async def test_before_ack_rolls_back_unaccepted_user_and_never_executes(self):
        root, case = await self.child("before_ack")
        async with LargeLanguageModel(root / "workspace", engines={"work": self.engine(root)}) as app:
            project = await app.projects.aload(case["project_id"])
            session = await project.sessions.aload(case["session_id"])
            await session.run.start()
            await session.run.wait_idle()
            self.assertEqual(await session.aconversation(), [])
            self.assertEqual(await session.run.alist(), [])
        self.assertEqual(self.effects(root), [])

    async def test_ack_and_partial_dispatch_recover_one_complete_group_once(self):
        for boundary in ("after_ack", "partial_dispatch"):
            with self.subTest(boundary=boundary):
                root, case = await self.child(boundary)
                if boundary == "partial_dispatch":
                    self.assertEqual(len(case["created_runs"]), 1)
                self.assertEqual(self.effects(root), [])
                async with LargeLanguageModel(root / "workspace", engines={"work": self.engine(root)}) as app:
                    project = await app.projects.aload(case["project_id"])
                    session = await project.sessions.aload(case["session_id"])
                    request = session.run.request(case["request_id"])
                    self.assertEqual(request.runs, [])
                    runs = await request.wait(timeout=15)
                    self.assertEqual(len(runs), 2)
                    self.assertTrue(all(run.data.status == RunStatus.COMPLETED for run in runs),
                                    [run.data.error for run in runs])
                    self.assertEqual(len(await session.run.alist()), 2)
                self.assertCountEqual(self.effects(root), ["a", "b"])

    async def test_committed_dispatch_and_effect_crashes_do_not_replay_any_recipient(self):
        cases = (("after_dispatch", [], [RunStatus.INTERRUPTED, RunStatus.INTERRUPTED]),
                 ("after_effect", ["a"], [RunStatus.INTERRUPTED, RunStatus.INTERRUPTED]),
                 ("after_finish", ["a"], [RunStatus.COMPLETED, RunStatus.INTERRUPTED]))
        for boundary, effects, statuses in cases:
            with self.subTest(boundary=boundary):
                root, case = await self.child(boundary)
                self.assertEqual(self.effects(root), effects)
                async with LargeLanguageModel(root / "workspace", engines={"work": self.engine(root)}) as app:
                    project = await app.projects.aload(case["project_id"])
                    session = await project.sessions.aload(case["session_id"])
                    await session.run.start()
                    await session.run.wait_idle()
                    request = session.run.request(case["request_id"])
                    runs = await request.wait(timeout=15)
                    self.assertEqual([run.data.status for run in runs], statuses)
                    self.assertEqual(len(await session.run.alist()), 2)
                self.assertEqual(self.effects(root), effects, "a dispatched recipient was replayed after crash")

    async def test_lost_run_or_history_evidence_blocks_recovery_without_modifying_originals(self):
        for damage in ("run", "history", "dispatch", "user", "assistant"):
            with self.subTest(damage=damage):
                # Separate actual crash trees, not a regenerated successful fixture.
                self.root = self.root / damage
                root, case = await self.child("after_dispatch")
                async with LargeLanguageModel(root / "workspace", engines={"work": self.engine(root)}) as app:
                    project = await app.projects.aload(case["project_id"])
                    session = await project.sessions.aload(case["session_id"])
                    request = session.run.request(case["request_id"])
                    if damage == "run":
                        (request.runs[0].data.paths.root / "run.json").unlink()
                    elif damage in ("user", "assistant"):
                        store = app.project_manager.sessions.conversations(session.data)
                        identifier = request.id if damage == "user" else request.runs[0].data.assistant_message_id
                        # Simulate lost bytes, rather than asking the ordinary
                        # pruning API to delete a protected pending Assistant.
                        retained = []
                        for raw in store.path.read_bytes().splitlines(keepends=True):
                            event = json.loads(raw)
                            if event.get("id", event.get("message", {}).get("id")) != identifier:
                                retained.append(raw)
                        store.path.write_bytes(b"".join(retained))
                    else:
                        store = app.project_manager.sessions.conversations(session.data)
                        admission = request.data.metadata["admission"]
                        if damage == "history":
                            del admission["dispatch"]["history"]
                        else:
                            admission["dispatch"] = None
                        with app.project_manager.ownership.scope():
                            store.update_metadata(request.id, {"admission": admission})
                    before = {p.relative_to(session.paths.root): p.read_bytes()
                              for p in session.paths.root.rglob("*") if p.is_file()}
                    with self.assertRaises((ValueError, FileNotFoundError, KeyError)):
                        await session.run.start()
                    after = {p.relative_to(session.paths.root): p.read_bytes()
                             for p in session.paths.root.rglob("*") if p.is_file()}
                    self.assertEqual(after.keys(), before.keys())
                    # A successful read preflight records session.loaded before
                    # the later corruption rejection. Preserve every prior log
                    # byte and permit only that diagnostic append, never a repair.
                    log = Path("logs/service.log")
                    self.assertTrue(after[log].startswith(before[log]))
                    added = after[log][len(before[log]):].splitlines()
                    self.assertTrue(added)
                    self.assertTrue(all(json.loads(line)["event"] == "session.loaded" for line in added))
                    self.assertEqual({path: raw for path, raw in after.items() if path != log},
                                     {path: raw for path, raw in before.items() if path != log})
                self.assertEqual(self.effects(root), [])
