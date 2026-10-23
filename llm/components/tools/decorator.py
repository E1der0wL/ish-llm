"""Python main 함수의 실행 선언. 함수를 감싸거나 이름/스키마를 복제하지 않는다."""

from types import MappingProxyType
from llm.core.tool_contracts import ToolContract
from llm.core.model_protocol import ToolDefinition


class ToolMetadata:
    """One declaration/reader for trusted Python function execution metadata."""

    CONTRACT_ATTRIBUTE = "__tool_contract__"
    OPTIONS_ATTRIBUTE = "__tool_options__"
    UNSET = object()
    ABSENT = None
    INVALID_CONTRACT = "tool requires an explicit ToolContract"
    INVALID_STRICT = "strict must be boolean"
    ALREADY_DECLARED = "Tool metadata is already declared"
    MISSING_CONTRACT = "Tool main() requires @tool metadata"

    @classmethod
    def options(cls, contract, strict):
        if not isinstance(contract, ToolContract):
            raise TypeError(cls.INVALID_CONTRACT)
        if strict is not cls.UNSET and type(strict) is not bool:
            raise TypeError(cls.INVALID_STRICT)
        return MappingProxyType({} if strict is cls.UNSET else {ToolDefinition.strict.__name__: strict})

    @classmethod
    def attach(cls, function, contract, options):
        if hasattr(function, cls.CONTRACT_ATTRIBUTE):
            raise ValueError(cls.ALREADY_DECLARED)
        setattr(function, cls.CONTRACT_ATTRIBUTE, contract)
        setattr(function, cls.OPTIONS_ATTRIBUTE, options)
        return function

    @classmethod
    def contract(cls, function):
        contract = getattr(function, cls.CONTRACT_ATTRIBUTE, cls.ABSENT)
        if not isinstance(contract, ToolContract):
            raise ValueError(cls.MISSING_CONTRACT)
        return contract

    @classmethod
    def definition(cls, function, name, description, parameters):
        return ToolDefinition(name, description, parameters, **getattr(function, cls.OPTIONS_ATTRIBUTE, {}))


def tool(contract: ToolContract, *, strict=ToolMetadata.UNSET):
    """ToolContract를 명시한다. strict는 지정한 경우에만 provider 정의에 포함한다."""
    options = ToolMetadata.options(contract, strict)
    def declare(function):
        return ToolMetadata.attach(function, contract, options)
    return declare
