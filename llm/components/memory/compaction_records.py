"""Stored compaction state validation and typed projections.

Compactions retains storage, transactions and the source/cursor validation callbacks.
"""

import re
from jsonschema import ValidationError
from llm.components.base import validate_name
from .compaction_values import CompactionOptions, SummaryCheckpoint


class CompactionState:
    ABSENT = None
    MINIMUM_COUNT = 0
    REVISION_PATTERN = r"[a-f0-9]{64}"
    INVALID_STATE = 'Invalid stored compaction state'
    INVALID_CONTEXT = 'Invalid stored compaction context'
    INVALID_TOKENS = 'Invalid stored compaction token count'
    MISSING_TOOL_REQUEST = 'Tool summary requires its stored request'
    INVALID_REQUEST = 'Invalid stored compaction request'
    MISSING_RUN = 'Running summary requires its Run'
    INVALID_ERROR = 'Invalid summary error'
    INVALID_OPTIONS = 'Invalid stored compaction options'
    MISSING_SOURCE_EVIDENCE = 'Missing compaction source evidence'
    INVALID_TOOL_REFERENCE = 'Invalid Tool summary reference'
    INVALID_REFERENCE_STATE = 'Invalid reference-only Tool state'
    MISSING_CURSOR = 'Partial summary requires its completed cursor'
    INCOMPLETE_CURSOR = 'Applied summary requires its complete cursor'
    INVALID_APPLIED = 'Invalid applied summary'
    INVALID_APPLIED_SOURCES = 'Invalid applied context sources'
    INVALID_APPLIED_CONTENT = 'Invalid applied summary content'

    class Field:
        VIEW = 'view'
        REQUEST = 'request'
        APPLIED = 'applied'
        RUN_ID = 'run_id'
        SCOPE = 'scope'
        ITERATION = 'iteration'
        TOKENS = 'tokens'
        MAX_TOKENS = 'max_tokens'
        RESERVE_TOKENS = 'reserve_tokens'
        TURNS = 'turns'
        ID = 'id'
        STATUS = 'status'
        OPTIONS = 'options'
        ERROR = 'error'
        SOURCES = 'sources'
        STORED_SOURCES = 'stored_sources'
        REFERENCE = 'reference'
        MODE = 'mode'
        TOOL_CALL_ID = 'tool_call_id'
        COMPONENT = 'component'
        IDENTIFIER = 'identifier'
        PATH = 'path'
        REVISION = 'revision'
        CHECKPOINT = 'checkpoint'
        SUMMARY = 'summary'
        DIGEST = 'digest'
        SOURCE = 'source'

    def __init__(self, value):
        self._value = value

    @classmethod
    def validate_state(cls, state, *, scope, tool_id, completion_adapter, declaration, validate_sources, validate_options, validate_cursor):
        if not isinstance(state, dict) or not state or state.keys() - {cls.Field.VIEW, cls.Field.REQUEST, cls.Field.APPLIED}:
            raise ValueError(cls.INVALID_STATE)
        if tool_id is cls.ABSENT:
            view = state.get(cls.Field.VIEW)
            if (not isinstance(view, dict) or view.keys() != {
                    cls.Field.RUN_ID, cls.Field.SCOPE, cls.Field.ITERATION, cls.Field.TOKENS, cls.Field.MAX_TOKENS, cls.Field.RESERVE_TOKENS, cls.Field.TURNS}
                    or view[cls.Field.SCOPE] != scope or type(view[cls.Field.ITERATION]) is not int or view[cls.Field.ITERATION] < cls.MINIMUM_COUNT
                    or not isinstance(view[cls.Field.TURNS], list)):
                raise ValueError(cls.INVALID_CONTEXT)
            validate_name(view[cls.Field.RUN_ID])
            for key in (cls.Field.TOKENS, cls.Field.MAX_TOKENS, cls.Field.RESERVE_TOKENS):
                if view[key] is not cls.ABSENT and (type(view[key]) is not int or view[key] < cls.MINIMUM_COUNT):
                    raise ValueError(cls.INVALID_TOKENS)
            for group in view[cls.Field.TURNS]:
                validate_sources(group, cls.Field.DIGEST)
        elif cls.Field.VIEW in state or cls.Field.REQUEST not in state:
            raise ValueError(cls.MISSING_TOOL_REQUEST)
        if cls.Field.REQUEST in state:
            request = state[cls.Field.REQUEST]
            if (not isinstance(request, dict) or not {cls.Field.ID, cls.Field.STATUS, cls.Field.OPTIONS} <= request.keys()
                    or not isinstance(request[cls.Field.STATUS], str) or request[cls.Field.STATUS] not in declaration.STATUSES
                    or not isinstance(request[cls.Field.OPTIONS], dict)):
                raise ValueError(cls.INVALID_REQUEST)
            validate_name(request[cls.Field.ID])
            if cls.Field.RUN_ID in request:
                validate_name(request[cls.Field.RUN_ID])
            if request[cls.Field.STATUS] == declaration.RUNNING and cls.Field.RUN_ID not in request:
                raise ValueError(cls.MISSING_RUN)
            if cls.Field.ERROR in request and request[cls.Field.ERROR] is not cls.ABSENT and not isinstance(request[cls.Field.ERROR], str):
                raise ValueError(cls.INVALID_ERROR)
            if tool_id is cls.ABSENT:
                try:
                    validate_options(request[cls.Field.OPTIONS], completion_adapter=completion_adapter)
                except ValidationError as error:
                    raise ValueError(cls.INVALID_OPTIONS) from error
                ids = validate_sources(request.get(cls.Field.SOURCES), cls.Field.DIGEST)
                if not ids or ids != validate_sources(request.get(cls.Field.STORED_SOURCES), cls.Field.REVISION):
                    raise ValueError(cls.MISSING_SOURCE_EVIDENCE)
            else:
                reference = request.get(cls.Field.REFERENCE)
                if (request[cls.Field.ID] != tool_id or request.get(cls.Field.MODE) not in declaration.MODES
                        or not isinstance(request.get(cls.Field.TOOL_CALL_ID), str) or not request[cls.Field.TOOL_CALL_ID]
                        or not isinstance(reference, dict)
                        or reference.keys() != {cls.Field.COMPONENT, cls.Field.IDENTIFIER, cls.Field.PATH, cls.Field.REVISION}
                        or not isinstance(reference[cls.Field.PATH], str) or not reference[cls.Field.PATH]
                        or not isinstance(reference[cls.Field.REVISION], str)
                        or re.fullmatch(cls.REVISION_PATTERN, reference[cls.Field.REVISION]) is cls.ABSENT):
                    raise ValueError(cls.INVALID_TOOL_REFERENCE)
                validate_name(reference[cls.Field.COMPONENT])
                validate_name(reference[cls.Field.IDENTIFIER])
                if request[cls.Field.MODE] == declaration.SUMMARY:
                    CompactionOptions.validate_tool(request[cls.Field.OPTIONS], completion_adapter=completion_adapter)
                elif request[cls.Field.STATUS] != declaration.APPLIED or state.get(cls.Field.APPLIED) != {} or cls.Field.CHECKPOINT in request:
                    raise ValueError(cls.INVALID_REFERENCE_STATE)
            if cls.Field.CHECKPOINT in request:
                validate_cursor(request[cls.Field.CHECKPOINT])
            if request[cls.Field.STATUS] == declaration.PARTIAL and cls.Field.CHECKPOINT not in request:
                raise ValueError(cls.MISSING_CURSOR)
            if request[cls.Field.STATUS] == declaration.APPLIED and not (tool_id is not cls.ABSENT and request[cls.Field.MODE] == declaration.REFERENCE):
                checkpoint = request.get(cls.Field.CHECKPOINT)
                if (not isinstance(checkpoint, dict) or SummaryCheckpoint(checkpoint).offset != SummaryCheckpoint(checkpoint).total_chars
                        or not isinstance(state.get(cls.Field.APPLIED), dict)
                        or state[cls.Field.APPLIED].get(cls.Field.SUMMARY) != SummaryCheckpoint(checkpoint).summary):
                    raise ValueError(cls.INCOMPLETE_CURSOR)
        if cls.Field.APPLIED in state:
            applied = state[cls.Field.APPLIED]
            if not isinstance(applied, dict):
                raise ValueError(cls.INVALID_APPLIED)
            if tool_id is cls.ABSENT:
                if (applied.keys() != {cls.Field.SUMMARY, cls.Field.SOURCES, cls.Field.SOURCE} or not isinstance(applied[cls.Field.SOURCE], dict)
                        or not validate_sources(applied[cls.Field.SOURCES], cls.Field.DIGEST)):
                    raise ValueError(cls.INVALID_APPLIED_SOURCES)
            if applied and (not isinstance(applied.get(cls.Field.SUMMARY), str) or not applied[cls.Field.SUMMARY].strip()):
                raise ValueError(cls.INVALID_APPLIED_CONTENT)


    @property
    def request(self):
        return CompactionRequest(self._value[self.Field.REQUEST])

    @property
    def has_applied(self):
        return self.Field.APPLIED in self._value

    @property
    def applied_record(self):
        return self._value[self.Field.APPLIED]


class CompactionRequest:
    def __init__(self, value):
        self._value = value

    @property
    def identifier(self):
        return self._value[CompactionState.Field.ID]

    @property
    def status(self):
        return self._value[CompactionState.Field.STATUS]

    @property
    def reference_revision(self):
        return self._value[CompactionState.Field.REFERENCE][CompactionState.Field.REVISION]

    def to_record(self):
        return self._value


class CompactionApplied:
    @staticmethod
    def from_summary(text):
        return {CompactionState.Field.SUMMARY: text}
