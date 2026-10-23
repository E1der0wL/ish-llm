"""Strict recipient admission and atomic dispatch evidence for one User request.

This owner validates evidence; it never fills missing records, selects an Engine,
creates a Run or replays an effect. Runtime integration supplies the transaction.
"""

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from llm.core.agents import AgentBinding, AgentDefinition
from llm.core.models import Message, MessageRole, MessageStatus, ProjectConfig
from llm.core.steering import is_instruction
from llm.services.infrastructure.storage import child, record, revision_token


@dataclass(frozen=True, init=False, slots=True)
class RequestDispatch:
    """Immutable recipient map and original history, validated before projection."""

    INVALID_FIELDS = "Dispatch requires the complete Run map and history evidence"
    INCOMPLETE_RECIPIENTS = "Dispatch must cover every recipient"
    INVALID_RUN = "Invalid dispatched Run identity"
    DUPLICATE_RUN = "Recipient Runs must be distinct"
    MISSING_HISTORY = "Dispatch requires its original history snapshot"
    DUPLICATE_MESSAGE = "Duplicate message in dispatch history"
    RECURSIVE_HISTORY = "Model history cannot recursively embed admission records"
    INVALID_INPUT = "Dispatch history must end with its committed User input"
    INVALID_REVISION = "Dispatch history revision mismatch"

    class Field(StrEnum):
        RUN_IDS = "run_ids"
        HISTORY = "history"
        HISTORY_REVISION = "history_revision"

    _value: dict
    _history: tuple[Message, ...]

    @classmethod
    def read(cls, dispatch, *, recipients, message_id):
        if not isinstance(dispatch, dict) or dispatch.keys() != set(cls.Field):
            raise ValueError(cls.INVALID_FIELDS)
        identifiers = dispatch[cls.Field.RUN_IDS]
        if not isinstance(identifiers, dict) or identifiers.keys() != set(recipients):
            raise ValueError(cls.INCOMPLETE_RECIPIENTS)
        for identifier in identifiers.values():
            if not isinstance(identifier, str):
                raise ValueError(cls.INVALID_RUN)
            child(Path(), identifier)
        if len(set(identifiers.values())) != len(identifiers):
            raise ValueError(cls.DUPLICATE_RUN)
        history = dispatch[cls.Field.HISTORY]
        if not isinstance(history, list) or not history:
            raise ValueError(cls.MISSING_HISTORY)
        messages = [Message.from_record(item) for item in history]
        if len({message.id for message in messages}) != len(messages):
            raise ValueError(cls.DUPLICATE_MESSAGE)
        if any(RequestAdmission.present(message) for message in messages):
            raise ValueError(cls.RECURSIVE_HISTORY)
        current = messages[-1]
        if (current.id != message_id or current.role != MessageRole.USER
                or is_instruction(current) or current.run_id is not None
                or current.status != MessageStatus.COMMITTED):
            raise ValueError(cls.INVALID_INPUT)
        # Verify the original wire records, before any normalized Message projection.
        if dispatch[cls.Field.HISTORY_REVISION] != revision_token(history):
            raise ValueError(cls.INVALID_REVISION)

        result = object.__new__(cls)
        object.__setattr__(result, cls._value.__name__, deepcopy(dispatch))
        object.__setattr__(result, cls._history.__name__, tuple(deepcopy(messages)))
        return result

    @classmethod
    def create(cls, *, recipients, message_id, run_ids, history):
        snapshot = []
        for message in history:
            detached = deepcopy(message)
            detached.metadata.pop(RequestAdmission.KEY, None)
            snapshot.append(record(detached))
        return cls.read({cls.Field.RUN_IDS: deepcopy(run_ids), cls.Field.HISTORY: snapshot,
                         cls.Field.HISTORY_REVISION: revision_token(snapshot)},
                        recipients=recipients, message_id=message_id)

    @property
    def run_ids(self):
        return deepcopy(self._value[self.Field.RUN_IDS])

    @property
    def history(self):
        return deepcopy(self._history)

    @property
    def history_revision(self):
        return self._value[self.Field.HISTORY_REVISION]

    def to_dict(self):
        return deepcopy(self._value)


