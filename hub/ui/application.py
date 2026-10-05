"""Standalone live terminal UI; ish installations reuse the host Application."""

from prompt_toolkit.application import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.layout import ConditionalContainer, Float, FloatContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import DynamicStyle

from .live import LiveController, LiveHubView
from ..config.preferences import configured_profile


def create_application(config, *, theme=None, input=None, output=None, worker_factory=None):
    config = configured_profile(config)
    view = LiveHubView(theme, config.language, config.language_packs)
    options = {"worker_factory": worker_factory} if worker_factory else {}
    controller = LiveController(view, config, **options)
    background = Window(FormattedTextControl(view.t("background"), focusable=True))
    root = FloatContainer(HSplit([background]), floats=[Float(
        view.container, top=0, bottom=0, left=0, right=0), Float(
        ConditionalContainer(view.toasts.container, Condition(lambda: not view.visible and view.toasts.active())),
        bottom=1, right=1, width=view.toasts.width, height=view.toasts.height)])
    keys = KeyBindings()

    @keys.add("c-q", filter=Condition(lambda: not view.visible))
    def toggle(event):
        view.toggle(event)

    @keys.add("c-s", filter=Condition(lambda: not view.visible), eager=True)
    def settings(event):
        view.toggle_settings(event)

    @keys.add("c-c", filter=Condition(lambda: not view.visible))
    @keys.add("c-d", filter=Condition(lambda: not view.visible))
    def close(event):
        event.app.exit()

    app = Application(layout=Layout(root, focused_element=background),
                      key_bindings=merge_key_bindings([keys, view.navigation_keys]),
                      style=DynamicStyle(lambda: view.style), full_screen=True,
                      mouse_support=True, input=input, output=output)
    return app, controller
