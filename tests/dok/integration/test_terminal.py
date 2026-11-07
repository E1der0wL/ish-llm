"""Drive the interactive preview through a real Linux PTY without executing commands."""

import errno
import json
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
        root = Path(__file__).resolve().parents[3]
        master, slave = pty.openpty()
        probe_read, probe_write = os.pipe()
        termios.tcsetwinsize(slave, (32, 120))
        process = subprocess.Popen(
            [sys.executable, "-m", "tests.dok.terminal_fixture", "--offline"],
            cwd=root, stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
            env=dict(os.environ, TERM="xterm-256color", PYTHONUNBUFFERED="1", DOK_TEST_POPUP_FD=str(probe_write)),
            pass_fds=(probe_write,),
        )
        os.close(slave)
        os.close(probe_write)
        received = bytearray()
        observations = bytearray()

        def popup_state(visible, timeout=3):
            deadline = time.monotonic() + timeout
            while True:
                while b"\n" in observations:
                    line, _, remaining = observations.partition(b"\n")
                    observations[:] = remaining
                    if json.loads(line)["visible"] == visible:
                        return
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0, f"Popup did not become {visible=}")
                ready = select.select([probe_read, master], [], [], remaining)[0]
                if master in ready:
                    chunk = os.read(master, 65536)
                    received.extend(chunk)
                    if b"\x1b[6n" in chunk:
                        os.write(master, b"\x1b[1;1R")
                if probe_read in ready:
                    chunk = os.read(probe_read, 4096)
                    self.assertTrue(chunk, "Popup observation pipe closed")
                    observations.extend(chunk)

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
            until("Player disconnected".encode())
            until("--°".encode())  # Unknown temperatures with no configured location.
            start = len(received)
            os.write(master, b"\x1bc")  # Alt+C: focused calendar.
            until(b"Today", start)
            popup_state(True)
            until(b"\x1b[?1006h", start)
            start = len(received)
            started = time.monotonic()
            os.write(master, b"\x1b")
            popup_state(False, .5)
            calendar_latency = time.monotonic() - started
            until(b"\x1b[?1006l", start)
            start = len(received)
            os.write(master, b"\x1bw")  # Alt+W: daily weather details.
            until(b"Location not configured", start)
            popup_state(True)
            until(b"\x1b[?1006h", start)
            start = len(received)
            started = time.monotonic()
            os.write(master, b"\x1b")
            popup_state(False, .5)
            weather_latency = time.monotonic() - started
            until(b"\x1b[?1006l", start)
            print(f"ESC PTY latency: calendar={calendar_latency*1000:.1f} ms, weather={weather_latency*1000:.1f} ms", flush=True)
            os.write(master, b"\x17")  # Ctrl+W: hide.
            os.write(master, b"\x17")  # Ctrl+W: reopen at the available size.
            os.write(master, b"status\x1b1")
            submitted = b"Submitted: 'git status'"
            until(submitted)
            # Wait for the next raw-mode prompt instead of sending EOF during print().
            until("Player disconnected".encode(), received.index(submitted) + len(submitted))
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
            os.close(probe_read)
