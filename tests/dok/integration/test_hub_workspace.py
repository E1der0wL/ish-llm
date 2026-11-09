"""Use the optional installed plugins and a temporary Hub workspace, offline."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from importlib.util import find_spec
import json
from pathlib import Path
import tempfile
import unittest

from prompt_toolkit.input import create_pipe_input

from dok import install, uninstall
from dok.integrations.hub import RegistrationState
from dok.integrations.hub_config import HubConfig
from tests.dok.support import HostPrompt, wait_running


@unittest.skipUnless(find_spec("hub") and find_spec("llm"), "optional Hub + llm integration")
class HubWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def installation(self, root, *, hub_first, settings=HubConfig()):
        from hub.hub import HubConfig, install as install_hub

        with create_pipe_input() as pipe:
            prompt = HostPrompt(pipe)
            config = HubConfig(workspace=root / "workspace", file_root=root, prepare_provider=False,
                               postprocess=False, auto_title=False)
            if hub_first:
                hub = install_hub(prompt, config=config)
            dock = install(prompt, config_path=root / "dock.jsonc")
            dock.configure(replace(dock.config, hub=settings))
            if not hub_first:
                hub = install_hub(prompt, config=config)
            task = asyncio.create_task(prompt.read_and_dispatch(default="draft"))
            try:
                await wait_running(prompt.app)
                async with asyncio.timeout(45):
                    while dock.hub_registration.state not in (RegistrationState.REGISTERED, RegistrationState.FAILED):
                        await asyncio.sleep(0.02)
                    self.assertEqual(dock.hub_registration.state, RegistrationState.REGISTERED,
                                     dock.hub_registration.diagnostic)
                    while not hub.view.project_id:
                        await asyncio.sleep(0.02)
                self.assertFalse(hub.view.visible)
                self.assertEqual(prompt.default_buffer.text, "draft")
                self.assertIs(prompt.app.layout.current_control.buffer, prompt.default_buffer)
                yield hub
            finally:
                uninstall(prompt)
                if prompt.app.is_running and not prompt.app.is_done:
                    prompt.app.exit(result="")
                await task
                await hub.controller.aclose()
                hub.close()

    async def test_agent_manager_edits_survive_a_second_ish_installation(self):
        from hub.backend.project.management_values import AgentLocation, EditorRecord, ManagementAction
        from hub.backend.runtime import HubRuntime
        from llm.core.agents import AgentDefinition
        from llm.providers.request_values import LiteLLMParameters

        async def manage(hub, action, target_project, argument="", version=None):
            home = hub.view.project_id
            future = hub.controller.worker.call(HubRuntime.manage_agents.__name__, home, target_project or home,
                                               action, argument, version)
            return await asyncio.wrap_future(future)

        async def dok_project(hub):
            rows = await manage(hub, ManagementAction.LIST, None)
            owned = [row for row in rows if row.get(AgentLocation.PLUGIN) == "Dok"]
            self.assertCountEqual([row[AgentLocation.AGENT] for row in owned],
                                  ["command-analysis", "calendar", "shell-assistant", "command-options"])
            projects = {row[AgentLocation.PROJECT] for row in owned}
            self.assertEqual(len(projects), 1)
            project = projects.pop()
            self.assertNotEqual(project, hub.view.project_id)
            return project

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initial = HubConfig(model="provider/test", api_key="test-key", api_base="https://example.invalid/v1")
            async with self.installation(root, hub_first=False, settings=initial) as hub:
                project = await dok_project(hub)
                for identifier in ("command-analysis", "calendar", "shell-assistant", "command-options"):
                    record = EditorRecord(await manage(hub, ManagementAction.READ, project, identifier))
                    effort = "low" if identifier == "command-options" else "medium"
                    self.assertEqual(json.loads(record.text)[AgentDefinition.Field.COMPLETION],
                                     {**initial.completion(), LiteLLMParameters.REASONING_EFFORT: effort})
                original = EditorRecord(await manage(hub, ManagementAction.READ, project, "command-analysis"))
                definition = json.loads(original.text)
                self.assertEqual(definition["tools"], [])
                definition["system_prompt"] = "My saved instruction"
                definition["completion"] = {"model": "my-selected-model"}
                await manage(hub, ManagementAction.SAVE, project,
                             EditorRecord.change("command-analysis", json.dumps(definition)), original.version)
            async with self.installation(root, hub_first=True, settings=HubConfig(model="provider/new")) as hub:
                self.assertEqual(await dok_project(hub), project)
                saved = EditorRecord(await manage(hub, ManagementAction.READ, project, "command-analysis"))
                self.assertEqual(json.loads(saved.text), definition)
                preserved = EditorRecord(await manage(hub, ManagementAction.READ, project, "command-options"))
                self.assertEqual(json.loads(preserved.text)[AgentDefinition.Field.COMPLETION],
                                 {**initial.completion(), LiteLLMParameters.REASONING_EFFORT: "low"})
