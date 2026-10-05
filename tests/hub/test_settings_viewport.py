"""Large settings forms paint only the viewport and keep distant fields editable."""

import unittest
from unittest.mock import patch

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import DummyInput
from prompt_toolkit.layout import DynamicContainer, Layout
from prompt_toolkit.widgets import Frame

from hub.locales import Language
from hub.ui.settings.form import SchemaForm
from hub.ui.settings.viewport import SettingsBody, SettingsPane
from tests.hub.test_mockup import SizedOutput


class SettingsViewportTests(unittest.IsolatedAsyncioTestCase):
    async def test_visible_rows_focus_jump_edit_and_resize(self):
        form = SchemaForm({"type": "object", "properties": {
            f"field_{index}": {"type": "string", "description": "Long description " * 6}
            for index in range(150)}}, {}, Language())
        fields = list(form.fields.values())
        body = SettingsBody(form.children, fields)
        pane = SettingsPane(DynamicContainer(lambda: body))
        output = SizedOutput(columns=100, rows=32)
        app = Application(layout=Layout(Frame(pane), focused_element=fields[0].input),
                          input=DummyInput(), output=output, full_screen=True)
        def render():
            app.render_counter += 1
            app.renderer.render(app, app.layout)
        with set_app(app), patch.object(fields[70].input.control, "create_content",
                                       wraps=fields[70].input.control.create_content) as hidden:
            render()
            self.assertEqual(hidden.call_count, 0)
            self.assertLess(len(app.renderer._last_screen.visible_windows), len(fields))
            app.layout.focus(fields[-1].input)
            render()
            self.assertGreater(pane.vertical_scroll, 0)
            self.assertIn(fields[-1].input.window, app.renderer._last_screen.visible_windows)
            self.assertEqual(hidden.call_count, 0)
            fields[-1].begin()
            fields[-1].input.buffer.insert_text("edited value")
            render()
            self.assertEqual(fields[-1].value(), "edited value")
            old_width = fields[-1].input.window.render_info.window_width
            output.columns = 60
            render()
            self.assertLess(fields[-1].input.window.render_info.window_width, old_width)
            self.assertIn(fields[-1].input.window, app.renderer._last_screen.visible_windows)
            app.layout.focus(fields[0].input)
            render()
            self.assertLess(pane.vertical_scroll, body._offsets[1])
            self.assertIn(fields[0].input.window, app.renderer._last_screen.visible_windows)
