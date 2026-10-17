"""Daily statistics, system diagnostics and versioned definition editing."""

import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.backend.runtime import HubConfig, HubRuntime
from hub.backend.project.usage import daily_usage
from hub.config.theme import HubTheme
from hub.locales import Language
from hub.model import ChatMessage, HubSnapshot
from hub.ui.presentation import present, MessagePresentationCache
from hub.widget.forms.definition_manager import DefinitionManager, DefinitionEditor, initial_definition
from hub.widget.usage_chart import DailyUsageChart
from hub.widget.workflow_graph import workflow_diagram
from hub.widget.transcript.conversation import render_messages
from tests.hub.support import SizedOutput, eventually


class UsageAndSystemTests(unittest.TestCase):
    def test_daily_counts_unknown_calls_and_exact_height(self):
        calls = [{"started_at": "2026-10-01T12:00:00+00:00", "usage": {"total_tokens": 12}, "usage_complete": True},
                 {"started_at": "2026-10-01T12:01:00+00:00", "usage": {"total_tokens": 4}, "usage_complete": True},
                 {"started_at": "2026-10-01T12:02:00+00:00", "usage_complete": False},
                 {"started_at": "2026-10-03T12:00:00+00:00", "usage": {"total_tokens": 30}, "usage_complete": True}]
        days = daily_usage(calls)
        self.assertEqual(days[0], {"date": "2026-10-01", "tokens": 16, "unknown": 1})
        chart = DailyUsageChart([{"daily": days}, {"error": "unavailable"}], Language())
        self.assertEqual(chart.preferred_height(80, 100).preferred, 40)
        for width in (10, 40, 100):
            self.assertEqual(len(chart.content.rows(width)), 40)
        rows = chart.content.rows(100)
        self.assertIn("█", "".join(text for row in rows for _, text in row))
        self.assertFalse(chart.content.is_focusable())

    def test_failure_has_separate_system_header_and_preserves_model_output(self):
        message = ChatMessage("assistant", "Partial model output", time="2026-10-18T05:06:07+00:00",
                              status="failed", id="a", run_id="r")
        snapshot = HubSnapshot("p", "P", "model", "file", (), "s", (message,))
        cache = MessagePresentationCache()
        display = present(snapshot, Language(), cache)
        self.assertEqual([m.role for m in display.messages], ["assistant", "system"])
        self.assertEqual(display.messages[0].text, message.text)
        system = display.messages[1]
        self.assertEqual(system.id, "a:failure")
        header = render_messages((system,), 100, HubTheme(), Language(), section="header")
        text = "".join(text for row in header for _, text in row)
        self.assertIn("시스템 • 2026-10-18", text)
        self.assertNotIn("실패", text)
        self.assertTrue(any("hub.system" in style for row in header for style, _ in row))
        t = Language()
        display = present(snapshot, t, cache)
        with patch("hub.ui.presentation.split_followup", side_effect=AssertionError("Unchanged body was reparsed")):
            self.assertEqual(present(snapshot, t, cache).messages, display.messages)
        self.assertEqual(snapshot.messages, (message,))

    def test_workflow_diagram_branches_cycles_and_invalid_draft(self):
        flow = {"entry": "a", "nodes": {"a": {"type": "branch"}, "b": {"type": "end"}},
                "edges": [{"source": "a", "target": "b", "port": "yes"}, {"source": "b", "target": "a"}]}
        diagram = workflow_diagram(json.dumps(flow))
        for value in ("┌", "│", "▶", "a ─yes→ b", "b ─→ a"):
            self.assertIn(value, diagram)
        rows = diagram.splitlines()
        self.assertEqual(rows[1][7], "▶")
        self.assertEqual(rows[2][7], "◀")
        with self.assertRaises(ValueError):
            workflow_diagram('{"nodes": []}')
        flow["edges"][0]["target"] = "missing"
        with self.assertRaisesRegex(ValueError, "Unknown edge"):
            workflow_diagram(json.dumps(flow))


class DefinitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_backend_create_edit_delete_validation_and_conflict(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False))
            await runtime.start()
            try:
                pid = runtime.project.id
                for component in ("prompts", "agents", "workflows"):
                    async def manage(action, argument="", version=None):
                        return await runtime.manage_records(pid, component, action, argument, version)
                    value = initial_definition(component)
                    if component == "agents":
                        value["purpose"] = "Review changes"
                    payload = {"name": "draft", "text": json.dumps(value)}
                    await manage("create", payload)
                    with self.assertRaises(FileExistsError):
                        await manage("create", payload)
                    original = await manage("read", "draft")
                    value["metadata"] = {"description": "Updated"}
                    await manage("save", {**payload, "text": json.dumps(value)}, original["version"])
                    with self.assertRaises(ValueError):
                        await manage("delete", "draft", original["version"])
                    current = await manage("read", "draft")
                    with self.assertRaises(ValueError):
                        await manage("save", {**payload, "text": "{}"}, current["version"])
                    self.assertEqual(await manage("read", "draft"), current)
                    await manage("delete", "draft", current["version"])
                    self.assertNotIn("draft", [r["name"] for r in await manage("list")])
            finally:
                await runtime.close()

    async def test_inline_workflow_preview_draft_retention_and_save(self):
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            calls, succeed = [], False
            def request(action, argument, version, done):
                calls.append((action, argument, version))
                done([{"name": argument["name"], "version": "v", "description": ""}] if succeed else None)
            manager = DefinitionManager(view, request, name="workflows", edit_text=None)
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                with set_app(app):
                    view.dialogs.show(manager, manager.path)
                    manager.create_item()
                editor = view._dialog
                self.assertIsInstance(editor, DefinitionEditor)
                self.assertIn("done", editor.preview.text)
                with set_app(app):
                    editor.identifier.text = "flow"
                    editor.input.text = "{"
                await eventually(lambda: view.t("workflow_invalid") in editor.preview.text)
                with set_app(app):
                    editor.input.text = json.dumps(initial_definition("workflows")).replace("done", "finish")
                await eventually(lambda: "finish" in editor.preview.text)
                with set_app(app):
                    editor.save()
                self.assertIs(view._dialog, editor)
                self.assertIn("finish", editor.input.text)
                self.assertFalse(editor.busy)
                succeed = True
                with set_app(app):
                    editor.save()
                self.assertIs(view._dialog, manager)
                self.assertEqual(manager.selected, "flow")
                self.assertEqual(calls[-1][0], "create")
            finally:
                app.exit()
                await task
                view.close()
                view.progress.close()
