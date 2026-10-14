"""Real component imports, versioned edits/deletes and shared popup routing."""

import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from hub.backend.project.component_commands import component_commands
from hub.backend.runtime import HubConfig, HubRuntime
from hub.ui.application import create_application
from hub.widget.forms.component_manager import ComponentManager
from hub.widget.forms.record_manager import RecordManager
from llm.components.rag.component import RAGConflictError
from tests.hub.support import SizedOutput, eventually
from tests.hub.test_components import config as rag_config


WORKFLOW = {"schema_version": 1, "entry": "done", "nodes": {"done": {"type": "end"}}, "edges": []}


class RecordManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_path_dropdown_tab_confirmation_and_filtered_settings_button(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            root = Path(directory)
            (root / "my-guide.md").write_text("Guide", encoding="utf-8")
            app, controller = create_application(HubConfig(root / "workspace", file_root=root, auto_title=False),
                                                  input=pipe, output=SizedOutput())
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: view.connected)
                view.composer.text = "/skill config"
                view.composer.buffer.cancel_completion()
                pipe.send_text("\r")
                await eventually(lambda: isinstance(view._dialog, RecordManager) and not view._dialog.busy)
                popup = view._dialog
                pipe.send_text("my-")
                await eventually(lambda: popup.path.buffer.complete_state is not None)
                pipe.send_text("\t")
                await eventually(lambda: popup.path.text == "my-guide.md")
                self.assertNotIn("my-guide", [item["name"] for item in popup.entries])
                from prompt_toolkit.completion import CompleteEvent
                from prompt_toolkit.document import Document
                with patch.dict("os.environ", {"HOME": str(root)}):
                    completions = list(popup.path.buffer.completer.get_completions(
                        Document("~/my-"), CompleteEvent()))
                self.assertEqual([item.text for item in completions], ["guide.md"])
                from prompt_toolkit.application.current import set_app
                with set_app(app):
                    popup.open_settings()
                await eventually(lambda: view.settings_open and view._dialog is None and
                                 view.settings.page is not None and not view.settings.busy)
                self.assertEqual(view.settings.page.query, "config.parameters.components.skills")
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    def test_aliases_require_registered_component_and_exclude_unmanaged_names(self):
        self.assertNotIn("skill", component_commands(("tools",)))
        self.assertNotIn("workflow", component_commands(("tools",)))
        self.assertEqual(component_commands(("skills", "workflows"))["skill"], "skills")
        self.assertEqual(component_commands(("skills", "skill"))["skill"], "skills")

    async def test_skill_workflow_agent_import_edit_conflict_delete_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "guide.md").write_text("# Coding guide\nRun relevant tests.", encoding="utf-8")
            (root / "flow.json").write_text(json.dumps(WORKFLOW), encoding="utf-8")
            (root / "reviewer.json").write_text(json.dumps({"purpose": "Review text", "engine": "loop"}), encoding="utf-8")
            config = HubConfig(root / "workspace", file_root=root, auto_title=False)
            runtime = HubRuntime(config)
            await runtime.start()
            project_id = runtime.project.id
            try:
                for name, filename, identifier in (("skills", "guide.md", "guide"), ("workflows", "flow.json", "flow"),
                                                   ("agents", "reviewer.json", "reviewer")):
                    async def manage(action, value="", version=None):
                        return await runtime.manage_records(project_id, name, action, value, version)
                    entries = await manage("import", filename)
                    self.assertIn(identifier, [item["name"] for item in entries])
                    with self.assertRaises(FileExistsError):
                        await manage("import", filename)
                    record = await manage("read", identifier)
                    values = json.loads(record["text"])
                    values.setdefault("metadata", {})["description"] = "Edited through manager"
                    await manage("save", {"name": identifier, "text": json.dumps(values)}, record["version"])
                    with self.assertRaises(ValueError):
                        await manage("delete", identifier, record["version"])
                    latest = await manage("read", identifier)
                    invalid = {} if name == "skills" else {**WORKFLOW, "schema_version": 0}
                    with self.assertRaises(ValueError):
                        await manage("save", {"name": identifier, "text": json.dumps(invalid)}, latest["version"])
                    self.assertEqual(await manage("read", identifier), latest)
            finally:
                await runtime.close()
            runtime = HubRuntime(config)
            await runtime.start()
            try:
                for name, identifier in (("skills", "guide"), ("workflows", "flow"), ("agents", "reviewer")):
                    record = await runtime.manage_records(project_id, name, "read", identifier)
                    self.assertIn("Edited through manager", record["text"])
                    entries = await runtime.manage_records(project_id, name, "delete", identifier, record["version"])
                    self.assertNotIn(identifier, [item["name"] for item in entries])
                self.assertTrue((root / "guide.md").exists())
                self.assertTrue((root / "flow.json").exists())
                await runtime.project.components.aselect(["tools"])
                with self.assertRaises(ValueError):
                    await runtime.manage_records(project_id, "skills", "list")
            finally:
                await runtime.close()

    async def test_commands_open_shared_manager_import_edit_and_delete(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            root = Path(directory)
            source = root / "guide.md"
            source.write_text("Original instructions", encoding="utf-8")
            app, controller = create_application(HubConfig(root / "workspace", file_root=root, auto_title=False),
                                                  input=pipe, output=SizedOutput())
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: view.connected)
                for command, name in (("skill", "skills"), ("rag", "rag"), ("workflow", "workflows"), ("agent", "agents")):
                    view.composer.text = f"/{command} config"
                    view.composer.buffer.cancel_completion()
                    pipe.send_text("\r")
                    await eventually(lambda: isinstance(view._dialog, RecordManager) and not view._dialog.busy)
                    popup = view._dialog
                    self.assertIsInstance(popup, ComponentManager)
                    self.assertEqual(popup.name, name)
                    self.assertIs(app.layout.current_control, popup.path.control)
                    self.assertEqual(view.composer.text, "")
                    if name == "skills":
                        popup.path.text = "guide.md"
                        popup.path.buffer.cancel_completion()
                        pipe.send_text("\r")
                        await eventually(lambda: not popup.busy and popup.selected == "guide")
                        self.assertEqual(popup.path.text, "")
                        pipe.send_text("\t")
                        await eventually(lambda: app.layout.current_control is popup.list)
                        edited = json.dumps({"instructions": "Revised instructions", "title": "Revised"})
                        with patch("hub.ui.host.edit_text", new_callable=AsyncMock, return_value=edited) as editor:
                            pipe.send_text("\r")
                            await eventually(lambda: editor.await_count == 1 and not popup.busy and
                                             any(item.get("label") == "Revised" for item in popup.entries))
                        self.assertFalse(popup.drafts)
                        pipe.send_text("d")
                        await eventually(lambda: view._dialog is not popup)
                        pipe.send_text("\x1b")
                        await eventually(lambda: view._dialog is popup)
                        self.assertEqual(popup.selected, "guide")
                        with set_app(app):
                            popup.delete()
                        pipe.send_text("\x1b[Z\r")  # Cancel is initially focused; select Confirm.
                        await eventually(lambda: view._dialog is popup and not popup.busy and
                                         all(item["name"] != "guide" for item in popup.entries))
                    pipe.send_text("\x1b")
                    await eventually(lambda: view._dialog is None)
                self.assertEqual(source.read_text(), "Original instructions")
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("chromadb", "kuzu", "rank_bm25")),
                     "Optional RAG dependencies are not installed")
class DocumentManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_and_edit_rebuild_index_and_reject_stale_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "note.md"
            path.write_text("Old searchable document", encoding="utf-8")
            settings = rag_config(Path(directory) / "workspace")
            settings.project_config["parameters"]["components"]["rag"]["config"]["search"] = {
                "candidate_count": 20, "rrf_constant": 60}
            runtime = HubRuntime(settings)
            await runtime.start()
            try:
                async def manage(action, argument="", version=None):
                    return await runtime.manage_records(runtime.project.id, "rag", action, argument, version)
                entries = await manage("import", str(path))
                self.assertEqual(entries[0]["name"], "note")
                record = await manage("read", "note")
                values = json.loads(record["text"])
                values["content"] = "Revised searchable document"
                entries = await manage("save", {"name": "note", "text": json.dumps(values)}, record["version"])
                self.assertEqual(entries[0]["version"], 2)
                data = await runtime.project.components.aget("rag")
                hits = await data.asearch_documents("Revised", method="bm25", expand="document", limit=5, rerank=False)
                self.assertTrue(hits)
                with self.assertRaises(RAGConflictError):
                    await manage("delete", "note", record["version"])
                self.assertEqual(await manage("delete", "note", 2), [])
                self.assertEqual(await data.asearch_documents("Revised", method="bm25", expand="document", limit=5, rerank=False), [])
                self.assertEqual(path.read_text(), "Old searchable document")
            finally:
                await runtime.close()
