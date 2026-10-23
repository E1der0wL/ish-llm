"""Explicit compaction options and durable cumulative summary cursors."""

from jsonschema import Draft202012Validator, ValidationError
from llm.core.json_data import JsonObject
from llm.core.schema import SchemaType, object_schema, field
from llm.providers.litellm import LiteLLMCompletionAdapter
from llm.providers.requests import provider_schema
from llm.services.infrastructure.storage import revision_token
from .configuration import MemoryProcessingConfig


class CompactionMode:
    REFERENCE = "reference"
    SUMMARY = "summary"
    VALUES = (REFERENCE, SUMMARY)


class CompactionOptions:
    ABSENT = None
    MINIMUM_KEEP_TURNS = 0
    MINIMUM_SIZE = 1
    MINIMUM_PROMPT_LENGTH = 1
    MINIMUM_TIMEOUT_EXCLUSIVE = 0
    AUXILIARY = True
    REQUIRE_MODEL = True
    INVALID_TOOL = "Invalid Tool summary options"
    INVALID_TOOL_COMPLETION = "Invalid stored Tool summary completion"

    class Field:
        METHOD = "method"
        KEEP_TURNS = MemoryProcessingConfig.Field.KEEP_TURNS
        SUMMARY_CHARS = MemoryProcessingConfig.Field.SUMMARY_CHARS
        MODEL_INPUT_CHARS = MemoryProcessingConfig.Field.MODEL_INPUT_CHARS
        MAX_SUMMARY_CALLS = MemoryProcessingConfig.Field.MAX_SUMMARY_CALLS
        TARGET_TOKENS = "target_tokens"
        PROMPT = "prompt"
        COMPLETION = MemoryProcessingConfig.Field.COMPLETION
        PROVIDER = MemoryProcessingConfig.Field.PROVIDER
        TIMEOUT_SECONDS = MemoryProcessingConfig.Field.TIMEOUT_SECONDS

    REQUIRED = (Field.METHOD, Field.KEEP_TURNS, Field.SUMMARY_CHARS, Field.MODEL_INPUT_CHARS,
                Field.MAX_SUMMARY_CALLS, Field.PROMPT, Field.COMPLETION)

    TOOL_SIZES = (Field.SUMMARY_CHARS, Field.MODEL_INPUT_CHARS, Field.MAX_SUMMARY_CALLS)

    def __init__(self, value):
        self._value = value

    @classmethod
    def schema(cls, *, completion_adapter=ABSENT):
        adapter = LiteLLMCompletionAdapter() if completion_adapter is cls.ABSENT else completion_adapter
        return object_schema({
            cls.Field.METHOD: field(SchemaType.STRING, enum=[CompactionMode.SUMMARY]),
            cls.Field.KEEP_TURNS: field(SchemaType.INTEGER, minimum=cls.MINIMUM_KEEP_TURNS),
            cls.Field.SUMMARY_CHARS: field(SchemaType.INTEGER, minimum=cls.MINIMUM_SIZE),
            cls.Field.MODEL_INPUT_CHARS: field(SchemaType.INTEGER, minimum=cls.MINIMUM_SIZE),
            cls.Field.MAX_SUMMARY_CALLS: field(SchemaType.INTEGER, minimum=cls.MINIMUM_SIZE),
            cls.Field.TARGET_TOKENS: field(SchemaType.INTEGER, minimum=cls.MINIMUM_SIZE),
            cls.Field.PROMPT: field(SchemaType.STRING, minLength=cls.MINIMUM_PROMPT_LENGTH),
            cls.Field.COMPLETION: adapter.describe_config(), cls.Field.PROVIDER: provider_schema(),
            cls.Field.TIMEOUT_SECONDS: field(SchemaType.NUMBER, exclusiveMinimum=cls.MINIMUM_TIMEOUT_EXCLUSIVE),
        }, required=list(cls.REQUIRED))

    @classmethod
    def validate(cls, options, *, completion_adapter=ABSENT):
        adapter = LiteLLMCompletionAdapter() if completion_adapter is cls.ABSENT else completion_adapter
        JsonObject.validate(options)
        Draft202012Validator(cls.schema(completion_adapter=adapter)).validate(options)
        adapter.validate_input_ownership(options[cls.Field.COMPLETION], auxiliary=cls.AUXILIARY)
        adapter.validate_request(options[cls.Field.COMPLETION], require_model=cls.REQUIRE_MODEL)

    @classmethod
    def validate_tool(cls, options, *, completion_adapter=ABSENT):
        if not isinstance(options.get(cls.Field.PROMPT), str) or not options[cls.Field.PROMPT]:
            raise ValueError(cls.INVALID_TOOL)
        for key in cls.TOOL_SIZES:
            if type(options.get(key)) is not int or options[key] < cls.MINIMUM_SIZE:
                raise ValueError(cls.INVALID_TOOL)
        try:
            adapter = LiteLLMCompletionAdapter() if completion_adapter is cls.ABSENT else completion_adapter
            adapter.validate_input_ownership(options.get(cls.Field.COMPLETION), auxiliary=cls.AUXILIARY)
            adapter.validate_request(options.get(cls.Field.COMPLETION), require_model=cls.REQUIRE_MODEL)
        except ValidationError as error:
            raise ValueError(cls.INVALID_TOOL_COMPLETION) from error

    @property
    def keep_turns(self):
        return self._value[self.Field.KEEP_TURNS]

    @property
    def summary_chars(self):
        return self._value[self.Field.SUMMARY_CHARS]

    @property
    def model_input_chars(self):
        return self._value[self.Field.MODEL_INPUT_CHARS]

    @property
    def max_summary_calls(self):
        return self._value[self.Field.MAX_SUMMARY_CALLS]

    @property
    def prompt(self):
        return self._value[self.Field.PROMPT]

    @property
    def has_target_tokens(self):
        return self.Field.TARGET_TOKENS in self._value

    def target_tokens_or(self, budget):
        return self._value.get(self.Field.TARGET_TOKENS, budget)

    @classmethod
    def from_tool_processing(cls, config, mode):
        values = config.tool_summary_options()
        if mode == CompactionMode.SUMMARY:
            values[cls.Field.PROMPT] = config.tool_summary_prompt
        return values


