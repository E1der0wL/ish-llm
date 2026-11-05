"""Web contracts with local transports: no search service or public endpoint needed."""

import asyncio
import tempfile
import unittest
import httpx

from llm.components.tools.builtin import BuiltinTools
from llm.providers.web import WebFetch, WebSearch, WebError
from llm.providers.web_content import SearchPage, WebSource


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def test_html_charset_sources_and_explicit_limits_through_builtin(self):
        requests = []
        async def serve(request):
            requests.append(request)
            return httpx.Response(200, headers={'Content-Type': 'text/html; charset=iso-8859-1'},
                content='<title>Caf\xe9</title><script>hidden()</script><p>Evidence</p><a href="/doc">Source</a>'.encode('latin1'))
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve), timeout=0.25) as client:
            adapter = WebFetch(client, revision='fixture')
            with tempfile.TemporaryDirectory() as folder:
                async with BuiltinTools(folder, adapters={'web_fetch': adapter}) as tools:
                    tool, args = tools.registry.prepare('web_fetch', {'url': 'https://fixture.test/page', 'max_chars': 9})
                    value = await tool.handler(args)
                    self.assertEqual(value['title'], 'Caf\xe9')
                    self.assertTrue(value['truncated'])
                    self.assertNotIn('hidden', value['text'])
                    self.assertEqual(value['links'][0]['url'], 'https://fixture.test/doc')
                    self.assertEqual(len(value['text']), 9)
                    self.assertEqual(tools.registry.contracts()['web_fetch']['execution_binding'], adapter.execution_binding)
            self.assertEqual(requests[0].extensions['timeout']['read'], 0.25)
            self.assertFalse(client.is_closed)

    async def test_search_preserves_service_options_and_cursor_without_injecting_limits(self):
        requests = []
        async def service(arguments, options):
            requests.append((arguments.copy(), options.copy()))
            arguments.clear(); options.clear()
            return SearchPage([WebSource('https://fixture.test/doc', 'Title', 'Evidence')], 'page-2')
        adapter = WebSearch(service, revision='configured-service', options={'private_option': [7]})
        original = {'query': 'topic'}
        self.assertEqual((await adapter(original))['next_cursor'], 'page-2')
        await adapter({'query': 'topic', 'cursor': 'page-2', 'limit': 2})
        self.assertEqual(requests[0], ({'query': 'topic'}, {'private_option': [7]}))
        self.assertEqual(original, {'query': 'topic'})
        self.assertEqual(adapter.options, {'private_option': [7]})
        self.assertEqual(requests[1][0]['cursor'], 'page-2')

    async def test_failures_close_response_and_do_not_retry(self):
        for status, content_type, body, arguments, code in (
            (503, 'text/plain', b'down', {}, 'web_http_error'),
            (200, 'application/pdf', b'pdf', {}, 'web_unsupported_content'),
            (200, 'text/plain', b'oversized', {'max_bytes': 2}, 'web_response_limit'),
            (200, 'text/plain; charset=utf-8', b'\xff', {}, 'web_invalid_response'),
        ):
            with self.subTest(code=code):
                requests = []
                async def serve(request):
                    requests.append(request)
                    return httpx.Response(status, headers={'content-type': content_type}, content=body)
                async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
                    with self.assertRaises(WebError) as caught:
                        await WebFetch(client, revision='test')({'url': 'https://fixture.test', **arguments})
                    self.assertEqual(caught.exception.code, code)
                    self.assertEqual(len(requests), 1)

    async def test_search_awaitable_callback_runs_inside_owned_request(self):
        caller = asyncio.current_task()
        def search(arguments, options):
            self.assertIsNot(asyncio.current_task(), caller)
            future = asyncio.get_running_loop().create_future()
            future.set_result({'results': [], 'next_cursor': None})
            return future
        self.assertEqual(await WebSearch(search, revision='fixture')({'query': 'topic'}),
                         {'results': [], 'next_cursor': None})

    async def test_repeated_cancel_keeps_admission_until_response_closes(self):
        from llm.providers.calls import ProviderCalls, ProviderLimits
        entered, closing, release, closed = (asyncio.Event() for _ in range(4))
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
                await asyncio.Event().wait()
                yield b'never'
            async def aclose(self):
                closing.set()
                await release.wait()
                closed.set()
        async def serve(request):
            return httpx.Response(200, headers={'content-type': 'text/plain'}, stream=Stream())
        calls = ProviderCalls(ProviderLimits(max_active=1))
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            with calls.scope():
                task = asyncio.create_task(WebFetch(client, revision='test')({'url': 'https://fixture.test'}))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                task.cancel()
                await asyncio.wait_for(closing.wait(), 5)
                task.cancel()
                await asyncio.sleep(0)
                self.assertEqual(calls.stats['active'], 1)
                self.assertFalse(task.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
                self.assertTrue(closed.is_set())
                self.assertEqual(calls.stats['active'], 0)
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

    async def test_actual_local_http_and_no_redirect_policy_added(self):
        async def serve(reader, writer):
            try:
                await reader.readuntil(b'\r\n\r\n')
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello')
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(serve, '127.0.0.1', 0)
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                url = f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/'
                result = await WebFetch(client, revision='fixture')({'url': url, 'max_chars': 0})
                self.assertEqual(result['text'], '')
                self.assertTrue(result['truncated'])
                self.assertFalse(client.follow_redirects)
        finally:
            server.close()
            await server.wait_closed()

    async def test_buffered_chunks_allow_cancellation_before_full_decode(self):
        entered, closed = asyncio.Event(), asyncio.Event()
        consumed = []
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                for number in range(100):
                    consumed.append(number)
                    entered.set()
                    yield b'x' * WebFetch.BUFFER_BYTES
            async def aclose(self):
                closed.set()
        async def serve(request):
            return httpx.Response(200, headers={'content-type': 'text/plain'}, stream=Stream())
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            task = asyncio.create_task(WebFetch(client, revision='fixture')({'url': 'https://fixture.test'}))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(closed.is_set())
                self.assertLess(len(consumed), 100)
            finally:
                await asyncio.gather(task, return_exceptions=True)
