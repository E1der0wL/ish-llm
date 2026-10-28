"""Async restart coordination shared by the shell and standalone application."""

from hub.locales.keys import MessageKey

import asyncio

from prompt_toolkit.application.current import get_app
from prompt_toolkit.widgets import Label
from ..widget.dialog import PopupSize


class RestartContract:
    RESULT = "hub-restart"
    IDLE = False
    PENDING = True
    ABSENT_WORKER = None
    WAIT_NOTICE_SECONDS = 10


RESTART = RestartContract.RESULT


def confirm_restart(view, restart, *, message=MessageKey.RESTART_CONFIRM):
    """One confirmation surface; cleanup starts only after explicit acceptance."""
    view.open_dialog(
        view.t(MessageKey.SETTINGS_RESTART_TITLE),
        Label(view.t(message)),
        restart,
        accept_text=view.t(MessageKey.SETTINGS_RESTART_NOW),
        size=PopupSize.SMALL,
    )


def bind_restart(controller, rebuild):
    screen = controller.view.settings
    restarting = RestartContract.IDLE

    async def restart():
        nonlocal restarting
        token = controller.view.progress.start(screen.t(MessageKey.SETTINGS_RESTARTING))
        try:
            if controller.worker is not RestartContract.ABSENT_WORKER:
                cleanup = asyncio.wrap_future(controller.worker.prepare_restart(
                    stage=lambda update: controller._dispatch(controller.view.progress.observe, token, update)))
                try:
                    await asyncio.wait_for(
                        asyncio.shield(cleanup),
                        timeout=RestartContract.WAIT_NOTICE_SECONDS,
                    )
                except TimeoutError:
                    screen.status = controller.view.notice = screen.t(
                        MessageKey.SETTINGS_RESTART_WAITING
                    )
                    get_app().invalidate()
                    await cleanup
            await rebuild()
        except Exception as error:
            if (
                controller.worker is not RestartContract.ABSENT_WORKER
                and not controller.closed
            ):
                controller.worker.cancel_restart()
            screen.error(error)
            controller.view.open_dialog(
                screen.t(MessageKey.SETTINGS_RESTART_TITLE), Label(screen.status)
            )
        finally:
            controller.view.progress.finish(token)
            screen.busy = RestartContract.IDLE
            restarting = RestartContract.IDLE
            get_app().invalidate()

    def request():
        nonlocal restarting
        if restarting or controller.closed:
            return
        restarting = RestartContract.PENDING
        screen.busy = RestartContract.PENDING
        get_app().create_background_task(restart())

    def confirm():
        if not restarting and not controller.closed:
            confirm_restart(controller.view, request)

    controller.view.on_restart = confirm
    screen.drafts.on_restart = request
