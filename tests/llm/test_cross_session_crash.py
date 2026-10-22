"""Durable transfer recovery uses the original receipt, never effect replay."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from llm.engines.base import BaseEngine
from llm.llm import LargeLanguageModel
from llm.services.runtime.transfers import TransferLedger


class TransferCrashTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    async def exercise(self, boundary):
        root = self.root / boundary
        result = await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "tests.llm.support.transfer_crash_worker", str(root), boundary],
            capture_output=True, text=True, timeout=35, cwd=Path(__file__).parents[2])
        self.assertEqual(result.returncode, 93, result.stdout + result.stderr)
        case = json.loads((root / "case.json").read_text())
        self.assertEqual(case["reached"], boundary)
        effects = root / "effects.txt"
        self.assertEqual(effects.read_text() if effects.exists() else "", "effect\n" if boundary == "after_effect" else "")
        async def effect(context):
            with effects.open("a") as stream:
                stream.write("effect\n")
                stream.flush()
                os.fsync(stream.fileno())
            yield "done"
        async with LargeLanguageModel(root / "workspace", engines={"work": BaseEngine(action=effect)}) as app:
            project = await app.projects.aload(case["project_id"])
            source = await project.sessions.aload(case["source_id"])
            target = await project.sessions.aload(case["target_id"])
            if boundary in ("target_input", "source_receipt", "target_receipt"):
                self.assertEqual(await target.aconversation(), [])
                self.assertEqual(TransferLedger.read(source.data)["outgoing"], {})
                self.assertEqual(TransferLedger.read(target.data)["incoming"], {})
            handle = await source.exchange.send(target.id, "once", recipients=["a"], key="same")
            result = await handle.wait(timeout=15)
            self.assertEqual(len(result["requests"]), 1)
            self.assertEqual(len(result["runs"]), 1)
            self.assertEqual(result["runs"][0]["status"], "interrupted" if boundary == "after_effect" else "completed")
            if boundary in ("after_ack", "after_effect"):
                self.assertEqual(result["receipt"]["target_message_id"], case["message_id"])
            again = await source.exchange.send(target.id, "once", recipients=["a"], key="same")
            self.assertEqual((await again.asnapshot())["receipt"], result["receipt"])
        self.assertEqual(effects.read_text(), "effect\n", "transfer recovery replayed an effect")

    async def test_partial_transaction_is_rolled_back_before_same_key_reacceptance(self):
        for boundary in ("target_input", "source_receipt", "target_receipt"):
            with self.subTest(boundary=boundary):
                await self.exercise(boundary)

    async def test_committed_ack_reopens_exact_request_once(self):
        await self.exercise("after_ack")

    async def test_effect_before_completion_crash_does_not_replay(self):
        await self.exercise("after_effect")
