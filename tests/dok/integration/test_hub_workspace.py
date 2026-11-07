"""Use the optional installed plugins and a temporary Hub workspace, offline."""
import asyncio
from contextlib import asynccontextmanager
from importlib.util import find_spec
import json
from pathlib import Path
import tempfile
import unittest

from prompt_toolkit.input import create_pipe_input

from dok import install, uninstall
from dok.integrations.hub import RegistrationState
from tests.dok.support import HostPrompt, wait_running


@unittest.skipUnless(find_spec("hub") and find_spec("llm"), "optional Hub + llm integration")
class HubWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    @asynccontextmanager
    async def installation(self, root, *, hub_first):
        from hub.hub import HubConfig, install as install_hub

        with create_pipe_input() as pipe:
            prompt = HostPrompt(pipe)
            config = HubConfig(workspace=root / "workspace", file_root=root, prepare_provider=False,
                               postprocess=False, auto_title=False)
            if hub_first:
                hub = install_hub(prompt, config=config)
            dock = install(prompt, config_path=root / "dock.jsonc")
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

        async def manage(hub, action, target_project, argument="", version=None):
            home = hub.view.project_id
            future = hub.controller.worker.call(HubRuntime.manage_agents.__name__, home, target_project or home,
                                               action, argument, version)
            return await asyncio.wrap_future(future)

        async def dok_project(hub):
            rows = await manage(hub, ManagementAction.LIST, None)
            owned = [row for row in rows if row.get(AgentLocation.PLUGIN) == "Dok"]
            self.assertCountEqual([row[AgentLocation.AGENT] for row in owned],
                                  ["command-analysis", "calendar", "shell-assistant"])
            projects = {row[AgentLocation.PROJECT] for row in owned}
            self.assertEqual(len(projects), 1)
            project = projects.pop()
            self.assertNotEqual(project, hub.view.project_id)
            return project

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            async with self.installation(root, hub_first=False) as hub:
                project = await dok_project(hub)
                original = EditorRecord(await manage(hub, ManagementAction.READ, project, "command-analysis"))
                definition = json.loads(original.text)
                self.assertEqual(definition["tools"], [])
                self.assertNotIn("completion", definition)
                definition["system_prompt"] = "My saved instruction"
                definition["completion"] = {"model": "my-selected-model"}
                await manage(hub, ManagementAction.SAVE, project,
                             EditorRecord.change("command-analysis", json.dumps(definition)), original.version)
            async with self.installation(root, hub_first=True) as hub:
                self.assertEqual(await dok_project(hub), project)
                saved = EditorRecord(await manage(hub, ManagementAction.READ, project, "command-analysis"))
                self.assertEqual(json.loads(saved.text), definition)
