"""Bound native reader results before Memory, without changing stored evidence.

Only detached model bodies are projected. No files are opened, Tools executed,
permissions widened or receipts/checkpoints rewritten by this processor.
"""

import asyncio
import json
from dataclasses import replace

from llm.components.processing import CompletionSession
from llm.core.model_protocol import ModelRole
from llm.components.tools.builtin.file_values import FileReadArguments
from llm.components.tools.builtin.process_values import (
    ProcessOutputArguments, ProcessStatus, ShellArguments, ProcessStartArguments, ProcessReferenceArguments)
from llm.core.schema import SchemaKeyword

from ...config.defaults import HubDefaults
from .reader_calls import ReaderCalls
from .tool_references import ToolReferences
from .tool_view_values import FilePage, ProcessPreview, PageHint


class NativeToolViews:
    PROCESS_TOOLS = frozenset((ShellArguments.NAME, ProcessStartArguments.NAME,
                              ProcessReferenceArguments.STATUS_NAME, ProcessOutputArguments.NAME))
    JSON_SEPARATORS = (',', ':')
    ERROR_FIELD = 'error'
    PARTIAL = ('This is a partial source page, not a summary. Use next_read for more evidence; '
               'do not rerun the command. Output source paths are Data references, not working-directory files.')

    @classmethod
    def applicable(cls, request):
        calls = {call.id for item in request.messages for call in item.message.calls
                 if call.name in cls.PROCESS_TOOLS or call.name == FileReadArguments.NAME}
        return any(item.message.role == ModelRole.TOOL and item.message.result is not None
                   and item.message.result.call_id in calls for item in request.messages)

    @staticmethod
    def encode(value):
        return json.dumps(value, ensure_ascii=False, separators=NativeToolViews.JSON_SEPARATORS)

    @classmethod
    def bounded(cls, render, size, limit):
        """Keep JSON and continuation metadata intact; include only a bounded body."""
        full = render(size)
        if full is not None and len(full) <= limit:
            return full
        low, high, best = 1, size - 1, None
        while low <= high:
            middle = (low + high) // 2
            text = render(middle)
            if text is None:
                return None  # No authorized, schema-valid way to retrieve omitted data.
            if len(text) <= limit:
                low, best = middle + 1, text
            else:
                high = middle - 1
        return best

    @classmethod
    def file(cls, context, request, value, arguments, limit):
        # Explicit line-based configurations retain their own reader semantics.
        try:
            page = FilePage.read(value)
        except (KeyError, TypeError, ValueError):
            return None
        body, offset = page.text, page.offset
        def render(count):
            more = count < len(body) or page.next_offset is not None
            result = replace(page, text=body[:count], next_offset=offset + count if more else None,
                             truncated=offset > 0 or more).to_dict()
            if more:
                args = {FileReadArguments.path.__name__: page.path,
                        FileReadArguments.expected_sha256.__name__: page.sha256,
                        FileReadArguments.offset.__name__: offset + count,
                        FileReadArguments.max_chars.__name__: HubDefaults.TOOL_PAGE_CONSTRAINTS[
                            FileReadArguments.NAME][FileReadArguments.max_chars.__name__][SchemaKeyword.MAXIMUM]}
                maximum = FileReadArguments.max_file_bytes.__name__
                if maximum in arguments:
                    args[maximum] = arguments[maximum]
                hint = ReaderCalls.create(context, request, FileReadArguments.NAME, args,
                                          limit_field=FileReadArguments.max_chars.__name__)
                if hint is None:
                    return None
                result.update(PageHint(hint, cls.PARTIAL).to_dict())
            return cls.encode(result)
        return cls.bounded(render, len(body), limit)

    @classmethod
    def process(cls, context, request, value, arguments, limit):
        try:
            source = ProcessPreview.read(value)
        except (KeyError, TypeError, ValueError):
            return None
        size = max(len(source.stdout.text), len(source.stderr.text))
        def render(count):
            stdout, stderr = source.stdout.text[:count], source.stderr.text[:count]
            end_out, end_err = source.stdout.offset + len(stdout), source.stderr.offset + len(stderr)
            page = replace(source.page, stdout=stdout, stderr=stderr,
                           next_stdout_offset=end_out, next_stderr_offset=end_err,
                           stdout_remaining=source.stdout.total - end_out,
                           stderr_remaining=source.stderr.total - end_err)
            more = page.status == ProcessStatus.RUNNING or page.stdout_remaining > 0 or page.stderr_remaining > 0
            result = source.to_dict(page, include_sources=more)
            if more:
                args = ProcessOutputArguments(process_id=page.process_id, stdout_offset=end_out, stderr_offset=end_err,
                    max_chars=HubDefaults.TOOL_PAGE_CONSTRAINTS[ProcessOutputArguments.NAME][
                        ProcessOutputArguments.max_chars.__name__][SchemaKeyword.MAXIMUM]).to_dict()
                hint = ReaderCalls.create(context, request, ProcessOutputArguments.NAME, args,
                                          limit_field=ProcessOutputArguments.max_chars.__name__)
                if hint is None:
                    return None
                result.update(PageHint(hint, cls.PARTIAL).to_dict())
            return cls.encode(result)
        return cls.bounded(render, size, limit)

    @classmethod
    def prepare(cls, context, request):
        options = ToolReferences.processing_options(context)
        if not options.compress_tools and not options.has_tool_result_mode:
            return
        limit = options.tool_result_chars
        calls = {call.id: call for item in request.messages for call in item.message.calls}
        for index, item in enumerate(request.messages):
            message = item.message
            if message.role != ModelRole.TOOL or message.result is None:
                continue
            call = calls.get(message.result.call_id)
            if call is None or call.name not in cls.PROCESS_TOOLS | {FileReadArguments.NAME}:
                continue
            try:
                value = json.loads(message.text)
            except (ValueError, TypeError):
                continue
            if not isinstance(value, dict) or cls.ERROR_FIELD in value:
                continue
            render = cls.file if call.name == FileReadArguments.NAME else cls.process
            text = render(context, request, value, call.arguments, limit)
            if text is not None:
                request.messages[index] = item.with_text(text)


class ToolViewSession(CompletionSession):
    def __init__(self, context):
        self.context = context

    async def prepare(self, request):
        if not NativeToolViews.applicable(request):
            return
        # Parsing/encoding a large legacy result must not block the TUI loop.
        # Mutate the detached request only after the worker returns successfully.
        from copy import deepcopy
        def project():
            view = deepcopy(request)
            NativeToolViews.prepare(self.context, view)
            return view.messages
        request.messages = await asyncio.to_thread(project)
        if False:
            yield


class ToolViewProcessor:
    name = 'hub.tool_views'
    close_timeout = None

    def __init__(self, processors):
        self.priority = min((item.priority for item in processors), default=0) - 1

    def session(self, context):
        return ToolViewSession(context)
