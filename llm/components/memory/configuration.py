"""Memory processing configuration, validation and typed option access."""

from copy import deepcopy
import math
from llm.core.implementation_parameters import ImplementationParameters, ParameterLookup
from llm.core.parameters import ParameterLayout
from llm.core.schema import SchemaKeyword, SchemaType, SchemaBoolean, object_schema, field
from llm.providers.litellm import LiteLLMCompletionAdapter
from llm.providers.schema import ModelParameters
from .work_state import WorkState


class MemoryProcessingConfig:
    KEY = "processing"
    ABSENT = None
    MINIMUM_POSITIVE = 1
    MINIMUM_PROMPT_LENGTH = 1
    MINIMUM_NONNEGATIVE = 0
    AUXILIARY = True
    IDENTIFIER_PATTERN = r"^[a-zA-Z0-9_-]{1,64}$"
    RAISE = "raise"
    CONTINUE = "continue"
    FAILURE_MODES = (RAISE, CONTINUE)
    TEXT = "text"
    WORK_STATE = WorkState.KEY
    SUMMARY_FORMATS = (TEXT, WORK_STATE)
    INVALID_INTEGER = "Memory {key} must be an integer"
    REQUIRED_OPTION = "Missing required setting: memory.processing.{key}"
    INVALID_OBJECT = 'Memory processing must be an object'
    INVALID_CONFIGURATION = 'Invalid Memory processing: '
    MISSING_COUNTER = 'context_tokens requires an injected token_counter(text)'
    INVALID_TIMEOUT = 'Memory timeout must be positive and finite'
    MISSING_PRIORITY = 'Missing required setting: memory.processing.priority'
    MISSING_THRESHOLD = 'Memory summary requires summary_after_chars or summary_after_tokens'
    CONFLICTING_TOOL_MODES = 'Select either Tool preview or tool_result_mode'
    MISSING_TOOL_MODEL = 'Tool summary requires completion.model'

    class Field:
        COMPLETION = 'completion'
        PRIORITY = 'priority'
        EXTRACT_SCOPE = 'extract_scope'
        EXTRACT_PROMPT_ID = 'extract_prompt_id'
        SUMMARY_CHARS = 'summary_chars'
        RECALL_LIMIT = 'recall_limit'
        RECALL_QUERY_CHARS = 'recall_query_chars'
        SUMMARY_FORMAT = 'summary_format'
        GOAL_IDS = 'goal_ids'
        TOOL_RESULT_MODE = 'tool_result_mode'
        TOOL_DATA = 'tool_data'
        TOOL_SUMMARY_PROMPT = 'tool_summary_prompt'
        RECALL = 'recall'
        SUMMARIZE = 'summarize'
        EXTRACT = 'extract'
        COMPRESS_TOOLS = 'compress_tools'
        NESTED_PROCESSING = 'nested_processing'
        COMPACT_ACTIVE = 'compact_active'
        KEEP_TURNS = 'keep_turns'
        SUMMARY_AFTER_CHARS = 'summary_after_chars'
        SUMMARY_AFTER_TOKENS = 'summary_after_tokens'
        CONTEXT_CHARS = 'context_chars'
        TOOL_RESULT_CHARS = 'tool_result_chars'
        MODEL_INPUT_CHARS = 'model_input_chars'
        MAX_CANDIDATES = 'max_candidates'
        ACTIVE_KEEP_ITERATIONS = 'active_keep_iterations'
        MAX_SUMMARY_CALLS = 'max_summary_calls'
        MAX_OUTPUT_CHARS = 'max_output_chars'
        CONTEXT_TOKENS = 'context_tokens'
        RECALL_EVERY = 'recall_every'
        FAILURE_MODE = 'failure_mode'
        TIMEOUT_SECONDS = 'timeout_seconds'
        PROVIDER = 'provider'

    CONFIG_FIELDS = (Field.COMPLETION, Field.PRIORITY, Field.EXTRACT_SCOPE, Field.EXTRACT_PROMPT_ID, Field.SUMMARY_CHARS, Field.RECALL_LIMIT, Field.RECALL_QUERY_CHARS, Field.SUMMARY_FORMAT, Field.GOAL_IDS, Field.TOOL_RESULT_MODE, Field.TOOL_DATA, Field.TOOL_SUMMARY_PROMPT,)

    POLICY_FIELDS = (Field.RECALL, Field.SUMMARIZE, Field.EXTRACT, Field.COMPRESS_TOOLS, Field.NESTED_PROCESSING, Field.COMPACT_ACTIVE, Field.KEEP_TURNS, Field.SUMMARY_AFTER_CHARS, Field.SUMMARY_AFTER_TOKENS, Field.CONTEXT_CHARS, Field.TOOL_RESULT_CHARS, Field.MODEL_INPUT_CHARS, Field.MAX_CANDIDATES, Field.ACTIVE_KEEP_ITERATIONS, Field.MAX_SUMMARY_CALLS, Field.MAX_OUTPUT_CHARS, Field.CONTEXT_TOKENS, Field.RECALL_EVERY, Field.FAILURE_MODE, Field.TIMEOUT_SECONDS, Field.PROVIDER,)

    PRIORITY_FEATURES = (Field.RECALL, Field.SUMMARIZE, Field.EXTRACT, Field.COMPRESS_TOOLS)
    BOOLEAN_FIELDS = (*PRIORITY_FEATURES, Field.NESTED_PROCESSING, Field.COMPACT_ACTIVE)
    POSITIVE_FIELDS = (Field.KEEP_TURNS, Field.SUMMARY_AFTER_CHARS, Field.SUMMARY_AFTER_TOKENS,
        Field.SUMMARY_CHARS, Field.CONTEXT_CHARS, Field.RECALL_LIMIT, Field.TOOL_RESULT_CHARS,
        Field.MODEL_INPUT_CHARS, Field.MAX_CANDIDATES, Field.ACTIVE_KEEP_ITERATIONS,
        Field.MAX_SUMMARY_CALLS, Field.RECALL_QUERY_CHARS, Field.MAX_OUTPUT_CHARS)
    SUMMARY_THRESHOLDS = (Field.SUMMARY_AFTER_CHARS, Field.SUMMARY_AFTER_TOKENS)
    TOOL_REQUIRED = (Field.PRIORITY, Field.TOOL_DATA, Field.TOOL_RESULT_CHARS)
    TOOL_SUMMARY_REQUIRED = (Field.COMPLETION, Field.TOOL_SUMMARY_PROMPT, Field.MODEL_INPUT_CHARS,
                             Field.SUMMARY_CHARS, Field.MAX_SUMMARY_CALLS)
    REQUIREMENTS = {
        Field.RECALL: (Field.RECALL_LIMIT, Field.RECALL_QUERY_CHARS, Field.CONTEXT_CHARS),
        Field.COMPRESS_TOOLS: (Field.TOOL_RESULT_CHARS,),
        Field.SUMMARIZE: (Field.KEEP_TURNS, Field.SUMMARY_CHARS, Field.MODEL_INPUT_CHARS,
                          Field.MAX_SUMMARY_CALLS, Field.CONTEXT_CHARS),
        Field.EXTRACT: (Field.MODEL_INPUT_CHARS, Field.MAX_CANDIDATES, Field.SUMMARY_CHARS, Field.EXTRACT_SCOPE),
    }

    TOOL_SUMMARY_OPTIONS = (Field.COMPLETION, Field.PROVIDER, Field.SUMMARY_CHARS,
        Field.MODEL_INPUT_CHARS, Field.MAX_SUMMARY_CALLS, Field.TIMEOUT_SECONDS, Field.MAX_OUTPUT_CHARS)

    def __init__(self, value):
        self._value = value

    @classmethod
    def resolve(cls, configuration, *, token_counter=ABSENT, completion_adapter=ABSENT):
        from .component import MemoryComponent
        from .processing import MemoryProcessor
        value = MemoryComponent.parameter_layout.unpack(configuration).get(cls.KEY, {})
        if not isinstance(value, dict):
            raise ValueError(cls.INVALID_OBJECT)
        config = deepcopy(value)
        adapter = LiteLLMCompletionAdapter() if completion_adapter is cls.ABSENT else completion_adapter
        schema = MemoryProcessor.describe_config(completion_adapter=adapter)
        for key, field in schema[SchemaKeyword.PROPERTIES].items():
            types = field.get(SchemaKeyword.TYPE)
            if types == SchemaType.INTEGER or (isinstance(types, list) and SchemaType.INTEGER in types):
                if key in config and config[key] is not cls.ABSENT and (type(config[key]) is not int):
                    raise ValueError(cls.INVALID_INTEGER.format(key=key))
        from jsonschema import Draft202012Validator
        error = next(Draft202012Validator(schema).iter_errors(config), cls.ABSENT)
        if error is not cls.ABSENT:
            raise ValueError(cls.INVALID_CONFIGURATION + error.message)
        from llm.providers.requests import resolve_provider_options
        provider = config.get(cls.Field.PROVIDER)
        resolve_provider_options({} if provider is cls.ABSENT else provider)
        if config.get(cls.Field.CONTEXT_TOKENS) is not cls.ABSENT and token_counter is cls.ABSENT:
            raise ValueError(cls.MISSING_COUNTER)
        duration = config.get(cls.Field.TIMEOUT_SECONDS)
        if duration is not cls.ABSENT and (isinstance(duration, bool) or not isinstance(duration, (int, float)) or (not math.isfinite(duration)) or (duration <= cls.MINIMUM_NONNEGATIVE)):
            raise ValueError(cls.INVALID_TIMEOUT)
        params = config.get(cls.Field.COMPLETION, {})
        adapter.validate_input_ownership(params, auxiliary=cls.AUXILIARY)
        adapter.validate_request(params, require_model=bool(config.get(cls.Field.SUMMARIZE) or config.get(cls.Field.EXTRACT)))
        if any((config.get(key) for key in cls.PRIORITY_FEATURES)) and cls.Field.PRIORITY not in config:
            raise ValueError(cls.MISSING_PRIORITY)
        requirements = dict(cls.REQUIREMENTS)
        if config.get(cls.Field.SUMMARIZE) and config.get(cls.Field.COMPACT_ACTIVE):
            requirements[cls.Field.SUMMARIZE] += (cls.Field.ACTIVE_KEEP_ITERATIONS,)
        if config.get(cls.Field.SUMMARIZE) and (not any((key in config for key in cls.SUMMARY_THRESHOLDS))):
            raise ValueError(cls.MISSING_THRESHOLD)
        for feature, keys in requirements.items():
            if config.get(feature):
                for key in keys:
                    if config.get(key) is cls.ABSENT:
                        raise ValueError(cls.REQUIRED_OPTION.format(key=key))
        if cls.Field.TOOL_RESULT_MODE in config:
            if config.get(cls.Field.COMPRESS_TOOLS):
                raise ValueError(cls.CONFLICTING_TOOL_MODES)
            required = list(cls.TOOL_REQUIRED)
            from .compaction import Compactions
            if config[cls.Field.TOOL_RESULT_MODE] == Compactions.SUMMARY:
                required += cls.TOOL_SUMMARY_REQUIRED
                if not ModelParameters(params).optional_model:
                    raise ValueError(cls.MISSING_TOOL_MODEL)
            for key in required:
                if config.get(key) is cls.ABSENT:
                    raise ValueError(cls.REQUIRED_OPTION.format(key=key))
        return config

    @classmethod
    def describe(cls, *, completion_adapter=ABSENT, declaration=ABSENT):
        public = cls if declaration is cls.ABSENT else declaration
        adapter = LiteLLMCompletionAdapter() if completion_adapter is cls.ABSENT else completion_adapter
        from llm.providers.requests import provider_schema
        from .component import MemoryComponent
        from .compaction import Compactions
        properties = {name: field(SchemaType.BOOLEAN) for name in cls.BOOLEAN_FIELDS}
        properties.update({name: field(SchemaType.INTEGER, minimum=cls.MINIMUM_POSITIVE)
                           for name in cls.POSITIVE_FIELDS})
        properties.update({
            cls.Field.PRIORITY: field(SchemaType.INTEGER),
            cls.Field.RECALL_EVERY: field(SchemaType.INTEGER, minimum=cls.MINIMUM_NONNEGATIVE),
            cls.Field.TOOL_RESULT_MODE: field(SchemaType.STRING, enum=list(Compactions.MODES)),
            cls.Field.TOOL_DATA: MemoryToolData.schema(),
            cls.Field.TOOL_SUMMARY_PROMPT: field(SchemaType.STRING, minLength=cls.MINIMUM_PROMPT_LENGTH),
            cls.Field.SUMMARY_FORMAT: field(SchemaType.STRING, enum=list(public.SUMMARY_FORMATS)),
            cls.Field.GOAL_IDS: {SchemaKeyword.TYPE: SchemaType.ARRAY, SchemaKeyword.UNIQUE_ITEMS: SchemaBoolean.ACCEPT,
                               SchemaKeyword.ITEMS: field(SchemaType.STRING, pattern=cls.IDENTIFIER_PATTERN)},
            cls.Field.CONTEXT_TOKENS: field([SchemaType.INTEGER, SchemaType.NULL], minimum=cls.MINIMUM_POSITIVE),
            cls.Field.COMPLETION: adapter.describe_config(),
            cls.Field.PROVIDER: {**provider_schema(), SchemaKeyword.TYPE: [SchemaType.OBJECT, SchemaType.NULL]},
            cls.Field.EXTRACT_SCOPE: field(SchemaType.STRING, enum=[MemoryComponent.SESSION_SCOPE, MemoryComponent.PROJECT_SCOPE]),
            cls.Field.EXTRACT_PROMPT_ID: field(SchemaType.STRING, pattern=cls.IDENTIFIER_PATTERN),
            cls.Field.FAILURE_MODE: field(SchemaType.STRING, enum=list(public.FAILURE_MODES)),
            cls.Field.TIMEOUT_SECONDS: field([SchemaType.NUMBER, SchemaType.NULL], exclusiveMinimum=cls.MINIMUM_NONNEGATIVE),
        })
        return object_schema(properties)

    @property
    def active_keep_iterations(self):
        return self._value[self.Field.ACTIVE_KEEP_ITERATIONS]

    @property
    def compact_active(self):
        return self._value.get(self.Field.COMPACT_ACTIVE)

    @property
    def completion(self):
        return self._value[self.Field.COMPLETION]

    @property
    def compress_tools(self):
        return self._value.get(self.Field.COMPRESS_TOOLS)

    @property
    def context_chars(self):
        return self._value[self.Field.CONTEXT_CHARS]

    @property
    def context_tokens(self):
        return self._value.get(self.Field.CONTEXT_TOKENS)

    @property
    def extract(self):
        return self._value.get(self.Field.EXTRACT)

    @property
    def extract_prompt_id(self):
        return self._value.get(self.Field.EXTRACT_PROMPT_ID)

    @property
    def extract_scope(self):
        return self._value[self.Field.EXTRACT_SCOPE]

    @property
    def failure_mode(self):
        return self._value.get(self.Field.FAILURE_MODE)

    @property
    def goal_ids(self):
        return self._value.get(self.Field.GOAL_IDS, [])

    @property
    def keep_turns(self):
        return self._value[self.Field.KEEP_TURNS]

    @property
    def max_candidates(self):
        return self._value[self.Field.MAX_CANDIDATES]

    @property
    def max_output_chars(self):
        return self._value.get(self.Field.MAX_OUTPUT_CHARS)

    @property
    def max_summary_calls(self):
        return self._value[self.Field.MAX_SUMMARY_CALLS]

    @property
    def model_input_chars(self):
        return self._value[self.Field.MODEL_INPUT_CHARS]

    @property
    def nested_processing(self):
        return self._value.get(self.Field.NESTED_PROCESSING)

    @property
    def provider(self):
        return self._value.get(self.Field.PROVIDER)

    @property
    def recall(self):
        return self._value.get(self.Field.RECALL)

    @property
    def recall_every(self):
        return self._value.get(self.Field.RECALL_EVERY)

    @property
    def optional_recall_limit(self):
        return self._value.get(self.Field.RECALL_LIMIT)

    @property
    def recall_limit(self):
        return self._value[self.Field.RECALL_LIMIT]

    @property
    def recall_query_chars(self):
        return self._value[self.Field.RECALL_QUERY_CHARS]

    @property
    def summarize(self):
        return self._value.get(self.Field.SUMMARIZE)

    @property
    def summary_after_chars(self):
        return self._value[self.Field.SUMMARY_AFTER_CHARS]

    @property
    def summary_after_tokens(self):
        return self._value[self.Field.SUMMARY_AFTER_TOKENS]

    @property
    def summary_chars(self):
        return self._value[self.Field.SUMMARY_CHARS]

    @property
    def summary_format(self):
        return self._value.get(self.Field.SUMMARY_FORMAT)

    @property
    def timeout_seconds(self):
        return self._value.get(self.Field.TIMEOUT_SECONDS)

    @property
    def tool_result_chars(self):
        return self._value[self.Field.TOOL_RESULT_CHARS]

    @property
    def has_summary_after_tokens(self):
        return self.Field.SUMMARY_AFTER_TOKENS in self._value

    @property
    def has_summary_after_chars(self):
        return self.Field.SUMMARY_AFTER_CHARS in self._value

    @property
    def has_tool_result_mode(self):
        return self.Field.TOOL_RESULT_MODE in self._value


    @classmethod
    def from_config_section(cls, configuration):
        return cls(ImplementationParameters.read(configuration).config.get(cls.KEY, {}))

    @property
    def tool_data(self):
        return MemoryToolData(self._value[self.Field.TOOL_DATA])

    def priority_or(self, default):
        return self._value.get(self.Field.PRIORITY, default)

    @property
    def tool_result_mode(self):
        return self._value.get(self.Field.TOOL_RESULT_MODE)

    @property
    def tool_summary_prompt(self):
        return self._value[self.Field.TOOL_SUMMARY_PROMPT]

    @property
    def tool_data_record(self):
        return self._value[self.Field.TOOL_DATA]

    def tool_summary_options(self):
        return {key: deepcopy(self._value[key]) for key in self.TOOL_SUMMARY_OPTIONS if key in self._value}


