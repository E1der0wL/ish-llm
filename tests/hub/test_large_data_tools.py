"""Hub binds backend-owned captures, range readers and independent path grants."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import shlex
import tempfile
import unittest

from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.defaults import HubDefaults
from hub.config.environment import ENVIRONMENT_OPTION
from hub.backend.project.tool_data_setup import ToolDataSetup
from hub.backend.tools.builtin import HubBuiltinTools
from llm.core.models import ProjectConfig
from llm.engines.loop import LoopEngine
from llm.providers.litellm import LiteLLMCompletionAdapter
from tests.hub.test_shell_environment import snapshot
from tests.llm.test_loop import ScriptedCompletion, chunk, call


class LargeDataToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_creation_preserves_explicit_grants_destinations_and_component_selection(self):
        with tempfile.TemporaryDirectory() as root:
            builtins = HubBuiltinTools(root)
            try:
                config = ToolDataSetup.creation_defaults(ProjectConfig(), ['builtin_tools', 'data'], builtins)
                values = config.parameter_targets.component('builtin_tools').to_dict()
                builtins.validate_config(values)
                self.assertEqual(values['config']['output'], ToolDataSetup.destination())
                self.assertEqual(config.policies['tools']['argument_constraints'], HubDefaults.TOOL_PAGE_CONSTRAINTS)
                explicit = deepcopy(values)
                explicit['config']['output'] = {'component': 'data', 'identifier': 'chosen'}
                explicit['policy'].update(output_preview_chars=0, read_access={'roots': [], 'follow_symlinks': False})
                config = ProjectConfig()
                config.set_component_parameters('builtin_tools', explicit)
                config.policies = {'tools': {'argument_constraints': {}}}
                result = ToolDataSetup.creation_defaults(config, ['builtin_tools', 'data'], builtins)
                self.assertEqual(result.parameter_targets.component('builtin_tools').to_dict(), explicit)
                self.assertEqual(result.policies['tools']['argument_constraints'], {})
                limited = ToolDataSetup.creation_defaults(ProjectConfig(), ['builtin_tools'], builtins)
                self.assertNotIn('output', limited.parameter_targets.component('builtin_tools').config)
                # A supplied but malformed grant is never broadened into the host default.
                invalid = ProjectConfig()
                invalid.set_component_parameters('builtin_tools', {'policy': {'read_access': {}}})
                invalid = ToolDataSetup.creation_defaults(invalid, ['builtin_tools', 'data'], builtins)
                self.assertEqual(invalid.parameter_targets.component('builtin_tools').policy['read_access'], {})
                with self.assertRaises(ValueError):
                    builtins.validate_config(invalid.parameter_targets.component('builtin_tools').to_dict())
            finally:
                await builtins.close()

    async def test_300000_lines_stay_on_disk_and_survive_approval_and_reopen(self):
        requests, observations = [], []
        script = ('from pathlib import Path\n'
                  'Path("executions.txt").open("a").write("once\\n")\n'
                  'for i in range(300000): print(f"{i:06d} 처리 결과 abcdefghijklmnopqrstuvwxyz")\n'
                  'print("FINAL-SENTINEL")\n')
        command = 'python3 -c ' + shlex.quote(script)

        def provider(**request):
            requests.append(request)
            results = [json.loads(message['content']) for message in request['messages']
                       if message['role'] == 'tool']
            if not results:
                yield chunk(calls=[call(json.dumps({'command': command}), name='shell_execute')], finish='tool_calls')
            elif 'output_sources' in results[-1]:
                result = results[-1]
                observations.append(result)
                yield chunk(calls=[call(json.dumps({'process_id': result['process_id'],
                    'stdout_offset': result['stdout_chars'] - 100}), name='process_output', call_id='page')], finish='tool_calls')
            else:
                observations.append(results[-1])
                yield chunk('Read the retained final range; the preview is not full-data verification.', finish='stop')

        with tempfile.TemporaryDirectory() as root:
            work = Path(root) / 'work'
            work.mkdir()
            config = HubConfig(Path(root) / 'storage', file_root=work, auto_title=False,
                agent_definition={'engine': 'loop', 'tools': ['shell_execute', 'process_output']},
                model='test/model', engine_factories={'loop': lambda: LoopEngine(
                    completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))})
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                project_id = runtime.project.id
                session = await runtime.project.sessions.acreate(agent_ids=['assistant'])
                pending = await session.run.submit('Inspect a large command output.', recipients=['assistant'],
                    request_context={ENVIRONMENT_OPTION: snapshot(work, 'large').to_dict()})
                run, = await asyncio.wait_for(pending.wait(), 45)
                self.assertEqual(run.data.status, 'paused', run.data.error)
                self.assertFalse((work / 'executions.txt').exists())
                approval, = await run.ainteractions(pending_only=True)
                await run.arespond(approval.respond('approve'))
                pending = await session.run.resume(run.id, agent_id='assistant')
                run, = await asyncio.wait_for(pending.wait(), 90)
                self.assertEqual(run.data.status, 'completed', run.data.error)
                self.assertEqual((work / 'executions.txt').read_text(), 'once\n')
                self.assertEqual(len(requests), 3)
                result, page = observations
                self.assertLessEqual(len(result['stdout']), HubDefaults.TOOL_OUTPUT_PREVIEW_CHARS)
                self.assertGreater(result['stdout_chars'], 10_000_000)
                self.assertIn('FINAL-SENTINEL', page['stdout'])
                self.assertLessEqual(len(page['stdout']), 8000)
                self.assertFalse(result['truncated'])
                for request in requests:
                    for message in request['messages']:
                        if message['role'] == 'tool':
                            self.assertLess(len(message['content']), 12000)
                process_id = result['process_id']
            finally:
                await runtime.close()
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                self.assertEqual(runtime.project.id, project_id)
                record = await runtime.project.aget_data()
                runtime.builtin_tools.resolve_runtime(record, 'tools', data_factory=runtime.project.components.__getitem__)
                # A changed shell environment creates another Toolkit. Retained
                # output is still owned by the Project, not by the old process.
                registry = runtime.builtin_tools.resolve_environment(
                    record, snapshot(work, 'reopened'))
                page = await registry.get('process_output').handler({
                    'process_id': process_id, 'stdout_offset': result['stdout_chars'] - 100, 'max_chars': 100})
                self.assertIn('FINAL-SENTINEL', page['stdout'])
                self.assertEqual((work / 'executions.txt').read_text(), 'once\n')
            finally:
                await runtime.close()

    async def test_external_symlink_read_is_paged_without_widening_writes_or_cwd(self):
        with tempfile.TemporaryDirectory() as root:
            work, external = Path(root) / 'work', Path(root) / 'external'
            work.mkdir(); external.mkdir()
            content = '가나다' * 100000
            (external / 'large.txt').write_text(content)
            (work / 'linked.txt').symlink_to(external / 'large.txt')
            provider = ScriptedCompletion(
                [chunk(calls=[call(json.dumps({'path': str(work / 'linked.txt')}), name='file_read')], finish='tool_calls')],
                [chunk('Read a page.', finish='stop')])
            runtime = HubRuntime(HubConfig(Path(root) / 'storage', file_root=work, auto_title=False,
                agent_definition={'engine': 'loop', 'tools': ['file_read']}, model='test/model',
                engine_factories={'loop': lambda: LoopEngine(
                    completion_adapter=LiteLLMCompletionAdapter(completion_fn=provider))}))
            try:
                await runtime.start()
                session = await runtime.project.sessions.acreate(agent_ids=['assistant'])
                run, = await (await session.run.submit('Read the linked file.', recipients=['assistant'],
                    request_context={ENVIRONMENT_OPTION: snapshot(work, 'read').to_dict()})).wait(timeout=30)
                self.assertEqual(run.data.status, 'completed', run.data.error)
                result = json.loads(next(m['content'] for m in provider.requests[-1]['messages'] if m['role'] == 'tool'))
                self.assertEqual(result['text'], content[:8000])
                self.assertEqual(result['next_offset'], 8000)
                registry = runtime.builtin_tools.resolve_environment(await runtime.project.aget_data(), snapshot(work, 'read'))
                page = await registry.get('file_read').handler({'path': str(external / 'large.txt'),
                    'offset': result['next_offset'], 'max_chars': 10, 'expected_sha256': result['sha256']})
                self.assertEqual(page['text'], content[8000:8010])
                with self.assertRaises(ValueError):
                    await registry.get('file_create').handler({'path': str(external / 'blocked'), 'content': 'no'})
                with self.assertRaises(ValueError):
                    await registry.get('shell_execute').handler({'command': 'pwd', 'cwd': str(external)})
                with self.assertRaises(ValueError):
                    await registry.get('file_read').handler({'path': '/dev/null', 'max_chars': 10})
                parameters = (await runtime.project.aget_data()).config.parameter_targets.component('builtin_tools').to_dict()
                parameters['policy']['read_access'] = {'roots': [str(work)], 'follow_symlinks': True}
                await runtime.project.components.builtin_tools.aconfigure(parameters)
                restricted = runtime.builtin_tools.resolve_environment(await runtime.project.aget_data(), snapshot(work, 'read'))
                self.assertNotEqual(registry.contracts(), restricted.contracts())
                with self.assertRaises(ValueError):
                    await restricted.get('file_read').handler({'path': 'linked.txt', 'max_chars': 10})
            finally:
                await runtime.close()

    async def test_existing_settings_are_preserved_and_explicit_save_prepares_output(self):
        with tempfile.TemporaryDirectory() as root:
            config = HubConfig(root, auto_title=False,
                project_config={'policies': {'tools': {'argument_constraints': {}}}})
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                project_id = runtime.project.id
                parameters = (await runtime.project.aget_data()).config.parameter_targets.component('builtin_tools').to_dict()
                parameters['config'].pop('output')
                parameters['policy'].pop('output_preview_chars')
                parameters['policy']['read_access'] = {'roots': [], 'follow_symlinks': False}
                await runtime.project.components.builtin_tools.aconfigure(parameters)
                await runtime.project.components.data.adelete(HubDefaults.TOOL_OUTPUT_SOURCE)
                saved = (await runtime.project.aget_data()).config.to_dict()
            finally:
                await runtime.close()
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                self.assertEqual((await runtime.project.aget_data()).config.to_dict(), saved)
                self.assertNotIn(HubDefaults.TOOL_OUTPUT_SOURCE, await runtime.project.components.data.alist())
                loaded = await runtime.settings_service.load_project(project_id)
                values = loaded['values']
                parameters = values['config']['parameters']['components']['builtin_tools']
                parameters['config']['output'] = ToolDataSetup.destination()
                parameters['policy']['output_preview_chars'] = 100
                await runtime.settings_service.save_project(project_id, values, loaded['config_version'])
                self.assertIn(HubDefaults.TOOL_OUTPUT_SOURCE, await runtime.project.components.data.alist())
                record = await runtime.project.aget_data()
                runtime.builtin_tools.resolve_runtime(record, 'tools', data_factory=runtime.project.components.__getitem__)
                registry = runtime.builtin_tools.resolve_environment(record, snapshot(Path(root), 'saved'))
                output = await registry.get('shell_execute').handler({'command': 'printf saved'})
                self.assertIn('output_sources', output)
                with self.assertRaises(ValueError):
                    await registry.get('file_read').handler({'path': str(Path(root) / '.hub')})
                self.assertEqual((await runtime.project.aget_data()).config.policies['tools']['argument_constraints'], {})
            finally:
                await runtime.close()