@dataclass(frozen=True, init=False, slots=True)
class RequestAdmission:
    INVALID_RECIPIENTS = "Recipients require a nonempty collection of Agent IDs"
    DUPLICATE_RECIPIENTS = "Duplicate Agent recipients"
    OUTSIDE_MEMBERSHIP = "Recipient is not a Session participant"
    INVALID_BINDINGS = "Request bindings require an object"
    INVALID_FIELDS = "Request admission requires all current fields"
    INVALID_VERSION = "Unsupported request admission schema_version"
    INVALID_IDENTITY = "Invalid request admission identity"
    NONCANONICAL_RECIPIENTS = "Stored recipients must be canonical"
    INCOMPLETE_BINDINGS = "Every recipient requires exactly one binding"
    BINDING_OWNER_MISMATCH = "Recipient binding ownership mismatch"
    ALREADY_DISPATCHED = "Request has already been dispatched"
    INVALID_MESSAGE = "Admission belongs to an executable User with independent recipient Runs"
    OWNER_MISMATCH = "Request admission ownership mismatch"
    STATUS_MISMATCH = "Request status disagrees with dispatch evidence"
    UNDISPATCHED_RUNS = "Undispatched request already has Run evidence"
    INCOMPLETE_RUNS = "Incomplete or duplicate recipient Run group"
    DUPLICATE_ASSISTANTS = "Recipient Assistants must be distinct"
    RUN_BINDING_MISMATCH = "Run differs from its admitted owner or Agent binding"
    RUN_CONTEXT_MISMATCH = "Run differs from its original request context"
    MISSING_ADMISSION = "Executable User requires its original recipient admission"
    MISSING_USER = "Run requires its original User admission"
    MISSING_ASSISTANT = "Dispatched Run requires its original Assistant"

    SCHEMA_VERSION = 1
    KEY = "admission"
    CLONE_KEY = "cloned_request"
    ALL = "all"
    DEFAULT_ALLOW_EMPTY = False
    ALLOW_EMPTY_MEMBERSHIP = True
    UNDISPATCHED = None

    class Field(StrEnum):
        SCHEMA_VERSION = "schema_version"
        PROJECT_ID = "project_id"
        SESSION_ID = "session_id"
        MESSAGE_ID = "message_id"
        RECIPIENTS = "recipients"
        BINDINGS = "bindings"
        DISPATCH = "dispatch"

    IDENTITY_FIELDS = (Field.PROJECT_ID, Field.SESSION_ID, Field.MESSAGE_ID)
    CLONE_FIELDS = (*IDENTITY_FIELDS, Field.RECIPIENTS)
    UNDISPATCHED_STATUSES = (MessageStatus.QUEUED, MessageStatus.CANCELLED)
    DISPATCHED_STATUSES = (MessageStatus.COMMITTED,)

    _value: dict
    _dispatch: RequestDispatch | None

    @classmethod
    def identifiers(cls, values, *, empty=DEFAULT_ALLOW_EMPTY):
        if not isinstance(values, (list, tuple)) or (not values and not empty):
            raise ValueError(cls.INVALID_RECIPIENTS)
        for identifier in values:
            AgentDefinition.identifier(identifier)
        if len(set(values)) != len(values):
            raise ValueError(cls.DUPLICATE_RECIPIENTS)
        return sorted(values)

    @classmethod
    def select(cls, recipients, participants):
        """Only explicit 'all' expands the current Session membership snapshot."""
        members = cls.identifiers(participants, empty=cls.ALLOW_EMPTY_MEMBERSHIP)
        selected = cls.identifiers(members if recipients == cls.ALL else recipients)
        if not set(selected) <= set(members):
            raise ValueError(cls.OUTSIDE_MEMBERSHIP)
        return selected

    @classmethod
    def create(cls, *, project_id, session_id, message_id, bindings):
        if not isinstance(bindings, dict):
            raise ValueError(cls.INVALID_BINDINGS)
        return cls.validate({
            cls.Field.SCHEMA_VERSION: cls.SCHEMA_VERSION,
            cls.Field.PROJECT_ID: project_id, cls.Field.SESSION_ID: session_id, cls.Field.MESSAGE_ID: message_id,
            cls.Field.RECIPIENTS: cls.identifiers(list(bindings)), cls.Field.BINDINGS: deepcopy(bindings),
            cls.Field.DISPATCH: cls.UNDISPATCHED,
        })

    @classmethod
    def validate(cls, value):
        if not isinstance(value, dict) or value.keys() != set(cls.Field):
            raise ValueError(cls.INVALID_FIELDS)
        ProjectConfig.validate_json(value)
        if type(value[cls.Field.SCHEMA_VERSION]) is not int or value[cls.Field.SCHEMA_VERSION] != cls.SCHEMA_VERSION:
            raise ValueError(cls.INVALID_VERSION)
        for name in cls.IDENTITY_FIELDS:
            if not isinstance(value[name], str):
                raise ValueError(cls.INVALID_IDENTITY)
            child(Path(), value[name])
        recipients = cls.identifiers(value[cls.Field.RECIPIENTS])
        if value[cls.Field.RECIPIENTS] != recipients:
            raise ValueError(cls.NONCANONICAL_RECIPIENTS)
        bindings = value[cls.Field.BINDINGS]
        if not isinstance(bindings, dict) or bindings.keys() != set(recipients):
            raise ValueError(cls.INCOMPLETE_BINDINGS)
        for identifier, binding in bindings.items():
            binding = AgentBinding.read(binding)
            if binding.agent_id != identifier or binding.project_id != value[cls.Field.PROJECT_ID]:
                raise ValueError(cls.BINDING_OWNER_MISMATCH)
        dispatch = value[cls.Field.DISPATCH]
        if dispatch is not cls.UNDISPATCHED:
            dispatch = RequestDispatch.read(dispatch, recipients=recipients, message_id=value[cls.Field.MESSAGE_ID])
        result = object.__new__(cls)
        object.__setattr__(result, cls._value.__name__, deepcopy(value))
        object.__setattr__(result, cls._dispatch.__name__, dispatch)
        return result

    @classmethod
    def dispatch(cls, admission, *, run_ids, history):
        if admission.dispatched is not cls.UNDISPATCHED:
            raise ValueError(cls.ALREADY_DISPATCHED)
        dispatch = RequestDispatch.create(recipients=admission.recipients, message_id=admission.message_id,
                                          run_ids=run_ids, history=history)
        value = admission.to_dict()
        value[cls.Field.DISPATCH] = dispatch.to_dict()
        return cls.validate(value)

    @property
    def project_id(self):
        return self._value[self.Field.PROJECT_ID]

    @property
    def session_id(self):
        return self._value[self.Field.SESSION_ID]

    @property
    def message_id(self):
        return self._value[self.Field.MESSAGE_ID]

    @property
    def recipients(self):
        return deepcopy(self._value[self.Field.RECIPIENTS])

    @property
    def dispatched(self):
        return self._dispatch

    def binding(self, identifier):
        return AgentBinding.read(self._value[self.Field.BINDINGS][identifier])

    def to_dict(self):
        return deepcopy(self._value)

    def metadata(self):
        return {self.KEY: self.to_dict()}

    @classmethod
    def present(cls, message):
        return cls.KEY in message.metadata

    @classmethod
    def from_message(cls, message):
        """Decode evidence without granting execution authority from its own identity."""
        return cls.validate(message.metadata.get(cls.KEY))

    @classmethod
    def read(cls, message, *, project_id, session_id):
        if message.role != MessageRole.USER or is_instruction(message) or message.run_id is not None:
            raise ValueError(cls.INVALID_MESSAGE)
        value = cls.from_message(message)
        if (value.project_id, value.session_id, value.message_id) != (
                project_id, session_id, message.id):
            raise ValueError(cls.OWNER_MISMATCH)
        allowed = (cls.UNDISPATCHED_STATUSES if value.dispatched is cls.UNDISPATCHED else cls.DISPATCHED_STATUSES)
        if message.status not in allowed:
            raise ValueError(cls.STATUS_MISMATCH)
        return value

    @classmethod
    def runs(cls, message, records, *, project_id, session_id):
        """Check both witnesses before recovery or a query may return an empty group."""
        admission = cls.read(message, project_id=project_id, session_id=session_id)
        selected = [run for run in records if run.input_message_id == message.id]
        dispatch = admission.dispatched
        if dispatch is cls.UNDISPATCHED:
            if selected:
                raise ValueError(cls.UNDISPATCHED_RUNS)
            return []
        by_agent = {run.agent_id: run for run in selected}
        if (len(by_agent) != len(selected) or set(by_agent) != set(admission.recipients)
                or dispatch.run_ids != {agent: run.id for agent, run in by_agent.items()}):
            raise ValueError(cls.INCOMPLETE_RUNS)
        if len({run.assistant_message_id for run in selected}) != len(selected):
            raise ValueError(cls.DUPLICATE_ASSISTANTS)
        for identifier, run in by_agent.items():
            binding = admission.binding(identifier)
            if (run.session_id != session_id or run.engine != binding.engine
                    or AgentBinding.from_run(run) != binding):
                raise ValueError(cls.RUN_BINDING_MISMATCH)
            if AgentBinding.request_context(run.metadata) != AgentBinding.request_context(message.metadata):
                raise ValueError(cls.RUN_CONTEXT_MISMATCH)
        return [by_agent[identifier] for identifier in admission.recipients]

    @classmethod
    def run_ids(cls, message):
        """Read history links without granting authority from a self-declared owner."""
        if message.role != MessageRole.USER or is_instruction(message):
            return (message.run_id,) if message.run_id is not None else ()
        if not cls.present(message):
            if message.run_id is not None or message.status == MessageStatus.QUEUED:
                raise ValueError(cls.MISSING_ADMISSION)
            return ()  # Explicitly imported/cloned observations carry no execution link.
        value = cls.from_message(message)
        value = cls.read(message, project_id=value.project_id, session_id=value.session_id)
        return tuple(value.dispatched.run_ids.values()) if value.dispatched is not cls.UNDISPATCHED else ()

    @classmethod
    def history(cls, messages, records, *, project_id, session_id, persistent):
        """Validate complete dispatch witnesses before recovery or deletion writes."""
        by_id = {message.id: message for message in messages}
        for message in messages:
            if cls.present(message) or (message.role == MessageRole.USER
                    and not is_instruction(message) and message.status == MessageStatus.QUEUED):
                cls.runs(message, records, project_id=project_id, session_id=session_id)
        for run in records:
            source = by_id.get(run.input_message_id)
            if source is None:
                if persistent:
                    raise ValueError(cls.MISSING_USER)
                continue  # Memory lifetime loss permits interruption, never replay.
            cls.runs(source, records, project_id=project_id, session_id=session_id)
            assistant = by_id.get(run.assistant_message_id)
            if (assistant is None or assistant.role != MessageRole.ASSISTANT
                    or assistant.run_id != run.id):
                raise ValueError(cls.MISSING_ASSISTANT)

    @classmethod
    def clone_message(cls, message):
        """Detach copied observations, retaining source identity without executable evidence."""
        value = cls.from_message(message)
        result = deepcopy(message)
        result.metadata.pop(cls.KEY)
        result.metadata[cls.CLONE_KEY] = {name: deepcopy(value._value[name]) for name in cls.CLONE_FIELDS}
        return result
