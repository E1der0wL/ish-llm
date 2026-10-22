"""Real component imports, versioned edits/deletes and shared popup routing."""

from tests.llm.support.skills import skill_markdown

import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout.layout import walk
from prompt_toolkit.layout.containers import to_container

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
    async def test_all_manager_settings_buttons_select_component_and_focus_its_fields(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(directory, auto_title=False),
                                                  input=pipe, output=SizedOutput())
            view, screen = controller.view, controller.view.settings
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: view.connected)
                # The first visit loads the catalog/project; later visits reuse drafts.
                for name in ("tools", "skills", "mcp", "rag", "workflows", "agents", "prompts", "memory", "tools"):
                    with self.subTest(component=name):
                        view.composer.text = f"/{name} config"
                        view.composer.buffer.cancel_completion()
                        pipe.send_text("\r")
                        await eventually(lambda: isinstance(view._dialog, ComponentManager) and not view._dialog.busy)
                        popup = view._dialog
                        buttons = next(node.content.hub_button_group for node in walk(to_container(popup))
                                       if getattr(getattr(node, "content", None), "hub_button_group", None))
                        if name == "agents":
                            self.assertFalse(any(button.handler == popup.open_settings for button in buttons))
                            self.assertIn(popup.edit_button, buttons)
                            self.assertIn(popup.create_button, buttons)
                            with set_app(app):
                                view.close_dialog()
                            continue
                        pipe.send_text("\t\t")  # InputField action, then component list.
                        await eventually(lambda: app.layout.current_control is popup.list)
                        pipe.send_text("\t")
                        await eventually(lambda: app.layout.current_control is buttons[0].control)
                        pipe.send_text("\x1b[C")
                        await eventually(lambda: app.layout.current_control is buttons[1].control)
                        pipe.send_text("\r")
                        await eventually(lambda: view.settings_open and view._dialog is None and not screen.busy)
                        page = screen.page
                        self.assertEqual(page.identifier, view.project_id)
                        self.assertEqual(page.section, "component:" + name)
                        self.assertEqual(screen.selected, page.section)
                        self.assertEqual(screen.navigation.left_key, page.section)
                        self.assertEqual(page.query, "")
                        self.assertEqual(screen.query, "")
                        self.assertEqual(screen.tabs.selected, "config")
                        self.assertEqual(page.fields(), [field for field in page.component_forms[name].fields.values()
                                                        if page.field_tabs[field] == "config"])
                        self.assertTrue(page.fields())
                        self.assertIs(app.layout.current_control, page.fields()[0].input.control)
                        with set_app(app):
                            screen.filter("no-such-setting")
                            screen.tabs.select("policy")
                            view.toggle_settings()
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    async def test_path_dropdown_tab_confirmation_and_component_settings_button(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            root = Path(directory)
            (root / "my-guide.md").write_text("Guide", encoding="utf-8")
            app, controller = create_application(HubConfig(root / "workspace", file_root=root, auto_title=False),
                                                  input=pipe, output=SizedOutput())
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: view.connected)
                view.composer.text = "/skills config"
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
                self.assertEqual(view.settings.page.query, "")
                self.assertEqual(view.settings.page.section, "component:skills")
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)

    def test_commands_require_registered_component_and_exclude_aliases(self):
        self.assertEqual(component_commands(("tools",)), {"tools": "tools", "agents": "agents"})
        self.assertEqual(component_commands(("skills", "workflows")), {"skills": "skills", "agents": "agents", "workflows": "workflows"})
        self.assertEqual(component_commands(("skills", "skill")), {"skills": "skills", "agents": "agents"})
        self.assertEqual(component_commands(("mcp",)), {"mcp": "mcp", "agents": "agents"})

    async def test_skill_workflow_agent_mcp_import_edit_conflict_delete_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "guide" / "SKILL.md"
            source.parent.mkdir()
            source.write_text(skill_markdown("guide", "Run relevant tests."), encoding="utf-8")
            (root / "flow.json").write_text(json.dumps(WORKFLOW), encoding="utf-8")
            (root / "reviewer.json").write_text(json.dumps({"purpose": "Review text", "engine": "loop"}), encoding="utf-8")
            (root / "server.json").write_text(json.dumps({"transport": "stdio", "command": "not-executed-server"}), encoding="utf-8")
            config = HubConfig(root / "workspace", file_root=root, auto_title=False)
            runtime = HubRuntime(config)
            await runtime.start()
            project_id = runtime.project.id
            try:
                for name, filename, identifier in (("skills", "guide/SKILL.md", "guide"), ("workflows", "flow.json", "flow"),
                                                   ("agents", "reviewer.json", "reviewer"), ("mcp", "server.json", "server")):
                    async def manage(action, value="", version=None):
                        return await runtime.manage_records(project_id, name, action, value, version)
                    entries = await manage("import", filename)
                    self.assertIn(identifier, [item["name"] for item in entries])
                    with self.assertRaises(FileExistsError):
                        await manage("import", filename)
                    record = await manage("read", identifier)
                    if name == "skills":
                        text = skill_markdown("guide", "Edited through manager")
                    else:
                        values = json.loads(record["text"])
                        values.setdefault("metadata", {})["description"] = "Edited through manager"
                        text = json.dumps(values)
                    await manage("save", {"name": identifier, "text": text}, record["version"])
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
                for name, identifier in (("skills", "guide"), ("workflows", "flow"), ("agents", "reviewer"), ("mcp", "server")):
                    record = await runtime.manage_records(project_id, name, "read", identifier)
                    self.assertIn("Edited through manager", record["text"])
                    entries = await runtime.manage_records(project_id, name, "delete", identifier, record["version"])
                    self.assertNotIn(identifier, [item["name"] for item in entries])
                self.assertEqual(source.read_text(), skill_markdown("guide", "Run relevant tests."))
                self.assertTrue((root / "flow.json").exists())
                self.assertTrue((root / "server.json").exists())
                await runtime.project.components.aselect(["tools"])
                with self.assertRaises(ValueError):
                    await runtime.manage_records(project_id, "skills", "list")
            finally:
                await runtime.close()

    async def test_commands_open_shared_manager_import_edit_and_delete(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            root = Path(directory)
            source = root / "guide" / "SKILL.md"
            source.parent.mkdir()
            source.write_text(skill_markdown("guide", "Original instructions"), encoding="utf-8")
            app, controller = create_application(HubConfig(root / "workspace", file_root=root, auto_title=False),
                                                  input=pipe, output=SizedOutput())
            view = controller.view
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: view.connected)
                for name in ("skills", "rag", "workflows", "agents", "mcp"):
                    view.composer.text = f"/{name} config"
                    view.composer.buffer.cancel_completion()
                    pipe.send_text("\r")
                    await eventually(lambda: isinstance(view._dialog, RecordManager) and not view._dialog.busy)
                    popup = view._dialog
                    self.assertIsInstance(popup, ComponentManager)
                    self.assertEqual(popup.name, name)
                    self.assertIs(app.layout.current_control, popup.path.control)
                    self.assertEqual(view.composer.text, "")
                    if name == "skills":
                        popup.path.text = "guide/SKILL.md"
                        popup.path.buffer.cancel_completion()
                        pipe.send_text("\r")
                        await eventually(lambda: not popup.busy and popup.selected == "guide")
                        self.assertEqual(popup.path.text, "")
                        pipe.send_text("\t\t")  # InputField action, then component list.
                        await eventually(lambda: app.layout.current_control is popup.list)
                        edited = skill_markdown("guide", "Revised instructions", "Revised")
                        with patch("hub.ui.host.edit_text", new_callable=AsyncMock, return_value=edited) as editor:
                            pipe.send_text("\r")
                            await eventually(lambda: editor.await_count == 1 and not popup.busy and
                                             any(item.get("description") == "Revised" for item in popup.entries))
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
                self.assertEqual(source.read_text(), skill_markdown("guide", "Original instructions"))
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
                self.assertEqual(entries[0]["version"]["revision"], 2)
                data = await runtime.project.components.aget("rag")
                hits = await data.asearch_documents("Revised", method="bm25", expand="document", limit=5, rerank=False)
                self.assertTrue(hits)
                with self.assertRaises(RAGConflictError):
                    await manage("delete", "note", record["version"])
                self.assertEqual(await manage("delete", "note", entries[0]["version"]), [])
                self.assertEqual(await data.asearch_documents("Revised", method="bm25", expand="document", limit=5, rerank=False), [])
                self.assertEqual(path.read_text(), "Old searchable document")
                # 삭제 후 같은 ID/revision으로 다시 등록해도 이전 UI 편집 토큰은 재사용할 수 없다.
                recreated = await manage("import", str(path))
                self.assertEqual(recreated[0]["version"]["revision"], record["version"]["revision"])
                self.assertNotEqual(recreated[0]["version"]["generation"], record["version"]["generation"])
                latest = await manage("read", "note")
                with self.assertRaises(RAGConflictError):
                    await manage("save", {"name": "note", "text": json.dumps(values)}, record["version"])
                with self.assertRaises(RAGConflictError):
                    await manage("delete", "note", record["version"])
                self.assertEqual(await manage("read", "note"), latest)
            finally:
                await runtime.close()