class MemoryToolData:
    MINIMUM_TEXT_LENGTH = 1

    class Field:
        COMPONENT = "component"
        IDENTIFIER = "identifier"

    @classmethod
    def schema(cls):
        return object_schema({cls.Field.COMPONENT: field(SchemaType.STRING, minLength=cls.MINIMUM_TEXT_LENGTH),
                              cls.Field.IDENTIFIER: field(SchemaType.STRING, minLength=cls.MINIMUM_TEXT_LENGTH)},
                             required=[cls.Field.COMPONENT, cls.Field.IDENTIFIER])

    def __init__(self, value):
        self._value = value

    @property
    def component(self):
        return self._value[self.Field.COMPONENT]

    @property
    def identifier(self):
        return self._value[self.Field.IDENTIFIER]


class MemoryConfiguration:
    UNCACHED = 0
    MINIMUM_RESULT_LIMIT = 1
    ABSENT = None
    KEYWORD = "keyword"
    INVALID_CONFIGURATION = "Invalid memory configuration: "
    EXCEEDED_SEARCH_LIMIT = "Memory search_limit exceeds max_search_results"
    MISSING_WRITE_STATUS = "Missing required setting: memory.tool_write_status"

    class Field:
        CACHE_RECORDS = "cache_records"
        SEARCH_STATUS = "search_status"
        SEARCH_LIMIT = "search_limit"
        SEARCH_STRATEGY = "search_strategy"
        MIN_SCORE = "min_score"
        TOOL_WRITE_STATUS = "tool_write_status"
        MAX_SEARCH_RESULTS = "max_search_results"

    CONFIG_FIELDS = (Field.CACHE_RECORDS, Field.SEARCH_STATUS, Field.SEARCH_LIMIT, Field.SEARCH_STRATEGY, Field.MIN_SCORE)
    POLICY_FIELDS = (Field.TOOL_WRITE_STATUS, Field.MAX_SEARCH_RESULTS)

    def __init__(self, value):
        self._value = value

    @classmethod
    def layout(cls):
        prefix = MemoryProcessingConfig.KEY + ParameterLookup.SEPARATOR
        return ParameterLayout(config=cls.CONFIG_FIELDS, policy=cls.POLICY_FIELDS,
            paths={**{prefix + key: ImplementationParameters.CONFIG + ParameterLookup.SEPARATOR + prefix + key
                      for key in MemoryProcessingConfig.CONFIG_FIELDS},
                   **{prefix + key: ImplementationParameters.POLICY + ParameterLookup.SEPARATOR + prefix + key
                      for key in MemoryProcessingConfig.POLICY_FIELDS}})

    @classmethod
    def schema(cls, *, statuses, all_status, keyword, processing_schema):
        return object_schema({
            cls.Field.CACHE_RECORDS: field(SchemaType.INTEGER, minimum=cls.UNCACHED),
            cls.Field.TOOL_WRITE_STATUS: field(SchemaType.STRING, enum=list(statuses)),
            cls.Field.SEARCH_STATUS: field(SchemaType.STRING, enum=[*statuses, all_status]),
            cls.Field.SEARCH_STRATEGY: field(SchemaType.STRING, enum=[keyword]),
            cls.Field.MIN_SCORE: field(SchemaType.NUMBER),
            cls.Field.SEARCH_LIMIT: field(SchemaType.INTEGER, minimum=cls.MINIMUM_RESULT_LIMIT),
            cls.Field.MAX_SEARCH_RESULTS: field(SchemaType.INTEGER, minimum=cls.MINIMUM_RESULT_LIMIT),
            MemoryProcessingConfig.KEY: processing_schema()})

    @classmethod
    def from_policy_section(cls, configuration):
        return cls(ImplementationParameters.read(configuration).policy)

    def validate_search_limits(self):
        if (self._value.get(self.Field.SEARCH_LIMIT) is not self.ABSENT
                and self._value.get(self.Field.MAX_SEARCH_RESULTS) is not self.ABSENT
                and self._value[self.Field.SEARCH_LIMIT] > self._value[self.Field.MAX_SEARCH_RESULTS]):
            raise ValueError(self.EXCEEDED_SEARCH_LIMIT)

    @property
    def cache_records(self):
        return self._value.get(self.Field.CACHE_RECORDS, self.UNCACHED)

    @property
    def search_status(self):
        return self._value.get(self.Field.SEARCH_STATUS)

    @property
    def search_limit(self):
        return self._value.get(self.Field.SEARCH_LIMIT)

    @property
    def search_strategy(self):
        return self._value.get(self.Field.SEARCH_STRATEGY)

    @property
    def maximum_results(self):
        return self._value.get(self.Field.MAX_SEARCH_RESULTS)

    @property
    def required_maximum_results(self):
        return self._value[self.Field.MAX_SEARCH_RESULTS]

    @property
    def has_min_score(self):
        return self.Field.MIN_SCORE in self._value

    @property
    def min_score(self):
        return self._value[self.Field.MIN_SCORE]

    @property
    def has_tool_write_status(self):
        return self.Field.TOOL_WRITE_STATUS in self._value

    @property
    def tool_write_status(self):
        return self._value[self.Field.TOOL_WRITE_STATUS]
