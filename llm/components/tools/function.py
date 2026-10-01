"""선언한 함수의 signature/type hints/docstring을 기존 Tool 계약으로 변환한다."""

from copy import deepcopy
import inspect
import types
import typing

from pydantic import TypeAdapter
from llm.core.models import ProjectConfig
from .registry import Tool, ToolContract, ToolRegistry


def _supported(annotation):
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if annotation in (str, int, float, bool, type(None)):
        return
    if origin is typing.Annotated:
        _supported(args[0])
        return
    if origin in (typing.Union, getattr(types, "UnionType", typing.Union)):
        for item in args:
            _supported(item)
        return
    if origin is typing.Literal and args and all(type(v) in (str, int, float, bool, type(None)) for v in args):
        ProjectConfig.validate_settings({"values": list(args)})
        return
    if origin is list and len(args) == 1:
        _supported(args[0])
        return
    if origin is dict and len(args) == 2 and args[0] is str:
        _supported(args[1])
        return
    raise ValueError("Unsupported Tool parameter annotation")


def function_tool(name, main) -> Tool:
    """출력은 기존 ToolExecutor가 검증한다. 입력은 선언한 JSON 타입을 엄격하게 반영한다."""
    if not inspect.iscoroutinefunction(main):
        raise ValueError("Tool requires async main()")
    contract = getattr(main, "__tool_contract__", None)
    if not isinstance(contract, ToolContract):
        raise ValueError("Tool main() requires @tool metadata")
    description = inspect.getdoc(main)
    if not description:
        raise ValueError("Tool main() requires a nonempty docstring")
    hints = typing.get_type_hints(main, include_extras=True)
    properties, required = {}, []
    for key, parameter in inspect.signature(main).parameters.items():
        if parameter.kind not in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            raise ValueError("Tool parameters cannot be positional-only, *args or **kwargs")
        if key not in hints:
            raise ValueError("Tool parameters require annotations")
        _supported(hints[key])
        schema = TypeAdapter(hints[key]).json_schema()
        # 호출 기본값은 signature만 소유한다. Annotated의 default로 호출 의미를 바꾸지 않는다.
        schema.pop("default", None)
        if parameter.default is inspect.Parameter.empty:
            required.append(key)
        else:
            ProjectConfig.validate_settings({"default": parameter.default})
            schema["default"] = deepcopy(parameter.default)
        properties[key] = schema
    parameters = {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
    async def handler(arguments):
        return await main(**arguments)
    definition = {"type": "function", "function": {"name": name, "description": description,
        "parameters": parameters, **getattr(main, "__tool_options__", {})}}
    value = Tool(name, description, parameters, handler, definition, contract)
    ToolRegistry((value,))
    return value