class SummaryCheckpoint:
    ABSENT = None
    INITIAL_OFFSET = 0
    INITIAL_CHUNKS = 0
    INVALID = "Invalid summary checkpoint integrity"

    class Field:
        FINGERPRINT = "fingerprint"
        OFFSET = "offset"
        SUMMARY = "summary"
        TOTAL_CHARS = "total_chars"
        COMPLETED_CHUNKS = "completed_chunks"
        DIGEST = "digest"

    FIELDS = frozenset((Field.FINGERPRINT, Field.OFFSET, Field.SUMMARY, Field.TOTAL_CHARS,
                        Field.COMPLETED_CHUNKS, Field.DIGEST))
    COUNTS = (Field.OFFSET, Field.TOTAL_CHARS, Field.COMPLETED_CHUNKS)
    MINIMUM_COUNT = 0

    def __init__(self, value):
        self._value = value

    @classmethod
    def validate(cls, value):
        f = cls.Field
        if (not isinstance(value, dict) or value.keys() != cls.FIELDS
                or not isinstance(value[f.FINGERPRINT], str) or not value[f.FINGERPRINT]
                or not isinstance(value[f.SUMMARY], str)
                or any(type(value[key]) is not int or value[key] < cls.MINIMUM_COUNT for key in cls.COUNTS)
                or value[f.OFFSET] > value[f.TOTAL_CHARS]
                or (value[f.OFFSET] > cls.INITIAL_OFFSET and (not value[f.SUMMARY].strip() or value[f.COMPLETED_CHUNKS] == cls.INITIAL_CHUNKS))
                or value[f.DIGEST] != revision_token({key: item for key, item in value.items() if key != f.DIGEST})):
            raise ValueError(cls.INVALID)

    @classmethod
    def create(cls, *, fingerprint, offset, summary, total_chars, completed_chunks):
        value = {cls.Field.FINGERPRINT: fingerprint, cls.Field.OFFSET: offset,
                 cls.Field.SUMMARY: summary, cls.Field.TOTAL_CHARS: total_chars,
                 cls.Field.COMPLETED_CHUNKS: completed_chunks}
        value[cls.Field.DIGEST] = revision_token(value)
        return value

    @property
    def fingerprint(self):
        return self._value[self.Field.FINGERPRINT]

    @property
    def offset(self):
        return self._value[self.Field.OFFSET]

    @property
    def summary(self):
        return self._value[self.Field.SUMMARY]

    @property
    def total_chars(self):
        return self._value[self.Field.TOTAL_CHARS]

    @property
    def completed_chunks(self):
        return self._value[self.Field.COMPLETED_CHUNKS]
