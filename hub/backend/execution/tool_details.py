"""Bounded previews of observed JSON values; never alter execution payloads."""

from itertools import islice
import json

from llm.core.execution_observations import ExecutionObservations
from llm.core.results import EngineOutput
from llm.core.step_tool_metadata import ToolStepMetadata
from llm.core.interactions import InteractionResponse

from ...locales.keys import MessageKey
from ..interactions.question_values import QuestionArguments, QuestionTool


class ValuePreview:
    WHITESPACE = '\n\t'
    SPACE = ' '
    TAB_WIDTH = 4
    KEY_CHARS = 80
    JSON_INDENT = 2
    NEWLINE = '\n'
    CHARACTERS = 1600
    STRING_CHARACTERS = 700
    ITEMS = 6
    DEPTH = 3
    LINES = 12
    ELLIPSIS = "…"

    @classmethod
    def literal(cls, text, limit):
        text = text[:limit]
        return ''.join(c if c.isprintable() or c in ValuePreview.WHITESPACE else ValuePreview.SPACE for c in text).expandtabs(ValuePreview.TAB_WIDTH)

    @classmethod
    def render(cls, value):
        remaining = cls.CHARACTERS

        def bounded(item, depth=0):
            nonlocal remaining
            if remaining <= 0:
                return cls.ELLIPSIS
            remaining -= 1
            if isinstance(item, str):
                size = min(remaining, cls.STRING_CHARACTERS)
                remaining -= min(size, len(item))
                return cls.literal(item, size) + (cls.ELLIPSIS if len(item) > size else '')
            if item is None or type(item) in (bool, int, float):
                return item
            if depth >= cls.DEPTH:
                return cls.ELLIPSIS
            if isinstance(item, dict):
                result = {cls.literal(key, ValuePreview.KEY_CHARS): bounded(child, depth + 1)
                          for key, child in islice(item.items(), cls.ITEMS) if isinstance(key, str)}
                if len(item) > cls.ITEMS:
                    result[cls.ELLIPSIS] = cls.ELLIPSIS
                return result
            if isinstance(item, (list, tuple)):
                result = [bounded(child, depth + 1) for child in islice(item, cls.ITEMS)]
                return result + ([cls.ELLIPSIS] if len(item) > cls.ITEMS else [])
            return cls.ELLIPSIS

        preview = bounded(value)
        text = preview if isinstance(preview, str) else json.dumps(preview, ensure_ascii=False, indent=ValuePreview.JSON_INDENT)
        lines = text[:cls.CHARACTERS].splitlines()
        return ValuePreview.NEWLINE.join(lines[:cls.LINES]) + (
            cls.ELLIPSIS if len(text) > cls.CHARACTERS or len(lines) > cls.LINES else '')


class TextPreview:
    """Select a few readable values for display, without serializing full objects."""
    SPACE = ' '
    KEY_CHARS = 80
    KEY_SEPARATOR = ': '

    INPUT_KEYS = ('command', 'argv', 'path', 'file_path', 'query', 'pattern', 'url', 'text', 'content')
    OUTPUT_KEYS = ('stdout', 'text', 'content', 'message', 'summary', 'answer', 'result', 'output', 'data')
    ERROR_KEYS = ('message', 'reason', 'detail', 'code')
    SEPARATOR = ' • '
    STDOUT = 'stdout'
    STDERR = 'stderr'
    RETURN_CODE = 'returncode'
    EXIT_DETAIL = 'returncode: {value}'

    @staticmethod
    def present(value):
        return value is not None and value != '' and value != [] and value != {}

    @classmethod
    def render(cls, value, keys=()):
        remaining = ValuePreview.STRING_CHARACTERS

        def scalar(text):
            nonlocal remaining
            size = min(remaining, len(text))
            remaining -= size
            return ValuePreview.literal(text, size) + (ValuePreview.ELLIPSIS if size < len(text) else '')

        def visit(item, depth=0):
            if remaining <= 0 or depth > ValuePreview.DEPTH:
                return ValuePreview.ELLIPSIS
            if isinstance(item, str):
                return scalar(item)
            if item is None:
                return ''
            if type(item) in (bool, int, float):
                return scalar(str(item))
            if isinstance(item, dict):
                # Process output uses the same value-based rule as other Tools.
                if keys == cls.OUTPUT_KEYS and (cls.STDOUT in item or cls.STDERR in item):
                    names = (cls.STDERR, cls.STDOUT) if item.get(cls.RETURN_CODE) else (cls.STDOUT, cls.STDERR)
                    parts = [visit(item[name], depth + 1) for name in names if cls.present(item.get(name))]
                    if type(item.get(cls.RETURN_CODE)) is int and item[cls.RETURN_CODE] != 0:
                        parts.append(scalar(cls.EXIT_DETAIL.format(value=item[cls.RETURN_CODE])))
                    if parts:
                        return cls.SEPARATOR.join(parts)
                for key in keys:
                    if cls.present(item.get(key)):
                        return visit(item[key], depth + 1)
                parts = []
                for key, child in islice(item.items(), ValuePreview.ITEMS):
                    if cls.present(child):
                        parts.append(scalar(str(key)[:TextPreview.KEY_CHARS]) + TextPreview.KEY_SEPARATOR + visit(child, depth + 1))
                if len(item) > ValuePreview.ITEMS:
                    parts.append(ValuePreview.ELLIPSIS)
                return cls.SEPARATOR.join(parts)
            if isinstance(item, (list, tuple)):
                parts = [visit(child, depth + 1) for child in islice(item, ValuePreview.ITEMS)]
                if len(item) > ValuePreview.ITEMS:
                    parts.append(ValuePreview.ELLIPSIS)
                return TextPreview.SPACE.join(parts)
            return ValuePreview.ELLIPSIS

        return TextPreview.SPACE.join(visit(value).split())


class ToolDetails:
    @classmethod
    def project(cls, metadata, *, name='', error=None, compact=False):
        arguments = metadata.get(ToolStepMetadata.ARGUMENTS, {})
        output = metadata.get(ExecutionObservations.Field.OUTPUT)
        data = output.get(EngineOutput.data.__name__) if output else None
        result = []
        if name in (QuestionTool.NAME, QuestionTool.NONBLOCKING_NAME):
            result.append((MessageKey.QUESTION_HEADING, ValuePreview.render(arguments.get(QuestionArguments.QUESTION, ''))))
            if arguments.get(QuestionArguments.DESCRIPTION):
                result.append((MessageKey.CARD_DESCRIPTION, ValuePreview.render(arguments[QuestionArguments.DESCRIPTION])))
            if isinstance(data, dict) and InteractionResponse.value.__name__ in data:
                result.append((MessageKey.CARD_ANSWER, ValuePreview.render(data[InteractionResponse.value.__name__])))
        else:
            if arguments:
                result.append((MessageKey.CARD_INPUT, TextPreview.render(arguments, TextPreview.INPUT_KEYS)
                               if compact else ValuePreview.render(arguments)))
            if output is not None:
                result.append((MessageKey.TOOL_RESULT, TextPreview.render(data, TextPreview.OUTPUT_KEYS)
                               if compact else ValuePreview.render(data)))
        if error:
            result.append((MessageKey.CARD_ERROR, TextPreview.render(error, TextPreview.ERROR_KEYS)
                           if compact else ValuePreview.render(error)))
        return tuple(result)
