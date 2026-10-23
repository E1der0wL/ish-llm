"""Stored compaction state validation and typed projections.

Compactions retains storage, transactions and the source/cursor validation callbacks.
"""

import re
from copy import deepcopy
from jsonschema import ValidationError
from llm.components.base import validate_name
from llm.services.infrastructure.storage import revision_token
from .compaction_values import CompactionOptions, SummaryCheckpoint


class CompactionState:
    ABSENT = None
    MINIMUM_COUNT = 0
    REVISION_PATTERN = r"[a-f0-9]{64}"
    SOURCE_CHANGED = "Summary source changed"
    REQUEST_REPLACED = "Summary request was replaced"
    CANCELLED_BEFORE_PUBLICATION = "Summary cancelled before publication"
    INCOMPLETE_PUBLICATION = "Cannot publish an incomplete summary"
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
        CONTEXT = 'context'
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


    @classmethod
    def describe(cls, state):
        return {cls.Field.REVISION: revision_token(state), cls.Field.CONTEXT: deepcopy(state.get(cls.Field.VIEW)),
                cls.Field.REQUEST: deepcopy(state.get(cls.Field.REQUEST))}

    @property
    def has_observed_view(self):
        return bool(self._value.get(self.Field.VIEW))

    @property
    def view(self):
        return CompactionView(self._value[self.Field.VIEW])

    def request_context(self, identifier, status, options, selected):
        self._value[self.Field.REQUEST] = {self.Field.ID: identifier, self.Field.STATUS: status,
            self.Field.OPTIONS: deepcopy(options), self.Field.SOURCES: [item for group in selected for item in group]}

    @classmethod
    def scope_identity(cls, scope):
        return {cls.Field.SCOPE: scope}

    def collect_history_references(self, messages, unfinished_states):
        messages.update(CompactionSource(item).identifier for item in self._value.get(self.Field.APPLIED, {}).get(self.Field.SOURCES, []))
        if self._value.get(self.Field.REQUEST, {}).get(self.Field.STATUS) in unfinished_states:
            messages.update(CompactionSource(item).identifier for item in self._value[self.Field.REQUEST][self.Field.SOURCES])

    @property
    def request(self):
        return CompactionRequest(self._value[self.Field.REQUEST])

    @property
    def previous_applied(self):
        return CompactionApplied(self._value.get(self.Field.APPLIED))

    @property
    def has_applied(self):
        return self.Field.APPLIED in self._value

    @property
    def applied_record(self):
        return self._value[self.Field.APPLIED]

    @property
    def optional_request(self):
        return CompactionRequest(self._value.get(self.Field.REQUEST, {}))

    def observe(self, view):
        self._value[self.Field.VIEW] = view

    def apply(self, applied):
        self._value[self.Field.APPLIED] = applied

    @classmethod
    def tool_request(cls, *, identifier, status, mode, options, reference, tool_call_id):
        return {cls.Field.REQUEST: {cls.Field.ID: identifier, cls.Field.STATUS: status,
            cls.Field.MODE: mode, cls.Field.OPTIONS: deepcopy(options), cls.Field.REFERENCE: reference,
            cls.Field.TOOL_CALL_ID: tool_call_id}}


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
    def reference_record(self):
        return self._value[CompactionState.Field.REFERENCE]

    @property
    def reference_revision(self):
        return CompactionReference(self.reference_record).revision

    @property
    def run_id(self):
        return self._value[CompactionState.Field.RUN_ID]

    @property
    def options_record(self):
        return self._value[CompactionState.Field.OPTIONS]

    @property
    def options(self):
        return CompactionOptions(self.options_record)

    @property
    def sources(self):
        return self._value[CompactionState.Field.SOURCES]

    @property
    def stored_sources(self):
        return self._value.get(CompactionState.Field.STORED_SOURCES, [])

    @property
    def has_reference(self):
        return CompactionState.Field.REFERENCE in self._value

    def checkpoint(self, checkpoint):
        self._value[CompactionState.Field.CHECKPOINT] = checkpoint

    def set_stored_sources(self, values):
        self._value[CompactionState.Field.STORED_SOURCES] = values

    def to_record(self):
        return self._value

    @property
    def optional_identifier(self):
        return self._value.get(CompactionState.Field.ID)

    @property
    def optional_status(self):
        return self._value.get(CompactionState.Field.STATUS)

    @property
    def optional_run_id(self):
        return self._value.get(CompactionState.Field.RUN_ID)

    @property
    def checkpoint_record(self):
        return self._value.get(CompactionState.Field.CHECKPOINT)

    def finish(self, status, error):
        self._value.update({CompactionState.Field.STATUS: status, CompactionState.Field.ERROR: error})

    def start(self, status, run_id):
        self._value.update({CompactionState.Field.STATUS: status, CompactionState.Field.RUN_ID: run_id})


