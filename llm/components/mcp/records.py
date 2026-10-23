"""MCP definitions, explicit selections and discovery evidence; no SDK or storage I/O."""

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum

from llm.core.contracts import JsonValue
from llm.core.implementation_parameters import ImplementationParameters
from llm.core.schema import SchemaKeyword as K, SchemaType as T, SchemaBoolean as B
from llm.core.schema import metadata_schema, object_schema, implementation_schema, field
from llm.core.tool_contracts import ToolContract, ToolClassification


class MCPTransport(StrEnum):
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"
    SSE = "sse"


class MCPDefinition:
    CAPABILITY = "mcp"

    class Field(StrEnum):
        TRANSPORT = "transport"
        METADATA = "metadata"
        COMMAND = "command"
        ARGS = "args"
        ENV = "env"
        URL = "url"
        HEADERS = "headers"

    MINIMUM_COMMAND = 1
    URL_PATTERN = r"^https?://[^/\s]+"
    AUTHENTICATION_FIELDS = "MCP credentials may supply only transport authentication fields"
    AUTHENTICATION_VALUES = "MCP credentials must be string mappings"
    SCHEMA = {
        K.TYPE: T.OBJECT, K.REQUIRED: [Field.TRANSPORT.value], K.ADDITIONAL_PROPERTIES: B.REJECT,
        K.PROPERTIES: {
            Field.TRANSPORT.value: {K.ENUM: [v.value for v in MCPTransport]},
            Field.METADATA.value: metadata_schema(),
            Field.COMMAND.value: {K.TYPE: T.STRING, K.MIN_LENGTH: MINIMUM_COMMAND},
            Field.ARGS.value: {K.TYPE: T.ARRAY, K.ITEMS: {K.TYPE: T.STRING}},
            Field.ENV.value: {K.TYPE: T.OBJECT, K.ADDITIONAL_PROPERTIES: {K.TYPE: T.STRING}},
            Field.URL.value: {K.TYPE: T.STRING, K.PATTERN: URL_PATTERN},
            Field.HEADERS.value: {K.TYPE: T.OBJECT, K.ADDITIONAL_PROPERTIES: {K.TYPE: T.STRING}},
        },
        K.ALL_OF: [{K.IF: {K.PROPERTIES: {Field.TRANSPORT.value: {K.CONST: MCPTransport.STDIO.value}}},
                    K.THEN: {K.REQUIRED: [Field.COMMAND.value]}, K.ELSE: {K.REQUIRED: [Field.URL.value]}}],
    }
    STDIO_FIELDS = (Field.COMMAND, Field.ARGS, Field.ENV)
    ABSENT_HEADERS = None

    def __init__(self, value):
        self._value = deepcopy(value)

    @property
    def transport(self):
        return self._value[self.Field.TRANSPORT.value]

    @property
    def url(self):
        return self._value[self.Field.URL.value]

    @property
    def headers(self):
        return deepcopy(self._value.get(self.Field.HEADERS.value, self.ABSENT_HEADERS))

    def stdio_parameters(self):
        return {key.value: deepcopy(self._value[key.value]) for key in self.STDIO_FIELDS if key.value in self._value}

    def with_credentials(self, supplied):
        allowed = {self.Field.ENV} if self.transport == MCPTransport.STDIO else {self.Field.HEADERS}
        if not isinstance(supplied, dict) or set(supplied) - allowed:
            raise ValueError(self.AUTHENTICATION_FIELDS)
        value = self.to_dict()
        for key, values in supplied.items():
            if not isinstance(values, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in values.items()):
                raise ValueError(self.AUTHENTICATION_VALUES)
            value[key] = {**value.get(key, {}), **values}
        return type(self)(value)

    def to_dict(self):
        return deepcopy(self._value)


@dataclass(frozen=True, slots=True)
class MCPToolSelection(JsonValue):
    SERVER_PATTERN = "^[A-Za-z0-9_-]{1,64}$"
    MINIMUM_TOOL_NAME = 1

    server: str
    tool: str

    @classmethod
    def schema(cls):
        return object_schema({cls.server.__name__: field(T.STRING, **{K.PATTERN: cls.SERVER_PATTERN}),
                              cls.tool.__name__: field(T.STRING, **{K.MIN_LENGTH: cls.MINIMUM_TOOL_NAME})},
                             **{K.REQUIRED: [cls.server.__name__, cls.tool.__name__]})


class MCPConfiguration:
    TOOLS = "tools"
    ALIAS_PATTERN = MCPToolSelection.SERVER_PATTERN

    @classmethod
    def schema(cls):
        return implementation_schema(config=object_schema({cls.TOOLS: object_schema(
            **{K.PATTERN_PROPERTIES: {cls.ALIAS_PATTERN: MCPToolSelection.schema()}})}))

    @classmethod
    def selections(cls, value):
        return {alias: MCPToolSelection.from_dict(selection) for alias, selection in
                ImplementationParameters.read(value).config.get(cls.TOOLS, {}).items()}


@dataclass(frozen=True, slots=True)
class MCPToolSnapshot(JsonValue):
    NO_CLASSIFICATION = None

    name: str
    description: str
    parameters: dict
    contract: dict
    classification: dict | None

    @classmethod
    def from_tool(cls, tool):
        return cls(tool.name, tool.description, tool.parameters, tool.contract.to_dict(),
                   tool.classification.to_dict() if tool.classification is not cls.NO_CLASSIFICATION else cls.NO_CLASSIFICATION)

    def __post_init__(self):
        JsonValue.__post_init__(self)
        self.execution_contract
        self.execution_classification

    @property
    def execution_contract(self):
        return ToolContract.from_dict(self.contract)

    @property
    def execution_classification(self):
        return ToolClassification.from_dict(self.classification) if self.classification is not self.NO_CLASSIFICATION else self.NO_CLASSIFICATION


@dataclass(frozen=True, slots=True)
class MCPCatalog(JsonValue):
    NO_SELECTION = None

    version: str
    connector: str
    tools: tuple[MCPToolSnapshot, ...]

    def matches(self, version, revision):
        return (self.version, self.connector) == (version, revision)

    def selected(self, name):
        return next((item for item in self.tools if item.name == name), self.NO_SELECTION)

    def tool_records(self):
        return [tool.to_dict() for tool in self.tools]


@dataclass(frozen=True, slots=True)
class MCPToolBinding(JsonValue):
    component: str
    server: str
    version: str
    connector: str
    tool: str

    @classmethod
    def create(cls, server, catalog, descriptor_revision):
        return cls(MCPDefinition.CAPABILITY, server, catalog.version, catalog.connector, descriptor_revision)


@dataclass(frozen=True, slots=True)
class MCPRuntime:
    UNAVAILABLE = "MCP connector is unavailable: {server}"
    NO_CONNECTOR = None

    connector: object
    revision: str

    def include(self, source):
        return {**source, type(self).connector.__name__: self.connector, type(self).revision.__name__: self.revision}

    @classmethod
    def selected(cls, source, server):
        connector = source.get(cls.connector.__name__)
        if not callable(connector):
            raise ValueError(cls.UNAVAILABLE.format(server=server))
        return cls(connector, source[cls.revision.__name__])


@dataclass(frozen=True, slots=True)
class MCPConnection:
    definition: dict
    version: str
    connector: object
    revision: str
