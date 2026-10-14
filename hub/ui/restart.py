"""Async restart coordination shared by the shell and standalone application."""

import asyncio

from prompt_toolkit.application.current import get_app
from prompt_toolkit.widgets import Label


RESTART = "hub-restart"


def bind_restart(controller, rebuild):
    screen = controller.view.settings

    async def restart():
        token = controller.view.progress.start(screen.t("settings_restarting"))
        try:
            if controller.worker is not None:
                await asyncio.wrap_future(controller.worker.prepare_restart())
            await rebuild()
        except Exception as error:
            if controller.worker is not None and not controller.closed:
                controller.worker.cancel_restart()
            screen.error(error)
            controller.view.open_dialog(screen.t("settings_restart_title"),
                Label(str(error)))
        finally:
            controller.view.progress.finish(token)
            screen.busy = False
            get_app().invalidate()

    def request():
        if screen.busy or controller.closed:
            return
        screen.busy = True
        get_app().create_background_task(restart())

    screen.drafts.on_restart = request
