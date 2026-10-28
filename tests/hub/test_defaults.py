"""Default installation and fixed registrations through the public backend API."""

from tests.llm.support.skills import skill_markdown
from llm.core.skills import SkillDocument

from llm.providers.litellm import LiteLLMCompletionAdapter

import asyncio
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prompt_toolkit.input import create_pipe_input

from hub.hub import install
from hub.backend.runtime import HubConfig, HubRuntime
from hub.asset.guides import SKILLS, SYSTEM_PROMPT, TEMPLATE_VERSION
from llm.components.workflows import WorkflowGraph
from tests.hub.test_ish_integration import Prompt
from tests.hub.test_live import until
from tests.hub.support import SizedOutput


COMPONENTS = {"tools", "skills", "mcp", "rag", "workflows", "memory", "data", "prompts", "builtin_tools", "goals", "refinement", "vision"}


class DefaultTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_agent_definition_does_not_inherit_rag_selection(self):
        for definition in ({'engine': 'loop'}, {'engine': 'loop', 'resources': {'rag': False}}):
            with self.subTest(definition=definition), tempfile.TemporaryDirectory() as root:
                runtime = HubRuntime(HubConfig(root, auto_title=False, agent_definition=definition))
                try:
                    await runtime.start()
                    self.assertEqual(await runtime.project.agents.aload('assistant'), definition)
                finally:
                    await runtime.close()

    async def test_starter_guides_are_editable_and_not_reseeded(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(directory, auto_title=False))
            try:
                await runtime.start()
                project_id = runtime.project.id
                config = (await runtime.project.aget_data()).config
                self.assertEqual((await runtime.project.agents.aload("assistant"))["system_prompt"], SYSTEM_PROMPT)
                self.assertEqual((await runtime.project.agents.aload("assistant"))["resources"], {"rag": True})
                self.assertNotIn("completion", config.parameters["engines"]["loop"])
                skills = await runtime.project.components.aget("skills")
                self.assertEqual(set(await skills.alist()), {item[0] for item in SKILLS})
                for identifier, title, description, instructions in SKILLS:
                    guide = await skills.aload(identifier)
                    document = SkillDocument.parse(guide["markdown"])
                    self.assertEqual(document.name, identifier)
                    self.assertEqual(document.frontmatter["description"], description)
                    self.assertEqual(guide["markdown"].replace('\r\n', '\n'), instructions)
                    self.assertEqual(document.frontmatter["metadata"]["hub-template-version"], str(TEMPLATE_VERSION))
                record = await runtime.project.agents.asnapshot("assistant")
                definition = record["definition"]
                definition["system_prompt"] = "My project prompt"
                definition["resources"]["rag"] = False
                await runtime.project.agents.arevise("assistant", definition,
                                                    expected_revision=record["revision"])
                await skills.aupdate("hub-code-change", {"markdown": skill_markdown("hub-code-change", "My project rules")})
                await skills.adelete("hub-document")
                created = await runtime.settings_service.create_project({"title": "Custom", "config": {}})
                other = await runtime.backend.projects.aload(created["project"]["id"])
                self.assertEqual((await other.agents.aload("assistant"))["system_prompt"], SYSTEM_PROMPT)
                self.assertEqual(len(await (await other.components.aget("skills")).alist()), len(SKILLS))
            finally:
                await runtime.close()
            reopened = HubRuntime(HubConfig(directory, project_id=project_id, auto_title=False))
            try:
                await reopened.start()
                config = (await reopened.project.aget_data()).config
                self.assertEqual((await reopened.project.agents.aload("assistant"))["system_prompt"], "My project prompt")
                self.assertFalse((await reopened.project.agents.aload("assistant"))["resources"]["rag"])
                skills = await reopened.project.components.aget("skills")
                self.assertEqual((await skills.aload("hub-code-change"))["markdown"], skill_markdown("hub-code-change", "My project rules"))
                self.assertNotIn("hub-document", await skills.alist())
            finally:
                await reopened.close()

    async def test_loop_discovers_and_reads_a_starter_guide(self):
        import json
        from llm.engines.loop import LoopEngine
        from tests.llm.test_loop import ScriptedCompletion, call, chunk
        provider = ScriptedCompletion(
            [chunk(calls=[call("{}", name="skill_list", call_id="list")]), chunk(finish="tool_calls")],
            [chunk(calls=[call(json.dumps({"identifier": "hub-code-change"}), name="skill_read", call_id="read")]),
             chunk(finish="tool_calls")],
            [chunk("Guide read", finish="stop")])
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(directory, model="test/model", auto_title=False,
                engine_factories={"loop": lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
            try:
                await runtime.start()
                identifier = await runtime.new_session()
                await runtime.submit(identifier, "Review the code")
                session = runtime.sessions[identifier]
                await session.run.wait_idle()
                run = (await session.run.alist())[-1]
                self.assertEqual(str((await run.aresult()).status), "completed")
                self.assertEqual(provider.requests[0]["messages"][0]["content"], SYSTEM_PROMPT)
                tools = {item["function"]["name"] for item in provider.requests[0]["tools"]}
                self.assertTrue({"skill_list", "skill_read", "rag_search"}.issubset(tools))
                messages = provider.requests[-1]["messages"]
                self.assertTrue(any(message["role"] == "tool" and "Preserve unrelated edits" in message["content"]
                                    for message in messages))
            finally:
                await runtime.close()

    async def test_install_without_config_opens_live_settings_without_model(self):
        with tempfile.TemporaryDirectory() as directory, create_pipe_input() as pipe:
            prompt = Prompt(input=pipe, output=SizedOutput())
            with patch("hub.backend.runtime.Path.home", return_value=Path(directory)):
                installation = install(prompt)
            view = installation.view
            self.assertIsNotNone(installation.controller)
            self.assertEqual(installation.controller.config.workspace, Path(directory) / ".ish/hub-workspace")
            task = asyncio.create_task(prompt.prompt_async(pre_run=lambda: view.show(prompt.app)))
            try:
                await until(lambda: view.connected and not view.projects.busy)
                self.assertEqual(view.model, "")
                self.assertTrue(view.no_sessions)
                self.assertEqual(view.notice, view.t("session_required"))
                pipe.send_text('\x10\r')
                await until(lambda: view.settings_open and view.settings.page is not None and not view.settings.busy)
                view.settings.choose(view.project_id)
                await until(lambda: getattr(view.settings.page, "identifier", None) == view.project_id)
                self.assertEqual(set(view.settings.page.components), COMPONENTS)
                self.assertFalse(hasattr(view.settings.page, "top"))
            finally:
                prompt.app.exit(result="")
                await task
                await asyncio.to_thread(installation.close)

    async def test_default_components_graph_and_new_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = HubRuntime(HubConfig(directory, auto_title=False))
            try:
                await runtime.start()
                if not runtime.sessions:
                    await runtime.new_session()
                original = await runtime.project.aget_data()
                self.assertEqual(set(original.components), COMPONENTS)
                self.assertIn("assistant", await runtime.project.agents.alist())
                self.assertEqual((await runtime.snapshot()).engines, ("loop", "graph"))
                session = runtime.sessions[runtime.selected_id]
                with self.assertRaisesRegex(ValueError, r"completion\.model"):
                    await runtime.submit(session.id, "preserve draft")
                self.assertEqual(await session.aconversation(), [])
                # A pure Graph workflow must execute without any Loop/model configuration.
                workflows = await runtime.project.components.aget("workflows")
                await workflows.acreate(WorkflowGraph(entry="finish").node("finish", "end").to_dict(), identifier="done")
                await runtime.project.agents.acreate({"engine": "graph", "engine_options": {"workflow": "done"}},
                                                     identifier="graph")
                await session.run.shutdown()
                await session.asave(agent_ids=["assistant", "graph"])
                await runtime.submit(session.id, "finish", "graph")
                await session.run.wait_idle()
                run = (await session.run.alist())[-1]
                self.assertEqual(str((await run.aget_data()).status), "completed")
                self.assertIsNone((await runtime.submission_state(session.id))["run_id"])
                record = await runtime.settings_service.load_project(runtime.project.id)
                values = deepcopy(record["values"])
                values.update(title="New")
                values.pop("components")
                created = await runtime.settings_service.create_project(values)
                self.assertEqual(set(created["values"]["components"]), COMPONENTS)
            finally:
                await runtime.close()
