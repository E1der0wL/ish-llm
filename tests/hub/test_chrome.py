"""Check painted cells and frame bounds in the actual host renderer."""

import asyncio
import unittest

from prompt_toolkit.input import create_pipe_input

from hub.hub import install
from hub.config.theme import HubTheme
from tests.hub.test_ish_integration import Prompt
from tests.hub.test_mockup import SizedOutput, eventually


class ChromeTests(unittest.IsolatedAsyncioTestCase):
    async def test_composer_growth_and_session_sidebar_resize(self):
        with create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            theme = HubTheme(accent2="#123456")
            installation = install(prompt, theme=theme, preview=True)
            app, view = prompt.app, installation.view
            task = asyncio.create_task(prompt.prompt_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running and app.renderer._last_screen is not None)
                def height():
                    return app.renderer._last_screen.visible_windows_to_write_positions[view.composer.window].height
                for lines, expected in ((1, 1), (4, 4), (12, 8), (2, 2)):
                    view.composer.text = "\n".join("line" for _ in range(lines))
                    await eventually(lambda: height() == expected)
                pipe.send_text("\x1b")
                await eventually(lambda: app.layout.current_control == view._session_control)
                pipe.send_text("\x1b[1;5C")
                await eventually(lambda: view.theme.sidebar_width == theme.sidebar_width + 1)
                self.assertIs(app.layout.current_control, view._session_control)
                appearance = view.settings.pages["appearance"]
                self.assertTrue(appearance.dirty)
                self.assertEqual(appearance.form.values()["accent2"], "#123456")
                pipe.send_text("\x1b[1;5D")
                await eventually(lambda: view.theme.sidebar_width == theme.sidebar_width)
                self.assertEqual(view.composer.text, "line\nline")
                view.toggle_settings()
                app.layout.focus(view.settings.project_list)
                self.assertTrue(view.settings.project_focused)
            finally:
                app.exit(result="")
                await task
                installation.close()

    async def test_bars_and_frames_survive_resize_and_theme_changes(self):
        with create_pipe_input() as pipe:
            output = SizedOutput()
            prompt = Prompt(input=pipe, output=output)
            installation = install(prompt, preview=True)
            app, view = prompt.app, installation.view
            view.composer.text = "**Draft**\n\n---\n\npreview"
            view.show_preview = True
            rendered = asyncio.Event()
            app.after_render += lambda _: rendered.set()
            task = asyncio.create_task(prompt.prompt_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                for columns, rows, theme in ((132, 40, HubTheme()), (80, 24, HubTheme(
                        background="#123456", accent1="#234567")),
                        (60, 14, HubTheme())):
                    output.columns, output.rows = columns, rows
                    view.set_theme(theme)
                    rendered.clear()
                    app._on_resize()
                    await asyncio.wait_for(rendered.wait(), 3)
                    screen = app.renderer._last_screen
                    for y, expected in ((0, theme.header_background), (rows - 1, theme.footer_background)):
                        for x in range(columns):
                            attrs = app._merged_style.get_attrs_for_style_str(screen.data_buffer[y][x].style)
                            self.assertEqual(attrs.bgcolor, expected[1:], (columns, rows, x, y))
                    positions = screen.visible_windows_to_write_positions
                    windows = [view.composer.window]
                    if view._preview_container.filter():
                        windows.append(view.draft_preview.window)
                    for window in windows:
                        position = positions[window]
                        self.assertEqual(screen.data_buffer[position.ypos - 1][position.xpos - 1].char, "┌")
                        self.assertEqual(screen.data_buffer[position.ypos][position.xpos - 1].char, "│")
                        self.assertEqual(screen.data_buffer[position.ypos][position.xpos + position.width].char, "│")
                    self.assertEqual(view.composer.text, "**Draft**\n\n---\n\npreview")
            finally:
                app.exit(result="")
                await task
                installation.close()
