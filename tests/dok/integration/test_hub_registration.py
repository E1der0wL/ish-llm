"""Exercise registration through the prompt lifecycle without requiring Hub."""
import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from prompt_toolkit.input import create_pipe_input

from dok import install, uninstall
from dok.integrations.hub import HubAgents, RegistrationState
from tests.dok.support import HostPrompt, render, wait_running


async def wait_state(dock, state):
    async with asyncio.timeout(10):
        while dock.hub_registration.state != state:
            await asyncio.sleep(0.01)


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.pipe = self.enterContext(create_pipe_input())
        self.prompt = HostPrompt(self.pipe)
        self.prompt.logger = Mock()
        self.dock = install(self.prompt, config_path=Path(directory) / "config.jsonc")
        self.addCleanup(uninstall, self.prompt)
        self.task = None
        self.addAsyncCleanup(self.stop_prompt)
        self.api = SimpleNamespace(INSTALLATION_ATTRIBUTE="test_hub_installation")
        self.enterContext(patch.dict(sys.modules, {"hub.hub": SimpleNamespace(HubInstallation=self.api)}))
        self.definition = self.enterContext(patch.object(HubAgents, "_definition", return_value=object()))

    async def start_prompt(self):
        self.task = asyncio.create_task(self.prompt.read_and_dispatch())
        await wait_running(self.prompt.app)
        await render(self.prompt.app)

    async def stop_prompt(self):
        if self.task is not None and not self.task.done():
            self.prompt.app.exit(result="")
        if self.task is not None:
            await self.task

    def install_hub(self, register):
        hub = SimpleNamespace(controller=SimpleNamespace(closed=False),
                              plugin_client=Mock(return_value=SimpleNamespace(register=register)))
        setattr(self.prompt, self.api.INSTALLATION_ATTRIBUTE, hub)
        return hub

    async def test_absent_preview_and_late_installation(self):
        await self.start_prompt()
        self.assertEqual(self.dock.hub_registration.state, RegistrationState.UNAVAILABLE)
        setattr(self.prompt, self.api.INSTALLATION_ATTRIBUTE, SimpleNamespace(controller=None))
        await render(self.prompt.app)
        self.definition.assert_not_called()
        register = AsyncMock()
        hub = self.install_hub(register)
        await render(self.prompt.app)
        await wait_state(self.dock, RegistrationState.REGISTERED)
        hub.plugin_client.assert_called_once_with("dok")
        register.assert_awaited_once_with(self.definition.return_value)
        self.dock.reload()
        await render(self.prompt.app)
        register.assert_awaited_once()

    async def test_registration_survives_enter_and_does_not_block_next_prompt(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def delayed(definition):
            started.set()
            await release.wait()

        register = AsyncMock(side_effect=delayed)
        self.install_hub(register)
        await self.start_prompt()
        await asyncio.wait_for(started.wait(), 3)
        self.pipe.send_text("echo ready\r")
        self.assertEqual(await asyncio.wait_for(self.task, 3), "echo ready")
        await self.start_prompt()
        self.assertEqual(self.dock.hub_registration.state, RegistrationState.REGISTERING)
        release.set()
        await wait_state(self.dock, RegistrationState.REGISTERED)
        register.assert_awaited_once()

    async def test_error_is_observable_and_retries_only_on_reload_or_replacement(self):
        register = AsyncMock(side_effect=[RuntimeError("workspace busy"), None])
        self.install_hub(register)
        await self.start_prompt()
        await wait_state(self.dock, RegistrationState.FAILED)
        self.assertEqual(self.dock.hub_registration.diagnostic, "workspace busy")
        self.prompt.logger.warning.assert_called_once()
        await render(self.prompt.app)
        register.assert_awaited_once()
        self.assertTrue(self.dock.reload())
        await wait_state(self.dock, RegistrationState.REGISTERED)
        self.assertEqual(register.await_count, 2)
        self.assertEqual(self.dock.hub_registration.diagnostic, "")
        replacement = AsyncMock()
        self.install_hub(replacement)
        await render(self.prompt.app)
        await wait_state(self.dock, RegistrationState.REGISTERED)
        replacement.assert_awaited_once()

    async def test_uninstall_cancels_only_dok_registration(self):
        started, stopped = asyncio.Event(), asyncio.Event()

        async def delayed(definition):
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        hub = self.install_hub(AsyncMock(side_effect=delayed))
        await self.start_prompt()
        await asyncio.wait_for(started.wait(), 3)
        uninstall(self.prompt)
        await asyncio.wait_for(stopped.wait(), 3)
        self.assertFalse(hub.controller.closed)
        self.assertEqual(self.dock.hub_registration.state, RegistrationState.UNAVAILABLE)

    async def test_replaced_hub_cannot_publish_a_late_registration_failure(self):
        started, stopped = asyncio.Event(), asyncio.Event()

        async def replaced(definition):
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                stopped.set()
                raise RuntimeError("old Hub closed")

        self.install_hub(AsyncMock(side_effect=replaced))
        await self.start_prompt()
        await asyncio.wait_for(started.wait(), 3)
        new_registration = AsyncMock()
        self.install_hub(new_registration)
        await render(self.prompt.app)
        await asyncio.wait_for(stopped.wait(), 3)
        await wait_state(self.dock, RegistrationState.REGISTERED)
        new_registration.assert_awaited_once()
        self.prompt.logger.warning.assert_not_called()
