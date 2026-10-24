"""Memory processing inputs, transient caches and execution observations."""

import hashlib
import json
import re

from llm.components.base import validate_name
from llm.core.models import Message
from llm.engines.agent_values import AgentStepMetadata
from .records import MemoryRecord
from .sources import MemorySource
from .summary_records import SummaryCursor
from .compaction_values import SummaryCheckpoint
from .tool_result_values import ToolSummaryView


class MemoryText:
    WHITESPACE = r"\s+"
    SPACE = " "
    SUMMARY_PREFIX = "summary_"
    SUMMARY_DIGEST_LENGTH = 40
    SORT_KEYS = True
    ENSURE_ASCII = False
    ALLOW_NAN = False

    @classmethod
    def content_key(cls, text):
        return re.sub(cls.WHITESPACE, cls.SPACE, text.casefold()).strip()

    @classmethod
    def digest(cls, value):
        return hashlib.sha256(json.dumps(value, sort_keys=cls.SORT_KEYS,
            ensure_ascii=cls.ENSURE_ASCII, allow_nan=cls.ALLOW_NAN).encode()).hexdigest()

    @classmethod
    def summary_id(cls, session_id):
        return cls.SUMMARY_PREFIX + cls.digest(validate_name(session_id))[:cls.SUMMARY_DIGEST_LENGTH]


class MemoryMessageFingerprint:
    @staticmethod
    def project(message):
        return {Message.id.__name__: message.id, Message.role.__name__: str(message.role),
                Message.status.__name__: str(message.status), Message.content.__name__: message.content,
                Message.run_id.__name__: message.run_id, Message.metadata.__name__: message.metadata}


class MemorySummaryInput:
    PREVIOUS = "previous_summary"
    EXCHANGES = "exchanges"
    MESSAGES = "messages"
    MAXIMUM = "max_summary_chars"
    FRAGMENT = "fragment"
    OFFSET = SummaryCursor.Field.OFFSET
    TOTAL_CHARS = SummaryCheckpoint.Field.TOTAL_CHARS
    TRUNCATED = "truncated"
    COMPLETE_MESSAGE = False
    REFERENCE_ROLE = "reference"
    PARTIAL_REFERENCE = "partial"
    PARTIAL_NOTICE = "[Partial summary; original turn retained]"

    @classmethod
    def exchanges(cls, previous, exchanges, maximum):
        return {cls.PREVIOUS: previous, cls.EXCHANGES: exchanges, cls.MAXIMUM: maximum}

    @classmethod
    def exchange_fragment(cls, value, start, total, previous, maximum):
        return cls.exchanges(previous, [{cls.FRAGMENT: value, cls.OFFSET: start,
                                        cls.TOTAL_CHARS: total}], maximum)

    @classmethod
    def messages(cls, previous, messages, maximum):
        return {cls.PREVIOUS: previous, cls.MESSAGES: messages, cls.MAXIMUM: maximum}

    @classmethod
    def history(cls, messages):
        return [{Message.role.__name__: str(m.role), Message.status.__name__: str(m.status),
                 Message.content.__name__: m.content, cls.TRUNCATED: cls.COMPLETE_MESSAGE} for m in messages]

    @classmethod
    def history_fragment(cls, value, start, total, previous, maximum, *, status=PARTIAL_REFERENCE):
        return cls.messages(previous, [{Message.role.__name__: cls.REFERENCE_ROLE,
            Message.status.__name__: status, Message.content.__name__: value,
            cls.OFFSET: start, cls.TOTAL_CHARS: total}], maximum)

    @classmethod
    def has_truncation(cls, messages):
        return any(m[cls.TRUNCATED] for m in messages)


class ActiveMemorySummary:
    COUNT = "count"
    DIGEST = SummaryCursor.Field.DIGEST
    SUMMARY = SummaryCursor.Field.SUMMARY
    LATEST = "latest"

    def __init__(self, value):
        self._value = value

    def matches(self, prefix):
        return (self._value and self.count <= len(prefix)
                and MemoryText.digest(prefix[:self.count]) == self._value[self.DIGEST])

    @property
    def count(self):
        return self._value[self.COUNT]

    @property
    def summary(self):
        return self._value[self.SUMMARY]

    @classmethod
    def latest(cls, count, digest, summary):
        return {cls.LATEST: {cls.COUNT: count, cls.DIGEST: digest, cls.SUMMARY: summary}}


class MemoryStepObservation:
    COMPONENT = "component"
    DEGRADED = "degraded"
    ERROR = "error"
    ERROR_CODE = "error_code"
    FALLBACK = True

    @classmethod
    def metadata(cls, component, parent_step_id):
        return {cls.COMPONENT: component, AgentStepMetadata.PARENT_STEP_ID: parent_step_id}

    @classmethod
    def failure_output(cls, error):
        return {cls.DEGRADED: cls.FALLBACK, cls.ERROR: error}

    @classmethod
    def failure_metadata(cls, code):
        return {cls.DEGRADED: cls.FALLBACK, cls.ERROR_CODE: code}


class MemoryToolPreview:
    PREVIEW = "preview"
    TRUNCATED = MemorySummaryInput.TRUNCATED
    ORIGINAL_CHARS = ToolSummaryView.ORIGINAL_CHARS
    SOURCE = "source"
    SHA256 = "sha256"
    SHORTENED = True

    @classmethod
    def project(cls, content, maximum, reference, checksum):
        return {cls.PREVIEW: content[:maximum], cls.TRUNCATED: cls.SHORTENED,
                cls.ORIGINAL_CHARS: len(content), cls.SOURCE: {
                    MemorySource.Field.RUN_ID: reference.run_id, MemorySource.Field.STEP_ID: reference.step_id,
                    cls.SHA256: checksum}}


class MemoryProcessingObservation:
    SUMMARY = SummaryCursor.Field.SUMMARY
    TOOL_CALL_IDS = "tool_call_ids"
    SOURCE_DIGEST = "source_digest"
    MEMORIES = "memories"
    COMPRESSED_TOOL_CALLS = "compressed_tool_calls"
    MEMORY_ENTRIES = "memory_entries"
    SUMMARIZED_MESSAGES = "summarized_messages"

    @classmethod
    def active(cls, text, ids, checksum, metadata):
        result = {}
        result.update(**{cls.SUMMARY: text, cls.TOOL_CALL_IDS: list(ids), cls.SOURCE_DIGEST: checksum}, **metadata)
        return result

    @classmethod
    def recall(cls, records):
        return {cls.MEMORIES: [MemoryRecord(record).to_reference() for record in records]}

    @classmethod
    def compressed(cls, calls):
        return {cls.COMPRESSED_TOOL_CALLS: calls}

    @classmethod
    def context(cls, count, covered):
        return {cls.MEMORY_ENTRIES: count, cls.SUMMARIZED_MESSAGES: covered}
