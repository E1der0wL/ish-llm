"""Hub matching projections deliberately exclude risk, scope and grant evidence."""

from dataclasses import dataclass, fields, replace
from copy import deepcopy

from llm.core.contracts import JsonValue
from llm.services.runtime.tool_approval_records import ToolApprovalActionView
from llm.core.schema import SchemaKeyword as K, SchemaType as T
from llm.components.tools.builtin.process_values import ShellArguments


@dataclass(frozen=True, slots=True)
class MatchedToolAction(JsonValue):
    tool: str
    arguments: dict
    contract: dict
    policy: dict

    @classmethod
    def names(cls):
        return tuple(item.name for item in fields(cls))

    @classmethod
    def capture(cls, value):
        if isinstance(value, cls):
            return cls.from_dict(value.to_dict())
        native = ToolApprovalActionView(value)
        return cls(native.tool, native.arguments, native.contract, native.policy)

    def with_arguments(self, arguments):
        return replace(self, arguments=arguments)


@dataclass(frozen=True, slots=True)
class CommandRangeConfig(JsonValue):
    ACTION_SCHEMA = {K.TYPE: T.OBJECT, K.ADDITIONAL_PROPERTIES: False,
        K.REQUIRED: list(MatchedToolAction.names()), K.PROPERTIES: {
            MatchedToolAction.tool.__name__: {K.CONST: ShellArguments.NAME},
            MatchedToolAction.arguments.__name__: {K.TYPE: T.OBJECT,
                K.REQUIRED: [ShellArguments.command.__name__],
                K.PROPERTIES: {ShellArguments.command.__name__: {K.TYPE: T.STRING}}},
            MatchedToolAction.contract.__name__: {K.TYPE: T.OBJECT},
            MatchedToolAction.policy.__name__: {K.TYPE: T.OBJECT}}}
    PREFIX_SCHEMA = {K.TYPE: T.ARRAY, K.MIN_ITEMS: 1, K.ITEMS: {
        K.TYPE: T.ARRAY, K.MIN_ITEMS: 1, K.ITEMS: {K.TYPE: T.STRING}}}

    action: MatchedToolAction
    prefixes: tuple[tuple[str, ...], ...]

    @classmethod
    def schema(cls):
        return {K.TYPE: T.OBJECT, K.ADDITIONAL_PROPERTIES: False,
            K.REQUIRED: [item.name for item in fields(cls)], K.PROPERTIES: {
                cls.action.__name__: deepcopy(cls.ACTION_SCHEMA), cls.prefixes.__name__: deepcopy(cls.PREFIX_SCHEMA)}}
