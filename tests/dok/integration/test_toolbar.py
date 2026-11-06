"""Real ish prompt layout, selection and toolbar ownership integration."""
import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from prompt_toolkit.application.current import set_app
from prompt_toolkit.document import Document
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.selection import SelectionState, SelectionType

from dok import DockConfig, DockMode, install, uninstall
from dok.integrations.ish.status import PromptStatus
from dok.features.toolbar.editor import NumberCalculator, NumberScope
from dok.features.system.models import SystemReading, NetworkCounter
from dok.rendering.text import width
from tests.dok.support import HostPrompt, Output, render, screen_lines, wait_running


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


class ToolbarIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.pipe_context = create_pipe_input()
        self.pipe = self.pipe_context.__enter__()
        self.addCleanup(self.pipe_context.__exit__, None, None, None)
        self.output = Output(columns=240, rows=40)
        self.prompt = HostPrompt(self.pipe, self.output)
        self.original_style = self.prompt.style
        self.toolbar_window = next(window for window in self.prompt.app.layout.find_all_windows()
                                   if window.style == "class:bottom-toolbar")
        self.original_control = self.toolbar_window.content
        self.original_wrap = self.toolbar_window.wrap_lines
        self.dock = install(self.prompt, config_path=Path(self.directory.name) / "config.jsonc")
        self.dock.configure(DockConfig(audio={"enabled": False}, media={"enabled": False}))
        self.calls = 0
        def read(interfaces):
            self.calls += 1
            return SystemReading(self.calls, 100 * self.calls, 20 * self.calls, 1024**3, 1024**2,
                                 (NetworkCounter("eth0", self.calls * 1000, self.calls * 500),))
        self.dock._system._backend = SimpleNamespace(read=read)
        self.task = None

    async def asyncTearDown(self):
        if self.task is not None and not self.task.done():
            self.prompt.app.exit(result="")
            await self.task
        uninstall(self.prompt)
        await until(lambda: not self.dock._system._task.running)

    async def start(self, text=""):
        self.task = asyncio.create_task(self.prompt.read_and_dispatch(default=text))
        await wait_running(self.prompt.app)
        await render(self.prompt.app)

    async def toolbar_line(self):
        # A float's actual visibility is known at the end of its paint pass.
        await render(self.prompt.app)
        lines = screen_lines(await render(self.prompt.app))
        return next(line for line in lines if "NET" in line)

    async def test_date_hides_for_actual_dock_but_system_keeps_sampling_when_hidden(self):
        await self.start("10 20")
        await until(lambda: self.dock.system.timestamp is not None)
        date = self.dock.config.toolbar.date.render(self.dock.config.clock.now())
        self.assertIn(date, await self.toolbar_line())
        for mode in (DockMode.BASIC, DockMode.MAXIMIZED):
            self.dock.set_mode(mode)
            line = await self.toolbar_line()
            self.assertNotIn(date, line)
            self.assertTrue(line.startswith("Len:5"))
        calls = self.calls
        self.dock.show(False)
        self.assertIn(date, await self.toolbar_line())
        await until(lambda: self.calls > calls)
        self.assertEqual(self.prompt.default_buffer.text, "10 20")
        self.assertIs(self.prompt.style, self.original_style)

    async def test_space_shortage_reshows_date_and_resize_keeps_right_alignment(self):
        await self.start()
        self.dock.show()
        await self.toolbar_line()
        self.output.rows = 4
        line = await self.toolbar_line()
        self.assertEqual(self.dock.display_mode, DockMode.HIDDEN)
        self.assertIn(self.dock.config.toolbar.date.render(self.dock.config.clock.now()), line)
        self.output.columns = 100
        line = await self.toolbar_line()
        self.assertTrue(line.rstrip().endswith("/s") or line.rstrip().endswith("↑—"))
        self.assertEqual(width(line), 100)

    async def test_buffer_status_and_summary_change_without_changing_prompt_editing(self):
        await self.start("한글 10\n20")
        self.prompt.default_buffer.document = Document("한글 10\n20", cursor_position=8)
        self.prompt.default_buffer.selection_state = SelectionState(original_cursor_position=3)
        original = self.prompt.default_buffer.document
        line = await self.toolbar_line()
        self.assertIn(f"Len:{8:<5}  Lines:{2:<5}    Ln:{2:<5}  Col:{3:<5}  Pos:{9:<5}    Σ 30(15)", line)
        self.assertIn("Σ 30(15)", line)
        self.assertEqual(self.prompt.default_buffer.document, original)
        self.prompt.default_buffer.selection_state = None
        line = await self.toolbar_line()
        self.assertIn("Σ 30(15)", line)
        self.dock.configure(replace(self.dock.config, toolbar=replace(self.dock.config.toolbar, numbers={"scope": "selection"})))
        self.assertIn("Σ —", await self.toolbar_line())

    async def test_disable_uninstall_and_later_plugin_override_restore_only_owned_values(self):
        callback = self.prompt.bottom_toolbar
        self.assertTrue(callable(callback))
        self.dock.configure(replace(self.dock.config, toolbar={"enabled": False}))
        self.assertEqual(self.prompt.bottom_toolbar, "HOST TOOLBAR")
        self.assertIs(self.toolbar_window.content, self.original_control)
        self.assertIs(self.toolbar_window.wrap_lines, self.original_wrap)
        self.dock.configure(replace(self.dock.config, toolbar={"enabled": True}))
        await self.start()
        await until(lambda: self.dock.system.timestamp is not None)
        later = lambda: "LATER TOOLBAR"
        self.prompt.bottom_toolbar = later
        await render(self.prompt.app)
        await until(lambda: not self.dock._system._task.running)
        self.dock.configure(self.dock.config)
        self.assertIs(self.prompt.bottom_toolbar, later)
        uninstall(self.prompt)
        self.assertIs(self.prompt.bottom_toolbar, later)

    async def test_prompt_completion_stops_sampling_and_next_prompt_rebaselines(self):
        await self.start()
        await until(lambda: self.dock.system.timestamp is not None)
        self.prompt.app.exit(result="")
        await self.task
        await until(lambda: not self.dock._system._task.running)
        self.assertEqual(self.dock.system_history, ())
        await self.start("next")
        await until(lambda: self.dock.system.timestamp is not None)
        self.assertIsNone(self.dock.system.cpu_percent)

    async def test_narrow_animated_toolbar_does_not_accumulate_short_lines_in_width_cache(self):
        from prompt_toolkit.utils import _CHAR_SIZES_CACHE
        from dok.features.system.models import SystemSnapshot
        self.output.columns = 40
        self.dock.configure(replace(self.dock.config, system={"enabled": False}, toolbar={
            "date": {"enabled": False}, "input": {"enabled": False}, "numbers": {"enabled": False},
            "system": {"order": ["cpu"]}}))
        await self.start()
        before = set(_CHAR_SIZES_CACHE)
        for value in range(40):
            self.dock._system._snapshot = SystemSnapshot(cpu_percent=value)
            await render(self.prompt.app)
        new_lines = [key for key in set(_CHAR_SIZES_CACHE) - before if len(key) > 1 and "%" in key]
        self.assertEqual(new_lines, [])


class PromptStatusTests(unittest.TestCase):
    def test_block_selection_uses_host_ranges_and_does_not_join_adjacent_numbers(self):
        with create_pipe_input() as pipe:
            prompt = HostPrompt(pipe, validate_while_typing=False)
            prompt.app.editing_mode = EditingMode.VI
            prompt.default_buffer.document = Document("10 999\n20 888", 8)
            prompt.default_buffer.selection_state = SelectionState(0, SelectionType.BLOCK)
            status = PromptStatus(prompt)
            with set_app(prompt.app):
                snapshot = status.read()
                self.assertEqual(snapshot.selection, ((0, 2), (7, 9)))
                self.assertIs(status.read(), snapshot)
            result = NumberCalculator().calculate(snapshot, NumberScope.SELECTION)
            self.assertEqual((result.total, result.average), (30, 15))
