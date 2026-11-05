"""Explicit web adapters. Application supplies service/client, limits and network policy."""

import asyncio
import codecs
from copy import deepcopy
import hashlib

from jsonschema import Draft202012Validator
from llm.errors import CodedError
from llm.services.infrastructure.cancellation import cancel_tasks
from llm.services.infrastructure.storage import drain_on_cancel
from .web_content import WebArguments, WebPage, SearchPage, PageText, validate_url


class WebError(CodedError, RuntimeError):
    def __init__(self, code, message, *, status=None):
        super().__init__(message)
        self.code, self.status_code = code, status


async def _owned_call(operation):
    """The child owns its HTTP cleanup; repeated cancellation cannot abandon it."""
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await cancel_tasks((task,))
        raise


class WebFetch:
    """Borrow an Application-owned httpx.AsyncClient. Never close a shared client.

    Omitted SDK kwargs stay omitted. Client timeout, redirects, authentication,
    proxies and environment behavior belong to that explicitly supplied client.
    revision identifies the Host's execution configuration for durable binding.
    """

    BUFFER_BYTES = 64 * 1024  # Streaming allocation, not a response-size policy.
    schema = WebArguments.FETCH

    def __init__(self, client, *, revision):
        if not isinstance(revision, str) or not revision:
            raise ValueError('Web adapter requires an explicit revision')
        self.client = client
        self.execution_binding = {'adapter': 'httpx-web-fetch', 'revision': revision}

    async def __call__(self, arguments):
        Draft202012Validator(self.schema).validate(arguments)
        arguments = deepcopy(arguments)
        validate_url(arguments['url'])
        return await _owned_call(self._fetch(arguments))

    async def _fetch(self, arguments):
        import httpx
        from .calls import current_calls
        response, calls = None, current_calls()
        if calls is not None:
            await calls.acquire()
        try:
            request = self.client.build_request('GET', arguments['url'])
            response = await self.client.send(request, stream=True)
            response.raise_for_status()
            media = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
            if media not in ('text/html', 'application/xhtml+xml', 'text/plain'):
                raise WebError('web_unsupported_content', 'Web text reader requires HTML or plain text')
            parser = PageText(str(response.url), arguments.get('max_chars'))
            decoder = codecs.getincrementaldecoder(response.encoding)(errors='strict')
            digest, size = hashlib.sha256(), 0
            async for block in response.aiter_bytes(chunk_size=self.BUFFER_BYTES):
                # Buffered SDK reads can complete without yielding. Preserve a
                # cancellation opportunity between bounded decoding blocks.
                await asyncio.sleep(0)
                size += len(block)
                if 'max_bytes' in arguments and size > arguments['max_bytes']:
                    raise WebError('web_response_limit', 'Web response exceeds the explicit byte limit')
                digest.update(block)
                text = decoder.decode(block)
                parser.feed(text) if media != 'text/plain' else parser.text(text)
            tail = decoder.decode(b'', final=True)
            parser.feed(tail) if media != 'text/plain' else parser.text(tail)
            parser.close()
            return WebPage(arguments['url'], str(response.url), ''.join(parser.title).strip(),
                           ''.join(parser.parts), parser.links, media, digest.hexdigest(), parser.truncated).to_dict()
        except httpx.HTTPStatusError as error:
            raise WebError('web_http_error', 'Web server rejected the request', status=error.response.status_code) from error
        except httpx.TimeoutException as error:
            raise WebError('web_timeout', 'Web transport timed out') from error
        except httpx.HTTPError as error:
            raise WebError('web_connection_failed', 'Web transport failed') from error
        except (UnicodeError, LookupError) as error:
            raise WebError('web_invalid_response', 'Web text encoding is invalid or unsupported') from error
        finally:
            try:
                if response is not None:
                    await drain_on_cancel(response.aclose())
            finally:
                if calls is not None:
                    calls.release()


class WebSearch:
    """Normalize a selected service adapter, never select or scrape a search engine.

    search(arguments, options) is Application-injected and returns SearchPage or
    its closed JSON representation. Service-specific options are opaque here.
    The adapter owns service response decoding and validation of those options.
    """

    schema = WebArguments.SEARCH

    def __init__(self, search, *, revision, options=None):
        if not callable(search) or not isinstance(revision, str) or not revision:
            raise ValueError('Web search requires a callable and an explicit revision')
        self.search, self.options = search, deepcopy(options)
        self.execution_binding = {'adapter': 'web-search', 'revision': revision}

    async def __call__(self, arguments):
        Draft202012Validator(self.schema).validate(arguments)
        async def invoke():
            return await self.search(deepcopy(arguments), deepcopy(self.options))
        value = await _owned_call(invoke())
        try:
            return (value if isinstance(value, SearchPage) else SearchPage.from_dict(value)).to_dict()
        except (ValueError, TypeError) as error:
            raise WebError('web_invalid_response', 'Search adapter returned an invalid result page') from error
