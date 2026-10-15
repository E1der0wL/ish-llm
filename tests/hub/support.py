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
        view.profile_bar.page.handler()
    await eventually(lambda: not view.settings_open)


async def minimize(pipe, view):
    if view.output.tags.visible:
        pipe.send_text("\x1b")
        await eventually(lambda: not view.output.tags.visible)
    if view._dialog is not None:
        pipe.send_text("\x1b")
        await eventually(lambda: view._dialog is None)
    if not view.active_page.sidebar_focused:
        pipe.send_text("\x1b")
        await eventually(lambda: view.active_page.sidebar_focused)
    pipe.send_text("\x11")
    await eventually(lambda: not view.visible)


async def auxiliary(app, pipe, view, name):
    """Exercise the auxiliary row via its public keyboard contract."""
    bar = view.settings.auxiliary if view.settings_open else view.auxiliary
    with set_app(app):
        if view.settings_open:
            view.settings.navigation.focus_zone(1)
        else:
            view._focus_composer()
    pipe.send_text("\x13")
    await eventually(lambda: app.layout.current_control is next(iter(bar.buttons.values())).control)
    for _ in range(list(bar.buttons).index(name)):
        pipe.send_text("\x1b[C")
    await eventually(lambda: app.layout.current_control is bar.buttons[name].control)
    pipe.send_text("\r")


async def open_settings(app, pipe, view):
    if not view.visible:
        pipe.send_text("\x11")
        await eventually(lambda: view.visible)
    if not view.settings_open:
        await auxiliary(app, pipe, view, "settings")
    await eventually(lambda: view.settings_open and view.settings.page is not None and not view.settings.busy)


