"""Global restart, header actions and composer-only stop/resume shortcuts."""

import asyncio
import unittest
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from examples.hub.preview import create_preview
from hub.widget.controls import TextArea
from hub.widget.header import HeaderActions
from tests.hub.support import SizedOutput, eventually


class HeaderActionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_everywhere_stop_only_composer_and_icon_buttons(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            restarted, stopped = [], []
            view.on_restart = lambda: restarted.append(True)
            view.on_interrupt = stopped.append
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running and view.composer.window.render_info)
                view.composer.text = "preserved draft"
                for mode in ("composer", "sidebar", "settings", "popup", "tags"):
                    with set_app(app):
                        if view._dialog:
                            view.close_dialog()
                        if view.settings_open:
                            view.toggle_settings()
                        view.output.deactivate()
                        view._focus_composer()
                        if mode == "sidebar":
                            view.focus_sidebar()
                        elif mode == "settings":
                            view.toggle_settings()
                        elif mode == "popup":
                            field = TextArea(text="popup draft")
                            view.open_dialog("Popup", field, focus=field)
                        elif mode == "tags":
                            view.output.tags.open()
                    count = len(restarted)
                    pipe.send_text("\x17")
                    await eventually(lambda: len(restarted) == count + 1)
                    count = len(stopped)
                    pipe.send_text("\x04")
                    await asyncio.sleep(.05)
                    self.assertEqual(len(stopped), count + (mode == "composer"))
                self.assertEqual(view.composer.text, "preserved draft")
                with set_app(app):
                    view.output.deactivate()
                for settings in (False, True):
                    with set_app(app):
                        if settings:
                            view.toggle_settings()
                    await asyncio.sleep(.05)
                    header = next(node for node in app.layout.walk() if isinstance(node, HeaderActions))
                    self.assertEqual((header.restart.text, header.minimize.text), ("", ""))
                    count = len(restarted)
                    rect = app.renderer._last_screen.visible_windows_to_write_positions[header.restart.window]
                    pipe.send_text(f"\x1b[<0;{rect.xpos+2};{rect.ypos+1}M\x1b[<0;{rect.xpos+2};{rect.ypos+1}m")
                    await eventually(lambda: len(restarted) == count + 1)
                self.assertTrue(view.profile_bar.presets.caption().endswith(""))
                with set_app(app):
                    header.minimize.handler()
                self.assertFalse(view.visible)
                count = len(restarted)
                pipe.send_text("\x17")
                await asyncio.sleep(.05)
                self.assertEqual(len(restarted), count)
            finally:
                app.exit()
                await task
                view.close()
                view.progress.close()
