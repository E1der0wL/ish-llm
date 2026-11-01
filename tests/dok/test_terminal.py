"""Drive the interactive preview through a real Linux PTY without executing commands."""

import errno
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import termios
import time
import unittest


class TerminalTests(unittest.TestCase):
    def test_preview_accepts_shortcut_and_exits_cleanly_on_real_pty(self):
        root = Path(__file__).resolve().parents[2]
        master, slave = pty.openpty()
        termios.tcsetwinsize(slave, (24, 120))
        process = subprocess.Popen(
            [sys.executable, "-m", "tests.dok.terminal_fixture", "--no-audio"],
            cwd=root, stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
            env=dict(os.environ, TERM="xterm-256color", PYTHONUNBUFFERED="1"),
        )
        os.close(slave)
        received = bytearray()

        def until(marker, start=0):
            deadline = time.monotonic() + 10
            while marker not in received[start:]:
                if time.monotonic() >= deadline:
                    self.fail(f"Missing {marker!r}: {bytes(received[-3000:])!r}")
                if not select.select([master], [], [], 0.1)[0]:
                    continue
                try:
                    chunk = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        self.fail(f"Preview exited early: {bytes(received)!r}")
                    raise
                received.extend(chunk)
                if b"\x1b[6n" in chunk:
                    os.write(master, b"\x1b[1;1R")

        try:
            until("플레이어 연결 안 됨".encode())
            os.write(master, b"status\x1b1")
            submitted = b"Submitted: 'git status'"
            until(submitted)
            # Wait for the next raw-mode prompt instead of sending EOF during print().
            until("플레이어 연결 안 됨".encode(), received.index(submitted) + len(submitted))
            os.write(master, b"\x04")
            self.assertEqual(process.wait(timeout=10), 0, received.decode(errors="replace"))
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            os.close(master)
