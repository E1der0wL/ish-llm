"""Strict compatible servers receive complete Tool-call messages, once."""

import asyncio
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest

from hub.backend.execution.provider_adapter import CompatibleMessages, HubCompletionAdapter
from hub.backend.runtime import HubConfig, HubRuntime
from llm.core.model_protocol import ModelMessage, ModelRequest, ModelRole, ToolResult
from llm.providers.wire_codec import decode_message, encode_request
from llm.providers.litellm import LiteLLMCompletionAdapter
from llm.providers.runtime import ProviderDiagnostics, logging_scope
from tests.llm.test_loop import chunk


def request(*, missing=False, model='openai/local', api_base='https://internal.example/v1'):
    message = {'role': 'assistant', 'tool_calls': [{'id': 'list-call', 'type': 'function',
        'function': {'name': 'file_list', 'arguments': '{"limit":50,"path":".","recursive":false}'}}],
        'reasoning_content': 'observed', 'provider_specific_fields': {'signature': 'keep'}}
    if not missing:
        message['content'] = None
    return ModelRequest([ModelMessage(ModelRole.USER, 'Inspect directory'), decode_message(message),
        ModelMessage(ModelRole.TOOL, result=ToolResult('list-call', '{"entries":[]}', 'file_list'))],
        {'model': model, 'api_base': api_base})


class MessageShapeTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_and_null_are_fixed_only_in_transport_copy(self):
        for missing in (True, False):
            original = request(missing=missing)
            before = original.to_dict()
            captured = []
            def provider(**values):
                captured.append(values)
                yield chunk('done', finish='stop')
            adapter = HubCompletionAdapter(completion_fn=provider)
            async for _ in adapter.stream(original, buffer_size=8):
                pass
            wire = captured[0]['messages']
            self.assertEqual(wire[1]['content'], '')
            expected = encode_request(original)['messages']
            expected[1]['content'] = ''
            self.assertEqual(wire, expected)
            self.assertEqual(original.to_dict(), before)
            shapes = CompatibleMessages.describe(original)
            self.assertNotIn(CompatibleMessages.DIAGNOSTIC, adapter.describe_request(original))
            self.assertEqual(shapes[1]['content_present'], not missing)
            self.assertTrue(shapes[1]['empty_tool_call_content'])
            self.assertEqual(shapes[2]['content_type'], 'text')
            self.assertNotIn('list-call', json.dumps(shapes))
            self.assertNotIn('observed', json.dumps(shapes))

    def test_other_adapters_endpoints_and_nonempty_content_remain_unchanged(self):
        for model, endpoint in (('gemini/example', 'https://other.example'),
                                ('azure/example', 'https://other.example'),
                                ('openai/test', 'https://api.openai.com/v1'),
                                ('openai/test', None), ('test', 'https://other.example')):
            value = request(model=model, api_base=endpoint)
            self.assertIs(CompatibleMessages.transport(value), value)
        value = request()
        for text in ('already present', ''):
            modified = replace(value, messages=[value.messages[0], replace(value.messages[1], content=text), value.messages[2]])
            self.assertEqual(CompatibleMessages.transport(modified), modified)
        overridden = replace(value, parameters={**value.parameters, 'custom_llm_provider': 'gemini'})
        self.assertIs(CompatibleMessages.transport(overridden), overridden)
        explicit = replace(value, parameters={**value.parameters, 'model': 'local', 'custom_llm_provider': 'openai'})
        self.assertEqual(CompatibleMessages.transport(explicit).messages[1].content, '')
        for invalid_provider in ([], {}, 1):
            invalid = replace(value, parameters={**value.parameters, 'custom_llm_provider': invalid_provider})
            self.assertIs(CompatibleMessages.transport(invalid), invalid)

    async def test_missing_tool_result_is_not_filled_or_sent(self):
        value = request()
        invalid = replace(value, messages=value.messages[:-1])
        captured = []
        adapter = HubCompletionAdapter(completion_fn=lambda **kw: captured.append(kw))
        with self.assertRaises(ValueError):
            async for _ in adapter.stream(invalid, buffer_size=8):
                pass
        self.assertEqual(captured, [])

    def test_schema_and_retry_rules_are_native(self):
        adapter = HubCompletionAdapter()
        self.assertEqual(adapter.describe_config(), LiteLLMCompletionAdapter().describe_config())
        class Rejected(Exception):
            status_code = 422
        error = Rejected('messages[12].content: Field required')
        self.assertFalse(adapter.retryable(error))
        self.assertEqual(adapter.classify_error(error), 'provider_invalid_request')

    def test_explicit_diagnostics_include_bounded_shapes_without_payloads(self):
        value = request(missing=True)
        with tempfile.TemporaryDirectory() as directory:
            with logging_scope(Path(directory), diagnostics=ProviderDiagnostics(request_summary=True)):
                details = HubCompletionAdapter().describe_request(value)
        self.assertEqual(details[CompatibleMessages.DIAGNOSTIC], CompatibleMessages.describe(value))
        self.assertNotIn('list-call', json.dumps(details))
        self.assertNotIn('observed', json.dumps(details))
        expanded = replace(value, messages=value.messages * 100)
        self.assertEqual(len(CompatibleMessages.describe(expanded)), CompatibleMessages.LIMIT)


class StrictServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_http_tool_roundtrip_and_repo_map_execute_once(self):
        requests, rejections = [], []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(body)
                for message in body['messages']:
                    if 'content' not in message or message['content'] is None:
                        rejections.append(message['role'])
                        self.send_response(422)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(b'{"error":{"message":"messages[12].content: Field required"}}')
                        return
                results = [m for m in body['messages'] if m['role'] == 'tool']
                if results:
                    delta, finish = {'content': 'Directory analyzed.'}, 'stop'
                else:
                    calls = [{'index': i, 'id': f'call-{i}', 'type': 'function', 'function': {
                        'name': name, 'arguments': json.dumps(arguments)}} for i, (name, arguments) in enumerate((
                            ('file_list', {'path': '.', 'limit': 50, 'recursive': False}),
                            ('repo_map', {'path': '.'}))) ]
                    delta, finish = {'tool_calls': calls}, 'tool_calls'
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                value = {'id': 'strict-local', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'local',
                         'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                self.wfile.write(('data: ' + json.dumps(value) + '\n\ndata: [DONE]\n\n').encode())
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                work = Path(directory) / 'work'
                work.mkdir()
                (work / 'main.py').write_text('def analyze(value):\n    return value\n')
                runtime = HubRuntime(HubConfig(Path(directory) / 'workspace', file_root=work,
                    auto_title=False, postprocess=False,
                    agent_definition={'engine': 'loop', 'tools': ['file_list', 'repo_map'],
                        'completion': {'model': 'openai/local', 'api_base': f'http://127.0.0.1:{server.server_port}/v1',
                                       'api_key': 'local-test'}},
                    project_config={'parameters': {'engines': {'loop': {'policy': {'final_review': None}}}}}))
                try:
                    await runtime.start()
                    session = await runtime.project.sessions.acreate(agent_ids=['assistant'])
                    pending = await session.run.submit('Inspect this directory.', recipients=['assistant'])
                    run, = await asyncio.wait_for(pending.wait(), 60)
                    self.assertEqual(run.data.status, 'completed', run.data.error)
                    self.assertEqual(rejections, [])
                    self.assertEqual(len(requests), 2)
                    results = [m for m in requests[-1]['messages'] if m['role'] == 'tool']
                    self.assertEqual(len(results), 2)
                    self.assertTrue(any('def analyze(value)' in m['content'] for m in results))
                    tool_steps = [step for step in await run.steps.alist() if str(step.kind) == 'tool']
                    self.assertEqual(len(tool_steps), 2)
                finally:
                    await runtime.close()
        finally:
            await asyncio.to_thread(server.shutdown)
            server.server_close()
            thread.join()
