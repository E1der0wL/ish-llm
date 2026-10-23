"""A reaped leader is not proof that its real descendants have terminated."""

import asyncio
import os
from pathlib import Path
import signal
import sys
import unittest
from dataclasses import replace
from tempfile import TemporaryDirectory
from unittest.mock import patch

from llm.services.infrastructure.processes import kill_process_tree, ProcessGroupExit
from llm.services.infrastructure.process_state import ProcessStat


class ProcessObservationTests(unittest.TestCase):
    def test_stat_projection_handles_parentheses_and_declares_identity(self):
        with TemporaryDirectory() as root:
            path = Path(root, "77", "stat")
            path.parent.mkdir()
            path.write_text("77 (odd ) name () " + " ".join(
                ["S", "1", "77", "77", "0", "-1", *(["0"] * 13), "456"]))
            with patch.object(ProcessStat, "ROOT", Path(root)):
                value = ProcessStat.read(77)
                self.assertEqual(value.to_dict(), {"pid": 77, "name": "odd ) name (", "state": "S",
                    "ppid": 1, "process_group": 77, "session": 77, "tty_number": 0,
                    "foreground_process_group": -1, "start_ticks": 456})
                self.assertEqual(value.identity, (77, 456))
                self.assertFalse(value.terminated)
                path.write_text("77 (broken) S")
                with self.assertRaises((ValueError, IndexError)):
                    ProcessStat.read(77)

    def test_reused_pid_and_departed_group_are_not_old_process_identity(self):
        observed = ProcessStat(77, "owned", "S", 1, 77, 77, 0, -1, 456)
        for current, active in ((observed, True), (replace(observed, state="Z"), False),
                                (replace(observed, state="X"), False),
                                (replace(observed, start_ticks=457), False),
                                (replace(observed, process_group=78), False)):
            with self.subTest(current=current), patch.object(ProcessStat, "read", return_value=current):
                self.assertIs(ProcessGroupExit.active(observed), active)
        with patch.object(ProcessStat, "read", side_effect=FileNotFoundError):
            self.assertFalse(ProcessGroupExit.active(observed))
        with patch.object(ProcessStat, "read", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                ProcessGroupExit.active(observed)


class ProcessGroupCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def group(self):
        code = ("import subprocess,sys,time; "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
                "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
                "print(child.pid,flush=True); time.sleep(60)")
        parent = await asyncio.create_subprocess_exec(sys.executable, "-I", "-c", code,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        child = int(await asyncio.wait_for(parent.stdout.readline(), 5))
        self.assertEqual(os.getpgid(child), parent.pid)
        return parent, child

    async def test_leader_exit_cannot_acknowledge_a_still_live_descendant(self):
        parent, child = await self.group()
        leader_reaped = asyncio.Event()
        class ObservedProcess:
            pid = parent.pid

            async def wait(self):
                code = await parent.wait()
                leader_reaped.set()
                return code

        real_killpg = os.killpg
        pending = None
        try:
            # Fault injection: model the kernel interval in which the leader
            # has finished but a signalled descendant has not finished exiting.
            with patch("llm.services.infrastructure.processes.os.killpg",
                       side_effect=lambda pgid, sig: os.kill(parent.pid, sig)):
                pending = asyncio.create_task(kill_process_tree(ObservedProcess()))
                await asyncio.wait_for(leader_reaped.wait(), 5)
                await asyncio.sleep(.02)
                self.assertFalse(pending.done(), "Cleanup acknowledged a live descendant")
                self.assertNotEqual(Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()[0], "Z")
                os.kill(child, signal.SIGKILL)
                await asyncio.wait_for(pending, 5)
            status = Path(f"/proc/{child}/stat")
            self.assertTrue(not status.exists() or status.read_text().rsplit(")", 1)[1].split()[0] in {"Z", "X"})
        finally:
            try:
                real_killpg(parent.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await parent.wait()
            if pending is not None:
                await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), 5)

    async def test_explicit_cleanup_deadline_includes_descendants(self):
        parent, child = await self.group()
        real_killpg = os.killpg
        try:
            with patch("llm.services.infrastructure.processes.os.killpg",
                       side_effect=lambda pgid, sig: os.kill(parent.pid, sig)):
                with self.assertRaisesRegex(RuntimeError, "cleanup deadline"):
                    await kill_process_tree(parent, timeout_seconds=.1)
            self.assertTrue(Path(f"/proc/{child}/stat").exists())
        finally:
            try:
                real_killpg(parent.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await parent.wait()
