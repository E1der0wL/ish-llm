from tests.hub.test_mockup import minimize
"""Real ish/PTK rendering must never paint Hub over the shell's output."""

import asyncio
import unittest

from prompt_toolkit.application import in_terminal
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.utils import get_cwidth

from hub.hub import install
from tests.hub.test_ish_integration import Prompt
from tests.hub.test_mockup import SizedOutput, eventually


class ScreenOutput(SizedOutput):
    """Cell buffers driven by the output operations of the actual PTK renderer."""
    def __init__(self):
        super().__init__()
        self.main, self.alternate = {}, {}
        self.positions = {False: [0, 0], True: [0, 0]}
        self.in_alternate = False
        self.transitions = []
        self.autowrap = True
        self.write("previous shell output\nsecond command result\n")

    @property
    def cells(self):
        return self.alternate if self.in_alternate else self.main

    @property
    def position(self):
        return self.positions[self.in_alternate]

    def write(self, data):
        for char in data:
            if char == "\r":
                self.position[1] = 0
            elif char == "\n":
                self.position[0] += 1
                self.position[1] = 0
            else:
                if self.position[1] >= self.columns:
                    if self.autowrap:
                        self.position[0] += 1
                        self.position[1] = 0
                    else:
                        self.position[1] = self.columns - 1
                self.cells[tuple(self.position)] = char
                self.position[1] += get_cwidth(char)

    def cursor_up(self, amount):
        self.position[0] = max(0, self.position[0] - amount)

    def cursor_down(self, amount):
        self.position[0] += amount

    def cursor_backward(self, amount):
        self.position[1] = max(0, self.position[1] - amount)

    def cursor_forward(self, amount):
        self.position[1] += amount

    def cursor_goto(self, row=0, column=0):
        self.position[:] = [row, column]

    def erase_down(self):
        for key in list(self.cells):
            if key >= tuple(self.position):
                del self.cells[key]

    def erase_end_of_line(self):
        for key in list(self.cells):
            if key[0] == self.position[0] and key[1] >= self.position[1]:
                del self.cells[key]

    def erase_screen(self):
        self.cells.clear()

    def enter_alternate_screen(self):
        self.transitions.append("enter")
        self.in_alternate = True
        self.alternate.clear()
        self.position[:] = [0, 0]

    def quit_alternate_screen(self):
        self.transitions.append("leave")
        self.in_alternate = False

    def disable_autowrap(self):
        self.autowrap = False

    def enable_autowrap(self):
        self.autowrap = True

    def text(self, cells):
        return "\n".join("".join(cells.get((row, col), " ") for col in range(self.columns)).rstrip()
                         for row in range(self.rows))


class TerminalScreenTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_settings_and_terminal_output_preserve_shell_screen(self):
        with create_pipe_input() as pipe:
            output = ScreenOutput()
            history = dict(output.main)
            prompt = Prompt(input=pipe, output=output)
            installation = install(prompt, preview=True)
            app, view = prompt.app, installation.view
            painted = asyncio.Event()
            app.after_render += lambda _: painted.set()
            task = asyncio.create_task(prompt.prompt_async())
            async def paint():
                painted.clear()
                app.invalidate()
                await asyncio.wait_for(painted.wait(), 3)
            try:
                await eventually(lambda: app.is_running)
                pipe.send_text("unfinished shell command")
                await eventually(lambda: prompt.default_buffer.text == "unfinished shell command")
                await paint()
                for key in ("\x11", "\x11\x13", "\x11"):
                    pipe.send_text(key)
                    await eventually(lambda: view.visible and output.in_alternate)
                    await paint()
                    self.assertTrue(app.full_screen and app.renderer.full_screen)
                    self.assertEqual({key: output.main.get(key) for key in history}, history)
                    self.assertNotIn(view.project_title, output.text(output.main))
                    self.assertTrue(output.alternate)
                    if not view.settings_open:
                        view.composer.text = "Hub draft"
                        with set_app(app):
                            async with in_terminal():
                                self.assertFalse(output.in_alternate)
                                output.write("background shell output\n")
                        await paint()
                        self.assertTrue(output.in_alternate)
                    await minimize(pipe, view)
                    await eventually(lambda: not view.visible and not output.in_alternate)
                    await paint()
                    self.assertFalse(app.full_screen or app.renderer.full_screen)
                    self.assertEqual({key: output.main.get(key) for key in history}, history)
                    self.assertIn("unfinished shell command", output.text(output.main))
                    self.assertIn("background shell output", output.text(output.main))
                    self.assertEqual(prompt.default_buffer.text, "unfinished shell command")
                    self.assertEqual(view.composer.text, "Hub draft")
                self.assertEqual(output.transitions.count("enter"), output.transitions.count("leave"))
            finally:
                app.exit(result="")
                await task
                installation.close()

    async def test_close_and_host_exit_restore_screen_ownership(self):
        for exit_host in (False, True):
            with self.subTest(exit_host=exit_host), create_pipe_input() as pipe:
                output = ScreenOutput()
                history = dict(output.main)
                prompt = Prompt(input=pipe, output=output)
                installation = install(prompt, preview=True)
                app, view = prompt.app, installation.view
                task = asyncio.create_task(prompt.prompt_async())
                try:
                    await eventually(lambda: app.is_running)
                    pipe.send_text("\x11")
                    await eventually(lambda: output.in_alternate)
                    if exit_host:
                        app.exit(result="")
                        await task
                    else:
                        installation.close()
                    self.assertFalse(output.in_alternate)
                    self.assertFalse(app.full_screen or app.renderer.full_screen)
                    self.assertEqual({key: output.main.get(key) for key in history}, history)
                finally:
                    if not task.done():
                        app.exit(result="")
                    await task
                    installation.close()

    async def test_existing_fullscreen_host_retains_its_buffer(self):
        with create_pipe_input() as pipe:
            output = ScreenOutput()
            prompt = Prompt(input=pipe, output=output)
            app = prompt.app
            app.full_screen = app.renderer.full_screen = True
            installation = install(prompt, preview=True)
            task = asyncio.create_task(prompt.prompt_async())
            try:
                await eventually(lambda: output.in_alternate)
                pipe.send_text("\x11")
                await eventually(lambda: installation.view.visible)
                installation.close()
                self.assertTrue(app.full_screen and app.renderer.full_screen)
                self.assertTrue(output.in_alternate)
                self.assertEqual(output.transitions, ["enter"])
            finally:
                app.exit(result="")
                await task
                installation.close()
