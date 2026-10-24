"""Current conversation event wire and transient replay projections.

Readers preserve the existing per-event access/validation order. They do not
repair missing journal evidence or create a second stored conversation format.
"""

import json
from dataclasses import dataclass

from llm.core.models import Message, MessageStatus
from llm.core.results import EngineDelta, OutputOperation
from llm.services.infrastructure.storage import record


class ConversationCreation:
    def __init__(self, value):
        self._value = value

    @property
    def id(self):
        return self._value[Message.id.__name__]

    @property
    def count_key(self):
        return self._value[Message.role.__name__], self._value[Message.status.__name__]

    def message(self):
        return Message.from_record(self._value)


class ConversationEvent:
    TYPE = "type"
    MESSAGE = "message"
    ID = Message.id.__name__
    TEXT = EngineDelta.text.__name__
    OPERATION = EngineDelta.operation.__name__
    STATUS = Message.status.__name__
    METADATA = Message.metadata.__name__
    RUN_ID = Message.run_id.__name__
    REASON = "reason"
    CREATE = "message.create"
    DELTA = "message.delta"
    CHANGE_STATUS = "message.status"
    CHANGE_METADATA = "message.metadata"
    BIND_RUN = "message.run"
    RECONCILIATION = "output_reconciliation"
    INVALID_DELTA_TEXT = "Message delta text must be a string"
    INVALID_OPERATION = "Message delta operation must be append or replace"
    UNKNOWN_EVENT = "Unknown conversation event: {kind}"

    def __init__(self, value):
        self._value = value

    @property
    def kind(self):
        return self._value[self.TYPE]

    @property
    def creation(self):
        return ConversationCreation(self._value[self.MESSAGE])

    @property
    def id(self):
        return self._value[self.ID]

    @property
    def text(self):
        return self._value[self.TEXT]

    @property
    def operation(self):
        return self._value.get(self.OPERATION, OutputOperation.APPEND)

    @property
    def explicit_operation(self):
        return self._value[self.OPERATION]

    @property
    def status(self):
        return self._value[self.STATUS]

    @property
    def metadata(self):
        return self._value[self.METADATA]

    @property
    def run_id(self):
        return self._value[self.RUN_ID]

    def validate_change(self, message):
        """Preflight and replay share one contract; validation never mutates state."""
        if self.kind == self.DELTA:
            if not isinstance(self.text, str):
                raise TypeError(self.INVALID_DELTA_TEXT)
            if self.operation not in tuple(OutputOperation):
                raise ValueError(self.INVALID_OPERATION)
        elif self.kind == self.CHANGE_STATUS:
            MessageStatus(self.status)
        elif self.kind == self.CHANGE_METADATA:
            metadata = dict(message.metadata)
            metadata.update(self.metadata)
            message.validate_changes(metadata=metadata)
        elif self.kind == self.BIND_RUN:
            message.validate_changes(run_id=self.run_id)
        else:
            raise ValueError(self.UNKNOWN_EVENT.format(kind=self.kind))

    @classmethod
    def create(cls, message):
        return {cls.TYPE: cls.CREATE, cls.MESSAGE: record(message)}

    @classmethod
    def delta(cls, identifier, text, operation):
        return {cls.TYPE: cls.DELTA, cls.ID: identifier, cls.TEXT: text, cls.OPERATION: operation}

    @classmethod
    def reconcile(cls, identifier, text):
        return {**cls.delta(identifier, text, OutputOperation.REPLACE), cls.REASON: cls.RECONCILIATION}

    @classmethod
    def change_status(cls, identifier, status):
        return {cls.TYPE: cls.CHANGE_STATUS, cls.ID: identifier, cls.STATUS: status}

    @classmethod
    def bind_run(cls, identifier, run_id):
        return {cls.TYPE: cls.BIND_RUN, cls.ID: identifier, cls.RUN_ID: run_id}

    @classmethod
    def change_metadata(cls, identifier, metadata):
        return {cls.TYPE: cls.CHANGE_METADATA, cls.ID: identifier, cls.METADATA: metadata}

    @property
    def observation_id(self):
        return self._value.get(self.ID, self._value.get(self.MESSAGE, {}).get(self.ID))

    def log(self, directory):
        from llm.services.infrastructure.logging import log_event

        message = self._value.get(self.MESSAGE, {})
        log_event(
            directory,
            self.kind,
            entity_id=self._value.get(self.ID, message.get(self.ID)),
            status=self._value.get(self.STATUS, message.get(self.STATUS)),
        )


class ConversationJournal:
    ENCODING = "utf-8"
    LINE_END = "\n"
    RECORD_END = b"\n"
    EMPTY_BYTES = b""
    ENSURE_ASCII = False
    ALLOW_NON_FINITE = False
    READ_MODE = "rb"
    REPLACE_MODE = "w"
    CREATE_PARENTS = True
    ALLOW_EXISTING_DIRECTORY = True

    @classmethod
    def line(cls, event):
        return json.dumps(event, ensure_ascii=cls.ENSURE_ASCII, allow_nan=cls.ALLOW_NON_FINITE) + cls.LINE_END

    @classmethod
    def encode(cls, event):
        return cls.line(event).encode(cls.ENCODING)

    @classmethod
    def encode_many(cls, events):
        return cls.EMPTY_BYTES.join(cls.encode(event) for event in events)

    @classmethod
    def snapshot(cls, event):
        return json.loads(json.dumps(event, ensure_ascii=cls.ENSURE_ASCII, allow_nan=cls.ALLOW_NON_FINITE))


@dataclass(frozen=True, slots=True)
class ConversationFileStamp:
    device: int
    inode: int
    size: int
    modified_ns: int

    @classmethod
    def capture(cls, stat):
        return cls(stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)

    @property
    def file_identity(self):
        return self.device, self.inode