class CompactionApplied:
    def __init__(self, value):
        self._value = value

    @property
    def summary(self):
        return self._value[CompactionState.Field.SUMMARY]

    def __bool__(self):
        return bool(self._value)

    @property
    def sources(self):
        return self._value[CompactionState.Field.SOURCES]

    @classmethod
    def context(cls, text, combined, source):
        return cls({CompactionState.Field.SUMMARY: text,
                    CompactionState.Field.SOURCES: CompactionSource.unique(combined),
                    CompactionState.Field.SOURCE: source})

    def to_record(self):
        return self._value

    @staticmethod
    def from_summary(text):
        return {CompactionState.Field.SUMMARY: text}


class CompactionView:
    def __init__(self, value):
        self._value = value

    @staticmethod
    def create(*, run_id, scope, iteration, tokens, max_tokens, reserve_tokens, turns):
        f = CompactionState.Field
        return {f.RUN_ID: run_id, f.SCOPE: scope, f.ITERATION: iteration, f.TOKENS: tokens,
                f.MAX_TOKENS: max_tokens, f.RESERVE_TOKENS: reserve_tokens, f.TURNS: turns}

    @property
    def turns(self):
        return self._value[CompactionState.Field.TURNS]


class CompactionSource:
    ID = CompactionState.Field.ID
    DIGEST = CompactionState.Field.DIGEST
    REVISION = CompactionState.Field.REVISION
    REVISION_PATTERN = CompactionState.REVISION_PATTERN
    ABSENT = None
    INVALID_LIST = "Invalid compaction source list"
    INVALID_IDENTITY = "Invalid compaction source identity"
    DUPLICATE = "Duplicate compaction source"

    def __init__(self, value):
        self._value = value

    @classmethod
    def validate(cls, value, hash_field):
        if not isinstance(value, list):
            raise ValueError(cls.INVALID_LIST)
        identifiers = set()
        for source in value:
            if (not isinstance(source, dict) or source.keys() != {cls.ID, hash_field}
                    or not isinstance(source[hash_field], str)
                    or re.fullmatch(cls.REVISION_PATTERN, source[hash_field]) is cls.ABSENT):
                raise ValueError(cls.INVALID_IDENTITY)
            identifier = validate_name(source[cls.ID])
            if identifier in identifiers:
                raise ValueError(cls.DUPLICATE)
            identifiers.add(identifier)
        return identifiers

    @classmethod
    def signatures(cls, messages):
        return [{cls.ID: m.source_id, cls.DIGEST: revision_token(m.message.to_dict())} for m in messages]

    @classmethod
    def stored(cls, sources, read_revision):
        return [{cls.ID: item[cls.ID], cls.REVISION: read_revision(item[cls.ID])} for item in sources]

    @classmethod
    def unique(cls, sources):
        return list({source[cls.ID]: source for source in sources}.values())

    @property
    def digest(self):
        return self._value[self.DIGEST]

    @property
    def identifier(self):
        return self._value[self.ID]

    @property
    def revision(self):
        return self._value[self.REVISION]


class CompactionReference:
    SOURCE_PATH = "tool-results/{session}/{identifier}.txt"

    def __init__(self, value):
        self._value = value

    @classmethod
    def path_for(cls, session_id, identifier):
        return cls.SOURCE_PATH.format(session=session_id, identifier=identifier)

    @staticmethod
    def create(destination, path, revision):
        return {**destination, CompactionState.Field.PATH: path, CompactionState.Field.REVISION: revision}

    @property
    def component(self):
        return self._value[CompactionState.Field.COMPONENT]

    @property
    def identifier(self):
        return self._value[CompactionState.Field.IDENTIFIER]

    @property
    def path(self):
        return self._value[CompactionState.Field.PATH]

    @property
    def revision(self):
        return self._value[CompactionState.Field.REVISION]


class CompactionSourceRead:
    TEXT = "text"
    OFFSET = "offset"
    REFERENCE = CompactionState.Field.REFERENCE
    MINIMUM_OFFSET = 0
    MINIMUM_LENGTH = 0
    PROBE_OFFSET = MINIMUM_OFFSET
    VERIFY_ONLY = MINIMUM_LENGTH
    TO_END = None
    NO_EVIDENCE = None
    EMPTY_TEXT = ""
    TEXT_ENCODING = "utf-8"
    INVALID_RANGE = "Source ranges must be nonnegative integers"
    STALE_FILE = "Summary source file changed; the saved reference is stale"
    CHANGED_REVISION = "Source revision changed"

    @classmethod
    def validate_range(cls, offset, limit):
        if type(offset) is not int or offset < cls.MINIMUM_OFFSET or limit is not cls.TO_END and (type(limit) is not int or limit < cls.MINIMUM_LENGTH):
            raise ValueError(cls.INVALID_RANGE)

    @classmethod
    def create(cls, text, offset, reference):
        return {cls.TEXT: text, cls.OFFSET: offset, cls.REFERENCE: deepcopy(reference)}
