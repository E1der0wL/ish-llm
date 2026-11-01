"""Linux PTY size ownership and preservation of overlapping host bindings."""

from dataclasses import replace
import os
from pathlib import Path
import pty
import tempfile
import termios
import unittest

from prompt_toolkit.input import create_pipe_input

from dok import ConfigStore, DockConfig, Shortcut, Width, install, uninstall
from dok.host import HostAccess
from .support import HostPrompt, Output


class HostTests(unittest.TestCase):
    def test_width_reads_the_ish_pty_not_the_worker_environment(self):
        master, slave = pty.openpty()
        try:
            termios.tcsetwinsize(slave, (30, 160))
            with create_pipe_input() as pipe:
                prompt = HostPrompt(pipe, Output(columns=120))
                prompt.interactive_shell.master_fd = master
                access = HostAccess(prompt)
                self.assertEqual(access.pty_columns(), 160)
                termios.tcsetwinsize(slave, (30, 200))
                self.assertEqual(access.pty_columns(), 200)
                prompt.interactive_shell.master_fd = -1
                self.assertEqual(access.pty_columns(), 120)
        finally:
            os.close(master)
            os.close(slave)

    def test_initial_overlapping_key_preserves_host_binding_and_other_settings(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            prompt = HostPrompt(pipe)
            handler = lambda event: None
            prompt.set_key("f8", handler=handler)
            path = Path(directory) / "config.json"
            settings = DockConfig(width=Width(value=90), shortcuts=(Shortcut(1, "git", key=("f9",)),))
            ConfigStore(path).save(settings)
            dock = install(prompt, config_path=path)
            self.addCleanup(uninstall, prompt)
            self.assertEqual(dock.config, settings)
            self.assertEqual(dock.diagnostic, "")
            self.assertTrue(any(binding.handler is handler for binding in prompt.key_bindings.bindings))
            self.assertTrue(any(binding.keys == ("f9",) for binding in prompt.app.key_bindings.bindings))
            dock.configure(replace(settings, toggle_key=("f10",)))
            self.assertEqual(dock.diagnostic, "")
