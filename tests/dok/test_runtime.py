"""Actual prompt-toolkit event loop and ish layout integration regressions."""

import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from prompt_toolkit.auto_suggest import Suggestion
from prompt_toolkit.completion import Completion, WordCompleter
from prompt_toolkit.buffer import CompletionState
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.selection import SelectionState
from prompt_toolkit.validation import ValidationError, Validator
from prompt_toolkit.widgets import Label

from dok import DockConfig, SettingsError, Shortcut, Width, install, uninstall
from .support import HostPrompt, Output, render, screen_lines, wait_running


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        audio = patch("dok.audio.PulseBackend.monitors", new=AsyncMock(return_value=()))
        audio.start()
        self.addCleanup(audio.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pipe_context = create_pipe_input()
        self.pipe = self.pipe_context.__enter__()
        self.addCleanup(self.pipe_context.__exit__, None, None, None)
        self.output = Output()
        self.prompt = HostPrompt(self.pipe, self.output, completer=WordCompleter(["candidate-one", "candidate-two"]))
        self.path = Path(self.directory.name) / "config.json"
        self.dock = install(self.prompt, config_path=self.path)
        self.task = None

    async def asyncTearDown(self):
        if self.task is not None and not self.task.done():
            self.prompt.app.exit(result="")
            await self.task
        uninstall(self.prompt)

    async def start(self, text=""):
        self.task = asyncio.create_task(self.prompt.read_and_dispatch(default=text))
        await wait_running(self.prompt.app)
        return await render(self.prompt.app)

    async def test_float_covers_completion_and_multiline_without_changing_buffer(self):
        await self.start("\n".join(f"editor-{index:02}" for index in range(30)))
        buffer = self.prompt.default_buffer
        original = buffer.document
        buffer.complete_state = CompletionState(original, [Completion("CANDIDATE")], complete_index=None)
        self.dock.show()
        visible = screen_lines(await render(self.prompt.app))
        self.assertTrue(any("플레이어 연결 안 됨" in line for line in visible), visible)
        self.assertTrue(any("editor-29" in line for line in visible), visible)
        self.assertTrue(any("HOST TOOLBAR" in line for line in visible), visible)
        self.assertEqual(buffer.document, original)
        rebuilds = self.prompt.layout_rebuilds
        self.dock.show(False)
        restored = screen_lines(await render(self.prompt.app))
        self.assertTrue(any("editor-29" in line for line in restored), restored)
        self.assertEqual(self.prompt.layout_rebuilds, rebuilds)
        self.assertEqual(buffer.document, original)

    async def test_f8_toggle_keeps_focus_and_text(self):
        await self.start("draft")
        control = self.prompt.app.layout.current_control
        self.pipe.send_bytes(b"\x1b[19~")
        async with asyncio.timeout(3):
            while not self.dock.requested_visible:
                await asyncio.sleep(0)
        self.assertEqual(self.prompt.default_buffer.text, "draft")
        self.assertIs(self.prompt.app.layout.current_control, control)
        self.dock.toggle()
        self.assertFalse(self.dock.requested_visible)

    async def test_both_completion_menus_restore_with_and_without_input_frame(self):
        from prompt_toolkit.shortcuts import CompleteStyle

        self.output.rows = 12
        await self.start("draft")
        buffer = self.prompt.default_buffer
        original = buffer.document
        for framed in (False, True):
            for style in (CompleteStyle.COLUMN, CompleteStyle.MULTI_COLUMN):
                with self.subTest(framed=framed, style=style):
                    self.prompt.show_frame = framed
                    self.prompt.complete_style = style
                    completion = CompletionState(original, [Completion(f"CAND-{index:03}") for index in range(200)])
                    buffer.complete_state = completion
                    self.dock.show(False)
                    behind = screen_lines(await render(self.prompt.app))
                    self.dock.show()
                    front = screen_lines(await render(self.prompt.app))
                    title = next(index for index, line in enumerate(front) if "플레이어 연결 안 됨" in line)
                    strip = slice(title - 2, title + 4)
                    self.assertTrue(any("CAND-" in line for line in behind[strip]), behind)
                    self.assertTrue(any("CAND-" in line for line in front[strip]), front)
                    self.assertEqual(front[title + 3], behind[title + 3])
                    self.assertTrue(any("HOST TOOLBAR" in line for line in front))
                    self.assertIs(buffer.complete_state, completion)
                    self.assertEqual(buffer.document, original)
                    self.dock.show(False)
                    restored = screen_lines(await render(self.prompt.app))
                    self.assertEqual(restored, behind)

    async def test_shortcut_submits_whole_buffer_and_records_combined_history(self):
        self.dock.configure(DockConfig(shortcuts=(Shortcut(1, "git"),)))
        await self.start("status --short")
        self.prompt.default_buffer.cursor_position = 3
        self.prompt.default_buffer.suggestion = Suggestion(" --ignored")
        self.assertTrue(self.dock.execute(1))
        self.assertFalse(self.dock.execute(1))
        result = await asyncio.wait_for(self.task, timeout=3)
        self.assertEqual(result, "git status --short")
        self.assertIn(result, self.prompt.history.get_strings())
        self.assertFalse(self.dock.visible)

    async def test_command_validation_failure_restores_document_and_allows_retry(self):
        self.dock.configure(DockConfig(shortcuts=(Shortcut(1, "git"),)))
        await self.start("status")
        buffer = self.prompt.default_buffer
        buffer.set_document(Document("status", cursor_position=2, selection=SelectionState(0)))
        original = buffer.document
        seen = []

        class Reject(Validator):
            def validate(self, document):
                seen.append(document.text)
                raise ValidationError(message="Rejected combined command")

        self.prompt.validator = Reject()
        self.assertFalse(self.dock.execute(1))
        self.assertEqual(buffer.document, original)
        self.assertIs(buffer.selection_state, original.selection)
        self.assertIn("git status", seen)
        self.assertIn("Rejected", self.dock.diagnostic)
        self.assertFalse(self.task.done())
        self.prompt.validator = None
        self.assertTrue(self.dock.execute(1))
        self.assertEqual(await self.task, "git status")

    async def test_host_transport_rejection_is_not_bypassed(self):
        from ish.shell.input import InputRejected

        self.dock.configure(DockConfig(shortcuts=(Shortcut(1, "git"),)))
        await self.start("status")
        def reject(text):
            self.assertEqual(text, "git status")
            raise InputRejected("transport refused")
        self.prompt.transport_check = reject
        self.assertFalse(self.dock.execute(1))
        self.assertEqual(self.prompt.default_buffer.text, "status")
        self.assertIn("transport refused", self.dock.diagnostic)

    async def test_reinstall_from_rc_worker_preserves_identity_state_and_buffer(self):
        await self.start("draft")
        self.dock.show()
        before = self.prompt.layout_rebuilds
        original = self.dock.config
        new = replace(original, toggle_key=("f9",), width=Width(mode="ratio", value=0.6),
                      shortcuts=(Shortcut(1, "rg", emoji="🔎", key=("escape", "1")),))
        from dok import ConfigStore
        ConfigStore(self.path).save(new)
        other = await asyncio.to_thread(install, self.prompt, config_path=self.path)
        self.assertIs(other, self.dock)
        self.assertEqual(other.config, new)
        self.assertTrue(other.requested_visible)
        self.assertEqual(self.prompt.default_buffer.text, "draft")
        self.assertEqual(self.prompt.layout_rebuilds, before)
        self.assertEqual(len(self.prompt.floats), 1)

    async def test_bad_reload_preserves_settings_and_host_key_overlap_is_allowed(self):
        await self.start("draft")
        original = self.dock.config
        self.path.write_text('{"width":{"mode":"ratio","value":2}}')
        self.assertFalse(self.dock.reload())
        self.assertEqual(self.dock.config, original)
        self.prompt.set_key("f9", handler=lambda event: None)
        updated = replace(original, toggle_key=("f9",))
        self.dock.configure(updated)
        self.assertEqual(self.dock.config, updated)
        self.assertEqual(self.prompt.default_buffer.text, "draft")

    async def test_ctrl_shortcut_overrides_host_only_while_visible(self):
        called = []
        self.prompt.set_key("c-g", handler=lambda event: called.append("host"), eager=True)
        original = self.prompt.app.key_bindings
        self.dock.configure(DockConfig(shortcuts=(Shortcut(1, "git", key=("c-g",)),)))
        await self.start("status")
        self.pipe.send_bytes(b"\x07")
        async with asyncio.timeout(3):
            while not called:
                await asyncio.sleep(0)
        self.assertEqual(self.prompt.default_buffer.text, "status")
        self.dock.show()
        await render(self.prompt.app)
        self.pipe.send_bytes(b"\x07")
        self.assertEqual(await asyncio.wait_for(self.task, 3), "git status")
        self.assertEqual(called, ["host"])
        await self.start("next")
        self.dock.show(False)
        self.pipe.send_bytes(b"\x07")
        async with asyncio.timeout(3):
            while len(called) != 2:
                await asyncio.sleep(0)
        self.dock.uninstall()
        self.assertIsNot(self.prompt.app.key_bindings, original)
        self.pipe.send_bytes(b"\x07")
        async with asyncio.timeout(3):
            while len(called) != 3:
                await asyncio.sleep(0)

    async def test_ctrl_sequence_shadows_eager_host_prefix_without_deleting_it(self):
        called = []
        handler = lambda event: called.append("host")
        self.prompt.set_key("c-x", handler=handler, eager=True)
        self.dock.configure(DockConfig(shortcuts=(Shortcut(1, "git", key=("c-x", "c-g")),)))
        await self.start("status")
        self.dock.show()
        await render(self.prompt.app)
        self.pipe.send_bytes(b"\x18\x07")
        self.assertEqual(await asyncio.wait_for(self.task, 3), "git status")
        self.assertEqual(called, [])
        self.assertTrue(any(binding.handler is handler for binding in self.prompt.key_bindings.bindings))

    async def test_small_width_temporarily_hides_and_restores(self):
        self.dock.configure(DockConfig(width=Width(mode="ratio", value=0.6)))
        await self.start()
        self.dock.show()
        self.output.columns = 60  # 36 cells, below compact shortcut width.
        small = screen_lines(await render(self.prompt.app))
        self.assertFalse(any("플레이어" in line for line in small))
        self.assertTrue(self.dock.requested_visible)
        self.output.columns = 120
        large = screen_lines(await render(self.prompt.app))
        self.assertTrue(any("플레이어" in line for line in large), large)

    async def test_next_prompt_restores_visibility_and_submission_gate(self):
        self.dock.configure(DockConfig(shortcuts=(Shortcut(1, "echo"),)))
        self.dock.show()
        await self.start("first")
        self.assertTrue(self.dock.execute(1))
        self.assertEqual(await self.task, "echo first")
        await self.start("second")
        self.assertTrue(self.dock.requested_visible)
        self.assertTrue(self.dock.execute(1))
        self.assertEqual(await self.task, "echo second")

    async def test_interrupt_and_terminal_handoff_restore_shell_dock(self):
        from prompt_toolkit.application import run_in_terminal

        async def interrupted_prompt():
            try:
                await self.prompt.read_and_dispatch(default="draft")
            except KeyboardInterrupt:
                return "interrupted"

        self.dock.show()
        self.task = asyncio.create_task(interrupted_prompt())
        await wait_running(self.prompt.app)
        await render(self.prompt.app)
        self.pipe.send_bytes(b"\x03")
        self.assertEqual(await asyncio.wait_for(self.task, timeout=3), "interrupted")
        await self.start("next draft")
        await run_in_terminal(lambda: None)
        visible = screen_lines(await render(self.prompt.app))
        self.assertTrue(any("플레이어 연결 안 됨" in line for line in visible), visible)
        self.assertEqual(self.prompt.default_buffer.text, "next draft")
        self.assertTrue(self.dock.requested_visible)

    async def test_uninstall_only_removes_owned_float_and_handlers(self):
        external = self.prompt.set_float(Label("OTHER"), top=0, right=0)
        handler = lambda event: None
        self.prompt.set_key("f8", handler=handler)
        uninstall(self.prompt)
        self.assertEqual(self.prompt.floats, [external])
        self.assertTrue(any(binding.handler is handler for binding in self.prompt.key_bindings.bindings))
        uninstall(self.prompt)

    async def test_disabled_unconfigured_and_search_focus_do_not_submit(self):
        self.dock.configure(DockConfig(shortcuts=(Shortcut(1, "git", enabled=False), Shortcut(2, "rg"))))
        await self.start("draft")
        self.assertFalse(self.dock.execute(1))
        self.assertFalse(self.dock.execute(3))
        from prompt_toolkit.search import start_search
        start_search(self.prompt.app.layout.current_control)
        self.assertFalse(self.dock.execute(2))
        self.assertEqual(self.prompt.default_buffer.text, "draft")

    async def test_other_plugin_control_temporarily_hides_dock(self):
        from prompt_toolkit.widgets import TextArea

        other = TextArea(text="another plugin")
        self.prompt.set_float(other, top=0, left=0, width=50, height=10)
        await self.start("draft")
        self.dock.show()
        self.assertTrue(self.dock.visible)
        self.prompt.app.layout.focus(other)
        self.assertFalse(self.dock.visible)
        self.assertTrue(self.dock.requested_visible)
        self.prompt.app.layout.focus(self.prompt.default_buffer)
        self.assertTrue(self.dock.visible)
