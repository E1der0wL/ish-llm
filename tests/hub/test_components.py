"""RAG slash commands use indexed document APIs; settings keep normal navigation."""

import asyncio
import importlib.util
import json
import tempfile
import unittest

from prompt_toolkit.input import create_pipe_input

from hub.backend.runtime import HubConfig, HubRuntime
from hub.ui.application import create_application
from llm.components.rag import RAGComponent, EmbeddingModel
from tests.hub.test_live import until
from tests.hub.test_mockup import SizedOutput


async def embedding(**request):
    return {"data": [{"index": i, "embedding": [1.0, 2.0, 3.0]}
                     for i, _ in enumerate(request["input"])]}


class Extractor:
    async def extract(self, chunks):
        return {"entities": [], "relations": []}


def config(directory):
    return HubConfig(directory, auto_title=False,
        component_factories=(lambda: RAGComponent(
            embedding=EmbeddingModel(model="test/embedding", embedding_fn=embedding), extractor=Extractor()),),
        project_config={"parameters": {"components": {"rag": {'config': {'chunk_size': 512, 'embedding_cache_max_bytes': 0, 'extraction_batch_size': 4, 'search_cache_chars': 0, 'index_batch_size': 32, 'graph': {'buffer_pool_size': 64 * 1024 * 1024, 'max_num_threads': 1}}, 'policy': {'embedding_concurrency': 1, 'extraction': {'failure_policy': 'required'}}}}}})


class ComponentUITests(unittest.IsolatedAsyncioTestCase):
    async def test_component_settings_focus_field_and_empty_schema_without_removed_checklist(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            app, controller = create_application(HubConfig(directory, auto_title=False), input=pipe, output=SizedOutput())
            view, screen = controller.view, controller.view.settings
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await until(lambda: view.connected)
                for name in ("rag", "skills"):
                    view.composer.text = f"/{name} settings"
                    view.composer.buffer.cancel_completion()
                    pipe.send_text("\r")
                    await until(lambda: view.settings_open and not controller.component_commands.busy)
                    self.assertEqual(screen.selected, view.project_id)
                    self.assertEqual(screen._left_key, view.project_id)
                    form = screen.page.component_forms[name]
                    expected = next(iter(form.fields.values())).input if form.fields else screen.page.body_widgets()[0]
                    self.assertIs(app.layout.current_control, expected.control)
                    self.assertEqual(screen._main_zone, 0)
                    pipe.send_text("\t")
                    await until(lambda: app.layout.current_control == screen.page.save.control)
                    pipe.send_text("\x13")
                    await until(lambda: not view.settings_open)
            finally:
                app.exit()
                await task
                await asyncio.to_thread(controller.close)


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("chromadb", "kuzu", "rank_bm25")),
                     "Optional RAG dependencies are not installed")
class RAGCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_document_crud_reopen_and_component_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = config(directory)
            runtime = HubRuntime(settings)
            try:
                await runtime.start()
                if not runtime.sessions:
                    await runtime.new_session()
                project_id = runtime.project.id
                async def command(text):
                    return await runtime.component_command(project_id, "rag", text)
                self.assertEqual(await command("list"), [])
                created = await command('create note ' + json.dumps({"title": "Memo", "content": "First content"}))
                self.assertEqual(created["id"], "note")
                self.assertNotIn("vectors", created)
                with self.assertRaises(FileExistsError):
                    await command('create note {"title":"Duplicate","content":"No overwrite"}')
                updated = await command('update note {"content":"Revised content","expected_revision":1}')
                self.assertEqual(updated["revision"], 2)
                self.assertEqual((await command("get note"))["content"], "Revised content")
                with self.assertRaises(ValueError):
                    await command("add file.md")  # This convenience command is not implemented.
                self.assertEqual([item["id"] for item in await command("list")], ["note"])
            finally:
                await runtime.close()
            runtime = HubRuntime(settings)
            try:
                await runtime.start()
                if not runtime.sessions:
                    await runtime.new_session()
                self.assertEqual((await runtime.component_command(project_id, "rag", "get note"))["revision"], 2)
                await runtime.component_command(project_id, "rag", "delete note")
                self.assertEqual(await runtime.component_command(project_id, "rag", "list"), [])
                await runtime.project.components.aselect(["tools"])
                with self.assertRaisesRegex(ValueError, "rag"):
                    await runtime.component_command(project_id, "rag", "list")
            finally:
                await runtime.close()
