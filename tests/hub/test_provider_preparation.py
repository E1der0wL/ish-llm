"""Application startup choices do not become backend inference defaults."""

import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from hub.backend.bootstrap import prepare_backend
from hub.backend.runtime import HubConfig


class ProviderPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_hub_selects_local_preparation_and_preserves_project_options(self):
        with tempfile.TemporaryDirectory() as directory:
            config = HubConfig(workspace=directory, project_config={'policies': {}})
            with patch('hub.backend.execution.provider_adapter.HubCompletionAdapter.ainitialize', new_callable=AsyncMock) as prepare:
                await prepare_backend(config)
                prepare.assert_awaited_once_with()
            self.assertEqual(config.project_config, {'policies': {}})

    async def test_opt_out_and_custom_loop_do_not_initialize_unused_provider(self):
        class FalseyFactories(dict):
            def __bool__(self):
                return False
        with tempfile.TemporaryDirectory() as directory:
            for values in ({'prepare_provider': False}, {'engine_factories': {'loop': lambda: object()}},
                           {'engine_factories': FalseyFactories(loop=lambda: object())}):
                with self.subTest(values=values), patch('hub.backend.execution.provider_adapter.HubCompletionAdapter.ainitialize', new_callable=AsyncMock) as prepare:
                    await prepare_backend(HubConfig(workspace=directory, **values))
                    prepare.assert_not_called()

    def test_startup_option_requires_explicit_boolean(self):
        for value in (None, 1, 'true'):
            with self.subTest(value=value), self.assertRaises(TypeError):
                HubConfig(prepare_provider=value)
