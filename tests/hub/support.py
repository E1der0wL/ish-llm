"""Shared PTK test output and asynchronous interaction helpers."""

import asyncio

from prompt_toolkit.data_structures import Size
from prompt_toolkit.output import DummyOutput


class SizedOutput(DummyOutput):
    def __init__(self, columns=132, rows=40):
        self.columns = columns
        self.rows = rows

    def get_size(self):
        return Size(rows=self.rows, columns=self.columns)


async def eventually(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.01)


async def minimize(pipe, view):
    if view.tags.visible:
        pipe.send_text("\x14")
        await eventually(lambda: not view.tags.visible)
    if view._dialog is not None:
        pipe.send_text("\x1b")
        await eventually(lambda: view._dialog is None)
    if not view.active_page.sidebar_focused:
        pipe.send_text("\x1b")
        await eventually(lambda: view.active_page.sidebar_focused)
    pipe.send_text("\x1b")
    await eventually(lambda: not view.visible)


