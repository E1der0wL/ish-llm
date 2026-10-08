"""Behavioral coverage for navigation, filtered drafts, output objects and Tool CRUD."""

import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.thinking import ThinkingPhases
from hub.config.theme import HubTheme
from hub.model import ChatMessage
from hub.widget.conversation import ConversationControl
from hub.widget.tool_manager import ToolManager
from hub.ui.application import create_application
from tests.hub.test_mockup import SizedOutput, eventually
from tests.hub.test_live import until
from tests.hub.test_settings import settings_backend


class ObjectTests(unittest.TestCase):
    def test_forms_keep_raw_and_reflow_anchors(self):
        source = ('Introduction\n\n```python\nprint("hello")\n```\n\n'
                  '| Name | Value |\n| --- | --- |\n| a | 2 |\n\n'
                  '<hub-diff title="changes">-old\n+new</hub-diff>\n\n'
                  '<hub-chart>{"values":{"a":2,"b":4}}</hub-chart>\n')
        control = ConversationControl((ChatMessage("assistant", source, id="message"),), HubTheme())
        control.create_content(80, 20)
        self.assertEqual([o.block.kind for o in control.objects], ["code", "table", "hub-diff", "hub-chart"])
        before = {o.id: o.raw for o in control.objects}
        for obj in control.objects:
            self.assertEqual(source[obj.block.start:obj.block.start + len(obj.raw)], obj.raw)
            self.assertEqual(dict(control._anchors)[obj.id], obj.start_line)
            self.assertEqual(len(obj.lines), obj.end_line - obj.start_line)
        control.create_content(32, 20)
        self.assertEqual({o.id: o.raw for o in control.objects}, before)
        self.assertNotIn("출력을 표시할 수 없습니다", str(control._lines))

    def test_reasoning_text_phases_repeat_and_completion_does_not_revive_them(self):
        phases, run = ThinkingPhases(), SimpleNamespace(id="run", status="running")
        completion = SimpleNamespace(id="call", reasoning_content="think one")
        event = SimpleNamespace(completion=completion, delta=None, output=None)
        phases.observe(run, event)
        visible = lambda: phases.visible("run", "", active=True, content="prior answer")
        self.assertEqual(visible(), "think one")
        text = SimpleNamespace(completion=None, delta=SimpleNamespace(text="answer", visibility="user"), output=None)
        phases.observe(run, text)
        self.assertEqual(visible(), "")
        phases.observe(run, event)
        self.assertEqual(visible(), "")
        completion.reasoning_content += "think two"
        phases.observe(run, event)
        self.assertEqual(visible(), "think two")
        phases.observe(run, text)
        self.assertEqual(visible(), "")


class InteractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_engine_settings_filter_and_component_command_popup(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(directory, auto_title=False),
                                                  input=pipe, output=SizedOutput())
            view, screen = controller.view, controller.view.settings
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: view.connected)
                for engine in ("loop", "graph"):
                    view.engine = engine
                    pipe.send_text("\x13")
                    try:
                        await until(lambda: screen.page is not None and not screen.busy and screen.page.query.endswith(engine))
                    except TimeoutError:
                        self.fail(f"engine={engine}, selected={view.engine}, opened={view.settings_open}, "
                                  f"query={getattr(screen.page, 'query', None)}, busy={screen.busy}, status={screen.status}")
                    self.assertEqual(screen.page.query, "config.parameters.engines." + engine)
                    expected = list(screen.page.engine_forms[engine].fields.values())
                    self.assertEqual(screen.page.fields(), expected)
                    self.assertIs(app.layout.current_control, expected[0].input.control)
                    pipe.send_text("\x06\x01\x0bno-such-setting\r")
                    await eventually(lambda: screen.page.query == "no-such-setting")
                    self.assertIs(app.layout.current_control, screen.page.save.control)
                    pipe.send_text("\x03")
                    await eventually(lambda: not view.settings_open)
                view.composer.text = "/tools config"
                view.composer.buffer.cancel_completion()
                pipe.send_text("\r")
                await eventually(lambda: isinstance(view._dialog, ToolManager) and not view._dialog.busy)
                self.assertEqual(view.composer.text, "")
                self.assertIs(app.layout.current_control, view._dialog.path.control)
                pipe.send_text("\x1b")
                await eventually(lambda: view._dialog is None)
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    async def test_keys_search_filtered_drafts_and_tag_focus(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            task = asyncio.create_task(app.run_async())
            try:
                pipe.send_text("\x11")
                await eventually(lambda: view.visible)
                pipe.send_text("\x12")
                await eventually(lambda: view.show_preview)
                for key in range(1, 13):
                    self.assertFalse(view.navigation_keys.get_bindings_for_keys((f"f{key}",)))
                pipe.send_text("\x1b")
                await eventually(lambda: view.active_page.sidebar_focused)
                width = view.theme.sidebar_width
                pipe.send_text("\x1b[1;5C")
                await eventually(lambda: view.theme.sidebar_width == width + 1)
                pipe.send_text("\x1b[C")
                await eventually(lambda: app.layout.current_control == view.composer.control)
                self.assertEqual(view.theme.sidebar_width, width + 1)
                view.transcript.set_messages((ChatMessage("assistant", "find me\n\n```py\nprint(1)\n```", id="m"),))
                app.invalidate()
                await eventually(lambda: bool(view.transcript.control.objects))
                pipe.send_text("\x06find")
                await eventually(lambda: view.transcript.control.search_query == "find")
                pipe.send_text("\x1b")
                await eventually(lambda: view._dialog is None)
                self.assertEqual(view.transcript.control.search_query, "")
                for expand in ("\t", " ", "\r"):
                    pipe.send_text("\x14")
                    await eventually(lambda: app.layout.current_control == view.tags.control)
                    with patch("hub.widget.tag_bar.copy_text", new_callable=AsyncMock, return_value="clipboard_copied") as copied:
                        pipe.send_text("c")
                        await eventually(lambda: copied.await_count == 1)
                        self.assertEqual(copied.call_args.args[0], "```py\nprint(1)\n```")
                    pipe.send_text(expand)
                    await eventually(lambda: view._dialog is not None)
                    self.assertTrue(view.tags.visible)
                    popup = view._dialog
                    await eventually(lambda: popup.output._render_key is not None)
                    self.assertTrue(any("print" in line for line in popup.output.lines))
                    self.assertNotIn("```", "\n".join(popup.output.lines))
                    pipe.send_text("\x1b")
                    await eventually(lambda: view._dialog is None)
                    self.assertIs(app.layout.current_control, view.tags.control)
                    pipe.send_text("\x14")
                    await eventually(lambda: not view.tags.visible)
                    self.assertIs(app.layout.current_control, view.composer.control)
                pipe.send_text("\x13")
                await eventually(lambda: view.settings.page is not None)
                screen = view.settings
                with set_app(app):
                    screen.choose("profile")
                    screen.layout.focus_main()
                field = screen.page.form.fields[("display_name",)]
                field.input.text = "original"
                field.input.buffer.cursor_position = 0
                pipe.send_text("\r")
                await eventually(lambda: field.editing)
                self.assertEqual(field.input.buffer.cursor_position, len("original"))
                pipe.send_text("d\r")
                await eventually(lambda: not field.editing)
                self.assertEqual(field.input.text, "originald")
                pipe.send_text("d")
                await eventually(lambda: field.input.text == "")
                hidden = screen.page.form.fields[("phone",)]
                hidden.input.text = "12345"
                with set_app(app):
                    screen.filter("12345")
                hidden.input.text = "67890"
                self.assertEqual(screen.page.fields(), [hidden])
                self.assertEqual(screen.page.container.fields, [hidden])
                with set_app(app):
                    screen.filter("")
                pipe.send_text("\x06email\r")
                await eventually(lambda: screen.page.query == "email" and view._dialog is None)
                self.assertEqual(screen.page.fields(), [screen.page.form.fields[("email",)]])
                self.assertEqual(screen.page.form.values()["phone"], "67890")
                pipe.send_text("\x03")
                await eventually(lambda: not view.settings_open)
                self.assertFalse(screen.pages)
            finally:
                app.exit()
                await task

    async def test_tool_manager_import_list_open_delete_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "demo"
            package.mkdir()
            (package / "demo.py").write_text('async def main():\n    """Demo description."""\n    return 1\n')
            runtime = HubRuntime(HubConfig(root / "workspace", auto_title=False), settings_backend)
            await runtime.start()
            try:
                entries = await runtime.manage_tools(runtime.project.id, "import", str(package))
                self.assertEqual(entries[0]["description"], "Demo description.")
                managed = Path(await runtime.manage_tools(runtime.project.id, "open", "demo"))
                self.assertNotEqual(managed, package)
                self.assertEqual((managed / "demo.py").read_text(), (package / "demo.py").read_text())
                with self.assertRaises(FileExistsError):
                    await runtime.manage_tools(runtime.project.id, "import", str(package))
                tools = await runtime.project.components.aget("tools")
                await tools.aenable("demo")
                with self.assertRaisesRegex(ValueError, "changed"):
                    await runtime.manage_tools(runtime.project.id, "delete", "demo", "outdated")
                self.assertEqual(await tools.aenabled(), ["demo"])
                self.assertEqual(await runtime.manage_tools(runtime.project.id, "delete", "demo", entries[0]["version"]), [])
                self.assertTrue(package.exists())
                self.assertFalse(managed.exists())
                (package / "unexpected.txt").write_text("not supported")
                with self.assertRaises(ValueError):
                    await runtime.manage_tools(runtime.project.id, "import", str(package))
            finally:
                await runtime.close()

    async def test_tool_popup_delete_cancel_restores_manager(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            task = asyncio.create_task(app.run_async())
            try:
                pipe.send_text("\x11")
                await eventually(lambda: view.visible)
                entry = {"name":"demo", "modified":"2026-10-08T00:00:00+00:00", "description":"Demo", "version":"v1"}
                with set_app(app):
                    popup = ToolManager(view, lambda action, argument, version, done: done([entry]))
                    view.dialogs.show(popup, popup.path)
                    popup.reload()
                pipe.send_text("\t")
                await eventually(lambda: app.layout.current_control == popup.list)
                pipe.send_text("d")
                await eventually(lambda: view._dialog is not popup)
                pipe.send_text("\x1b")
                await eventually(lambda: view._dialog is popup)
                self.assertIs(app.layout.current_control, popup.list)
                pipe.send_text("\x1b")
                await eventually(lambda: view._dialog is None)
                self.assertIs(app.layout.current_control, view.composer.control)
            finally:
                app.exit()
                await task
