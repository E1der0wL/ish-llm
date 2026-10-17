"""Async restart coordination shared by the shell and standalone application."""

import asyncio

from prompt_toolkit.application.current import get_app
from prompt_toolkit.widgets import Label


RESTART = "hub-restart"


def bind_restart(controller, rebuild):
    screen = controller.view.settings
    restarting = False

    async def restart():
        nonlocal restarting
        token = controller.view.progress.start(screen.t("settings_restarting"))
        try:
            if controller.worker is not None:
                cleanup = asyncio.wrap_future(controller.worker.prepare_restart())
                try:
                    await asyncio.wait_for(asyncio.shield(cleanup), timeout=10)
                except TimeoutError:
                    screen.status = controller.view.notice = screen.t("settings_restart_waiting")
                    get_app().invalidate()
                    await cleanup
            await rebuild()
        except Exception as error:
            if controller.worker is not None and not controller.closed:
                controller.worker.cancel_restart()
            screen.error(error)
            controller.view.open_dialog(screen.t("settings_restart_title"),
                Label(screen.status))
        finally:
            controller.view.progress.finish(token)
            screen.busy = False
            restarting = False
            get_app().invalidate()

    def request():
        nonlocal restarting
        if restarting or controller.closed:
            return
        restarting = True
        screen.busy = True
        get_app().create_background_task(restart())

    controller.view.on_restart = screen.drafts.on_restart = request
