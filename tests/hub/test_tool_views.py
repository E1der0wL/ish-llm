"""Native result pages remain useful and their read hints actually execute."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest

from llm.components.processing import CompletionInput, CompletionMessage, CompletionRequest
from llm.components.tools.builtin import BuiltinTools
from llm.core.model_protocol import ModelMessage, ModelRole, ToolCall, ToolResult
from llm.core.models import ProjectConfig
from llm.engines.base import EngineContext
from hub.backend.execution.tool_views import NativeToolViews, ToolViewSession, ToolViewProcessor
from hub.backend.execution.tool_references import ToolReferences
from hub.backend.execution.working_context import WorkingContext
from hub.config.defaults import HubDefaults


class ToolViewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.builtin = BuiltinTools(self.directory.name, allow_commands=True, shell=['/bin/sh', '-c'])
        self.addAsyncCleanup(self.builtin.close)
        self.context = EngineContext(NS(components=('memory',), config=ProjectConfig()), NS(id='s'),
            NS(id='r', input_message_id='u'), (), tools=self.builtin.registry,
            tool_scope=NS(argument_constraints=deepcopy(HubDefaults.TOOL_PAGE_CONSTRAINTS)))

    def request(self, name, value, arguments):
        return CompletionRequest({}, [
            CompletionMessage(ModelMessage(ModelRole.USER, 'Inspect this source'), 'u'),
            CompletionMessage(ModelMessage(ModelRole.ASSISTANT, calls=(ToolCall('c', name, arguments),))),
            CompletionMessage(ModelMessage(ModelRole.TOOL, result=ToolResult('c', json.dumps(value, ensure_ascii=False))))
        ], 1, tools=self.context.tools.definitions(constraints=self.context.tool_scope.argument_constraints))

    async def project(self, request):
        original, invocation = deepcopy(request.messages), request.to_model()
        _ = [e async for e in ToolViewSession(self.context).prepare(request)]
        CompletionInput.validate(request, original, invocation, 'u')
        self.assertEqual(request.tools, tuple(invocation.tools))
        return json.loads(request.messages[-1].message.text)

    async def test_8475_character_envelope_is_a_body_page_with_valid_native_read(self):
        path = Path(self.directory.name) / 'file.txt'
        content = ('가나다\n"quoted"\\text ' * 600)
        path.write_text(content)
        result = await self.builtin.registry.get('file_read').handler({'path': str(path), 'max_chars': 8000})
        # Include the native JSON-envelope overhead which formerly triggered a reference.
        request = self.request('file_read', result, {'path': str(path)})
        self.assertGreater(len(request.messages[-1].message.text), 8475)
        original = deepcopy(result)
        view = await self.project(request)
        self.assertLessEqual(len(request.messages[-1].message.text), 8000)
        self.assertNotIn(ToolReferences.NOTICE, request.messages[-1].message.text)
        self.assertTrue(view['text'])
        self.assertEqual(view['text'], content[:view['next_offset']])
        read = view['next_read']
        tool, args = self.context.tools.prepare(read['tool'], read['arguments'],
            constraints=self.context.tool_scope.argument_constraints)
        self.assertNotIn('start_line', args)
        page = await tool.handler(args)
        self.assertTrue(content.startswith(view['text'] + page['text']))
        self.assertEqual(result, original)

    async def test_small_file_stays_complete_and_oversized_single_line_does_not(self):
        for content in ('short file\n', 'x' * 300000):
            path = Path(self.directory.name) / 'one-line.txt'
            path.write_text(content)
            result = await self.builtin.registry.get('file_read').handler({'path': str(path), 'max_chars': 8000})
            request = self.request('file_read', result, {'path': str(path)})
            view = await self.project(request)
            if len(content) < 8000:
                self.assertEqual(view['text'], content)
                self.assertNotIn('next_read', view)
            else:
                self.assertGreater(len(view['text']), 0)
                self.assertLess(len(view['text']), 8000)
                self.assertEqual(view['next_read']['arguments']['offset'], len(view['text']))

    async def test_process_stream_offsets_preserve_both_streams_and_capture_loss(self):
        value = dict(process_id='a' * 32, status='completed', returncode=7, truncated=True,
            stdout='o' * 8000, stderr='e' * 8000,
            next_stdout_offset=8100, next_stderr_offset=8200,
            stdout_remaining=20, stderr_remaining=30)
        request = self.request('process_output', value, {'process_id': value['process_id']})
        view = await self.project(request)
        self.assertEqual(view['next_read']['tool'], 'process_output')
        for name, start, total in (('stdout', 100, 8120), ('stderr', 200, 8230)):
            end = start + len(view[name])
            self.assertEqual(view['next_read']['arguments'][name + '_offset'], end)
            self.assertEqual(view[name + '_remaining'], total - end)
        self.assertEqual(view['returncode'], 7)
        self.assertTrue(view['truncated'])
        self.assertLessEqual(len(request.messages[-1].message.text), 8000)
        # Re-projecting a model view must not reset its nonzero stream offsets.
        self.assertEqual(await self.project(request), view)

    async def test_small_command_returns_content_without_storage_metadata(self):
        value = dict(process_id='a' * 32, status='completed', returncode=0, truncated=False,
                     stdout='hello', stderr='', stdout_chars=5, stderr_chars=0,
                     output_sources={'stdout': {'path': 'process-output/a/stdout.txt'}})
        view = await self.project(self.request('shell_execute', value, {'command': 'echo hello'}))
        self.assertEqual(view['stdout'], 'hello')
        self.assertNotIn('output_sources', view)
        self.assertNotIn('next_read', view)

    async def test_missing_reader_and_conflicting_constraints_do_not_invent_access(self):
        value = dict(path='source', sha256='a' * 64, text='x' * 8000, offset=0, next_offset=8000, truncated=True)
        request = self.request('file_read', value, {'path': 'source'})
        request.tools = ()
        original = deepcopy(request)
        await self.project(request)
        self.assertEqual(request, original)
        self.context.tool_scope.argument_constraints['file_read']['offset'] = {'mode': 'fixed', 'value': 0}
        request = self.request('file_read', value, {'path': 'source'})
        original = deepcopy(request)
        await self.project(request)
        self.assertEqual(request, original)

    async def test_explicit_page_and_memory_limits_remain_authoritative(self):
        self.context.project.config.set_component_parameters('memory', {'policy': {'processing': {'tool_result_chars': 2000}}})
        self.context.tool_scope.argument_constraints['file_read']['max_chars']['maximum'] = 1500
        value = dict(path='source', sha256='a' * 64, text='x' * 1500, offset=0, next_offset=1500, truncated=True)
        request = self.request('file_read', value, {'path': 'source'})
        view = await self.project(request)
        self.assertLessEqual(len(request.messages[-1].message.text), 2000)
        self.assertEqual(view['next_read']['arguments']['max_chars'], 1500)
        self.assertEqual(view['next_offset'], len(view['text']))

    async def test_fractional_page_bounds_generate_an_executable_integer_hint(self):
        self.context.tool_scope.argument_constraints['file_read']['max_chars'] = {
            'mode': 'bounded', 'minimum': 1.25, 'maximum': 1500.75}
        value = dict(path='source', sha256='a' * 64, text='x' * 1500,
            offset=0, next_offset=1500, truncated=True)
        view = await self.project(self.request('file_read', value, {'path': 'source'}))
        read = view['next_read']
        _, actual = self.context.tools.prepare(read['tool'], read['arguments'],
            constraints=self.context.tool_scope.argument_constraints)
        self.assertEqual(actual['max_chars'], 1500)
        self.assertIs(type(actual['max_chars']), int)

    def test_processor_precedes_even_an_explicit_negative_memory_priority(self):
        self.assertLess(ToolViewProcessor((NS(priority=-2000), NS(priority=1000))).priority, -2000)


class ToolViewIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_sees_bounded_pages_and_model_can_follow_them_without_references(self):
        from hub.backend.runtime import HubConfig, HubRuntime
        from llm.components.memory import MemoryComponent
        from llm.engines.loop import LoopEngine
        from llm.providers.litellm import LiteLLMCompletionAdapter
        from tests.llm.test_loop import chunk, call
        from hub.config.environment import ENVIRONMENT_OPTION
        from tests.hub.test_shell_environment import snapshot

        content = '가나다\n' * 2118 + 'end'  # 8,475 characters, larger than one native page.
        pages, summary_calls, requests = [], [], []
        def summarize(**request):
            summary_calls.append(request)
            raise AssertionError('A just-read bounded native page must not be summarized')
        def model(**request):
            requests.append(request)
            results = [m['content'] for m in request['messages'] if m['role'] == 'tool']
            if not results:
                yield chunk(calls=[call('{"path":"source.txt","max_chars":8000}', name='file_read')], finish='tool_calls')
                return
            self.assertLessEqual(len(results[-1]), 8000)
            value = json.loads(results[-1])
            pages.append(value['text'])
            if value.get('next_read'):
                hint = value['next_read']
                yield chunk(calls=[call(json.dumps(hint['arguments']), name=hint['tool'], call_id=f'p{len(pages)}')], finish='tool_calls')
            else:
                yield chunk('All source text read.', finish='stop')
        with tempfile.TemporaryDirectory() as root:
            work = Path(root) / 'work'
            work.mkdir()
            (work / 'source.txt').write_text(content)
            runtime = HubRuntime(HubConfig(Path(root) / 'storage', file_root=work, model='test/main',
                auto_title=False, postprocess=False,
                agent_definition={'engine': 'loop', 'tools': ['file_read', 'memory_tool_result']},
                component_factories=(lambda: MemoryComponent(completion_adapter=LiteLLMCompletionAdapter(completion_fn=summarize)),),
                project_config={'parameters': {'components': {'memory': {'config': {'processing': {
                    'completion': {'model': 'test/summary'}}}}}}},
                engine_factories={'loop': lambda: LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))}))
            self.addAsyncCleanup(runtime.close)
            await runtime.start()
            session = await runtime.project.sessions.acreate(agent_ids=['assistant'])
            pending = await session.run.submit('Read the file.', recipients=['assistant'],
                request_context={ENVIRONMENT_OPTION: snapshot(work, 'pages').to_dict()})
            run, = await pending.wait(timeout=45)
            self.assertEqual(run.data.status, 'completed', run.data.error)
            self.assertEqual(''.join(pages), content)
            self.assertFalse(summary_calls)
            self.assertNotIn(ToolReferences.NOTICE, str(requests))
