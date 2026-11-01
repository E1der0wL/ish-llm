"""Use the reference host's real layout, keys, Float and input dispatch methods."""

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

from prompt_toolkit import PromptSession
from prompt_toolkit.data_structures import Size
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.output import DummyOutput

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ish.platform/src"))
from ish.ui.prompt import Prompt


class Output(DummyOutput):
    def __init__(self, columns=120, rows=24):
        self.columns = columns
        self.rows = rows

    def get_size(self):
        return Size(self.rows, self.columns)

    def get_rows_below_cursor_position(self):
        return self.rows


class HostPrompt(Prompt):
    def __init__(self, input, output=None, **kwargs):
        self.floats = []
        self.internal_tools = {}
        self.internal_commands = {}
        self.exit_command = "ish_exit"
        self.input_observer = SimpleNamespace(record=lambda *args: None)
        self.transport_check = lambda text: None
        self.layout_rebuilds = 0
        self.interactive_shell = SimpleNamespace(master_fd=None)
        self.encoder = "utf-8"
        self.prompt_text = None
        self._primary_prompt = True
        PromptSession.__init__(self, input=input, output=output or Output(),
                               history=InMemoryHistory(), multiline=True, bottom_toolbar="HOST TOOLBAR",
                               **kwargs)
        self.key_bindings = self._create_key_binding()

    def _validate_editor_submission(self, text):
        self.transport_check(text)

    def _update_layout(self):
        self.layout_rebuilds += 1
        super()._update_layout()

    def _tool_argv(self, command):
        return None


async def wait_running(app):
    async with asyncio.timeout(3):
        while not app.is_running:
            await asyncio.sleep(0)


async def render(app):
    future = asyncio.get_running_loop().create_future()

    def done(sender):
        if not future.done():
            future.set_result(sender.renderer._last_screen)

    app.after_render += done
    try:
        app.invalidate()
        return await asyncio.wait_for(future, timeout=3)
    finally:
        app.after_render -= done


def screen_lines(screen):
    columns = max((max(row, default=-1) + 1 for row in screen.data_buffer.values()), default=0)
    return ["".join(screen.data_buffer[y][x].char for x in range(columns))
            for y in range(screen.height)]
