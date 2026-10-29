"""Bound native reader results before Memory, without changing stored evidence.

Only detached model bodies are projected. No files are opened, Tools executed,
permissions widened or receipts/checkpoints rewritten by this processor.
"""

import asyncio
import json

from llm.components.processing import CompletionSession
from llm.core.model_protocol import ModelRole
from llm.components.memory.configuration import MemoryProcessingConfig
from llm.components.tools.builtin.file_values import FileReadArguments
from llm.components.tools.builtin.process_values import ProcessOutputArguments, ProcessStatus

from ...config.defaults import HubDefaults
from .reader_calls import ReaderCalls
from .tool_references import ToolReferences


class NativeToolViews:
    PROCESS_TOOLS = frozenset(('shell_execute', 'process_start', 'process_status', 'process_output'))
    PARTIAL = ('This is a partial source page, not a summary. Use next_read for more evidence; '
               'do not rerun the command. Output source paths are Data references, not working-directory files.')

    @staticmethod
    def encode(value):
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))

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
        if not (isinstance(value.get('text'), str) and type(value.get('offset')) is int
                and isinstance(value.get('path'), str) and isinstance(value.get('sha256'), str)):
            return None
        if not {'next_offset', 'truncated'} <= value.keys():
            return None
        body, offset = value['text'], value['offset']
        def render(count):
            more = count < len(body) or value['next_offset'] is not None
            result = {key: value[key] for key in ('path', 'sha256', 'offset')}
            result.update(text=body[:count], next_offset=offset + count if more else None,
                          truncated=offset > 0 or more)
            if more:
                args = {FileReadArguments.path.__name__: value['path'],
                        FileReadArguments.expected_sha256.__name__: value['sha256'],
                        FileReadArguments.offset.__name__: offset + count,
                        FileReadArguments.max_chars.__name__: HubDefaults.TOOL_PAGE_CONSTRAINTS['file_read']['max_chars']['maximum']}
                if 'max_file_bytes' in arguments:
                    args['max_file_bytes'] = arguments['max_file_bytes']
                hint = ReaderCalls.create(context, request, FileReadArguments.NAME, args, limit_field='max_chars')
                if hint is None:
                    return None
                result.update(next_read=hint, notice=cls.PARTIAL)
            return cls.encode(result)
        return cls.bounded(render, len(body), limit)

    @classmethod
    def process(cls, context, request, value, arguments, limit):
        if not (all(isinstance(value.get(key), str) for key in ('process_id', 'status', 'stdout', 'stderr'))
                and {'returncode', 'truncated'} <= value.keys()):
            return None
        offsets, totals = {}, {}
        for name in ('stdout', 'stderr'):
            if (type(value.get('next_' + name + '_offset')) is int
                  and type(value.get(name + '_remaining')) is int):
                offsets[name] = value['next_' + name + '_offset'] - len(value[name])
                totals[name] = value['next_' + name + '_offset'] + value[name + '_remaining']
            elif type(value.get(name + '_chars')) is int:
                offsets[name], totals[name] = 0, value[name + '_chars']
            else:
                return None  # Non-retained output must not acquire an invented cursor.
        size = max(len(value['stdout']), len(value['stderr']))
        def render(count):
            result = {key: value[key] for key in ('process_id', 'status', 'returncode', 'truncated')}
            if 'cwd' in value:
                result['cwd'] = value['cwd']
            more = value['status'] == ProcessStatus.RUNNING
            args = {'process_id': value['process_id'], 'max_chars': HubDefaults.TOOL_PAGE_CONSTRAINTS['process_output']['max_chars']['maximum']}
            for name in ('stdout', 'stderr'):
                result[name] = value[name][:count]
                end = offsets[name] + len(result[name])
                result['next_' + name + '_offset'] = args[name + '_offset'] = end
                result[name + '_remaining'] = totals[name] - end
                result[name + '_chars'] = totals[name]
                more |= end < totals[name]
            if more:
                hint = ReaderCalls.create(context, request, ProcessOutputArguments.NAME, args, limit_field='max_chars')
                if hint is None:
                    return None
                result.update(next_read=hint, notice=cls.PARTIAL)
                if 'output_sources' in value:
                    result['output_sources'] = value['output_sources']
            return cls.encode(result)
        return cls.bounded(render, size, limit)

    @classmethod
    def prepare(cls, context, request):
        limit = ToolReferences.processing_policy(context).get(
            MemoryProcessingConfig.Field.TOOL_RESULT_CHARS,
            HubDefaults.MEMORY_SUMMARY['policy']['processing']['tool_result_chars'])
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
            if not isinstance(value, dict) or 'error' in value:
                continue
            render = cls.file if call.name == FileReadArguments.NAME else cls.process
            text = render(context, request, value, call.arguments, limit)
            if text is not None:
                request.messages[index] = item.with_text(text)


class ToolViewSession(CompletionSession):
    def __init__(self, context):
        self.context = context

    async def prepare(self, request):
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
