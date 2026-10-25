"""Standalone live terminal UI; ish installations reuse the host Application."""

from hub.locales.keys import MessageKey
from .input.keys import KeyName
from .installation import InstallationSurface

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.layout import (
    ConditionalContainer,
    Float,
    FloatContainer,
    HSplit,
    Layout,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import DynamicStyle

from .live import LiveController, LiveHubView
from ..config.preferences import configured_profile


ABSENT = None


def create_application(
    config, *, theme=ABSENT, input=ABSENT, output=ABSENT, worker_factory=ABSENT
):
    config = configured_profile(config)
    view = LiveHubView(theme, config.language, config.language_packs)
    controller = (
        LiveController(view, config, worker_factory=worker_factory)
        if worker_factory
        else LiveController(view, config)
    )
    background = Window(
        FormattedTextControl(
            view.t(MessageKey.BACKGROUND),
            focusable=InstallationSurface.FOCUSABLE_BACKGROUND,
        )
    )
    root = FloatContainer(
        HSplit([background]),
        floats=[
            Float(
                view.container,
                top=InstallationSurface.EDGE,
                bottom=InstallationSurface.EDGE,
                left=InstallationSurface.EDGE,
                right=InstallationSurface.EDGE,
            ),
            Float(
                ConditionalContainer(
                    view.toasts.container,
                    Condition(lambda: not view.visible and view.toasts.active()),
                ),
                bottom=InstallationSurface.TOAST_MARGIN,
                right=InstallationSurface.TOAST_MARGIN,
                width=view.toasts.width,
                height=view.toasts.height,
            ),
        ],
    )
    keys = KeyBindings()

    @keys.add(KeyName.CONTROL_Q, filter=Condition(lambda: not view.visible))
    def toggle(event):
        view.toggle(event)

    @keys.add(KeyName.CONTROL_C, filter=Condition(lambda: not view.visible))
    @keys.add(KeyName.CONTROL_D, filter=Condition(lambda: not view.visible))
    def close(event):
        event.app.exit()

    app = Application(
        layout=Layout(root, focused_element=background),
        key_bindings=merge_key_bindings([keys, view.navigation_keys]),
        style=DynamicStyle(lambda: view.style),
        full_screen=InstallationSurface.FULL_SCREEN,
        mouse_support=Condition(lambda: view.visible),
        input=input,
        output=output,
    )
    from .restart import RESTART, bind_restart

    async def restart():
        app.exit(result=RESTART)

    bind_restart(controller, restart)
    return app, controller
