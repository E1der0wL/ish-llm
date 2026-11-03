"""Native SDK transport cancellation, cleanup ownership and admission accounting."""

import asyncio
from contextvars import ContextVar
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from llm.providers.calls import ProviderCalls, ProviderLimits
from llm.providers.litellm import stream_completion, StreamError
from llm.providers.runtime import configure_logging, litellm_sdk


class AsyncStream:
    def __init__(self):
        self.reading = asyncio.Event()
        self.closing = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False
        self.context = ContextVar('stream_owner')
        self.token = self.context.set('request')
        self.owner = asyncio.current_task()

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.reading.set()
        await asyncio.Event().wait()

    async def aclose(self):
        assert asyncio.current_task() is self.owner
        self.closing.set()
        await self.release.wait()
        self.context.reset(self.token)
        self.closed = True


class AsyncProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        configure_logging(folder.name)

    async def test_cancel_pending_sdk_request_releases_admission(self):
        started, stopped = asyncio.Event(), asyncio.Event()
        async def call(**kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        sdk = SimpleNamespace(acompletion=AsyncMock(side_effect=call), num_retries=None)
        calls = ProviderCalls(ProviderLimits(max_active=1))
        async def consume():
            async for _ in stream_completion({'model': 'fixture', 'stream': True}, calls=calls):
                self.fail('cancelled request produced a chunk')
        with patch('llm.providers.runtime.litellm_sdk', return_value=sdk):
            task = asyncio.create_task(consume())
            await asyncio.wait_for(started.wait(), 5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        self.assertTrue(stopped.is_set())
        self.assertEqual(calls.stats, {'active': 0, 'waiting': 0})
        self.assertEqual(sdk.acompletion.await_count, 1)
        self.assertNotIn('timeout', sdk.acompletion.call_args.kwargs)

    async def test_repeated_cancel_waits_for_same_task_cleanup_and_retains_slot(self):
        ready = asyncio.Event()
        streams = []
        async def call(**kwargs):
            stream = AsyncStream()
            streams.append(stream)
            ready.set()
            return stream
        sdk = SimpleNamespace(acompletion=AsyncMock(side_effect=call), num_retries=None)
        calls = ProviderCalls(ProviderLimits(max_active=1))
        async def consume():
            async for _ in stream_completion({'model': 'fixture'}, calls=calls):
                self.fail('unexpected chunk')
        with patch('llm.providers.runtime.litellm_sdk', return_value=sdk):
            task = asyncio.create_task(consume())
            await asyncio.wait_for(ready.wait(), 5)
            stream = streams[0]
            self.addCleanup(stream.release.set)
            await asyncio.wait_for(stream.reading.wait(), 5)
            task.cancel()
            await asyncio.wait_for(stream.closing.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.assertEqual(calls.stats['active'], 1)
            stream.release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        self.assertTrue(stream.closed)
        self.assertEqual(calls.stats['active'], 0)

    async def test_explicit_stream_close_and_close_failure_are_not_silenced(self):
        class Stream:
            def __aiter__(self):
                return self
            async def __anext__(self):
                raise StopAsyncIteration
            async def aclose(self):
                raise ConnectionError('fixture close error')
        sdk = SimpleNamespace(acompletion=AsyncMock(return_value=Stream()), num_retries=None)
        calls = ProviderCalls(ProviderLimits(max_active=1))
        with patch('llm.providers.runtime.litellm_sdk', return_value=sdk):
            with self.assertRaises(StreamError) as caught:
                async for _ in stream_completion({'model': 'fixture'}, calls=calls):
                    pass
        self.assertIsInstance(caught.exception.__cause__, ConnectionError)
        self.assertEqual(caught.exception.diagnostic.details['stage'], 'stream_close')
        self.assertEqual(calls.stats['active'], 0)

    async def test_cancel_during_natural_stream_close_does_not_cancel_cleanup(self):
        streams = []
        ready = asyncio.Event()
        class Stream(AsyncStream):
            async def __anext__(self):
                raise StopAsyncIteration
        async def call(**kwargs):
            stream = Stream()
            streams.append(stream)
            ready.set()
            return stream
        sdk = SimpleNamespace(acompletion=AsyncMock(side_effect=call), num_retries=None)
        calls = ProviderCalls(ProviderLimits(max_active=1))
        async def consume():
            async for _ in stream_completion({'model': 'fixture'}, calls=calls):
                self.fail('unexpected chunk')
        with patch('llm.providers.runtime.litellm_sdk', return_value=sdk):
            task = asyncio.create_task(consume())
            await asyncio.wait_for(ready.wait(), 5)
            stream = streams[0]
            self.addCleanup(stream.release.set)
            await asyncio.wait_for(stream.closing.wait(), 5)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.assertEqual(calls.stats['active'], 1)
            stream.release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        self.assertTrue(stream.closed)
        self.assertEqual(calls.stats['active'], 0)

    async def test_actual_sdk_http_disconnect_before_headers_and_during_stream(self):
        sdk = await asyncio.to_thread(litellm_sdk)
        for send_headers in (False, True):
            with self.subTest(send_headers=send_headers):
                entered, disconnected, received = asyncio.Event(), asyncio.Event(), asyncio.Event()
                connections = set()
                async def serve(reader, writer):
                    connections.add(asyncio.current_task())
                    try:
                        header = await reader.readuntil(b'\r\n\r\n')
                        size = next(int(line.split(b':', 1)[1]) for line in header.split(b'\r\n')
                                    if line.lower().startswith(b'content-length:'))
                        await reader.readexactly(size)
                        if send_headers:
                            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n'
                                         b'Connection: close\r\n\r\n')
                            chunk = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': 1,
                                     'model': 'probe', 'choices': [{'index': 0,
                                     'delta': {'content': 'partial'}, 'finish_reason': None}]}
                            writer.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                            await writer.drain()
                        entered.set()
                        self.assertEqual(await reader.read(), b'')
                        disconnected.set()
                    finally:
                        writer.close()
                        await writer.wait_closed()
                        connections.discard(asyncio.current_task())
                server = await asyncio.start_server(serve, '127.0.0.1', 0)
                parameters = {'model': 'openai/probe', 'api_key': 'fixture-only', 'stream': True,
                              'api_base': f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1',
                              'timeout': 20}
                calls = ProviderCalls(ProviderLimits(max_active=1))
                async def consume():
                    async for _ in stream_completion(parameters, calls=calls):
                        received.set()
                task = None
                try:
                    with patch.object(sdk, 'num_retries', None):
                        task = asyncio.create_task(consume())
                        await asyncio.wait_for(entered.wait(), 20)
                        if send_headers:
                            await asyncio.wait_for(received.wait(), 5)
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(task, 5)
                        await asyncio.wait_for(disconnected.wait(), 5)
                        self.assertEqual(calls.stats['active'], 0)
                finally:
                    if task is not None and not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                    server.close()
                    await server.wait_closed()
                    for connection in tuple(connections):
                        connection.cancel()
                    await asyncio.gather(*connections, return_exceptions=True)
