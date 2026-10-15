"""Shared PTK test output and asynchronous interaction helpers."""

import asyncio

from prompt_toolkit.application.current import set_app
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


def settings_button(app, pipe, view, name="save"):
    """Invoke a settings action through its focused button and real Enter key."""
    with set_app(app):
        view.settings.finish_edit()
        app.layout.focus(getattr(view.settings.page, name))
    pipe.send_text("\r")


async def open_home(app, view):
    """Saving/cancelling stays in settings; Home is a separate explicit action."""
    await asyncio.sleep(0.05)  # Let a queued Enter invoke its button first.
    await eventually(lambda: not view.settings.busy and not view.projects.busy)
    with set_app(app):
        view.profile_bar.home.handler()
    await eventually(lambda: not view.settings_open)


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
    pipe.send_text("\x11")
    await eventually(lambda: not view.visible)


