"""선언한 함수의 signature/type hints/docstring을 기존 Tool 계약으로 변환한다."""

from copy import deepcopy
import inspect
import types
import typing

from pydantic import TypeAdapter
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from llm.core.json_data import JsonObject
from llm.core.schema import SchemaKeyword, SchemaInput, object_schema
from .decorator import ToolMetadata
from .registry import Tool, ToolRegistry


class FunctionAnnotations:
    SCALARS = (str, int, float, bool, type(None))
    UNIONS = (typing.Union, types.UnionType)
    LIST_ARITY = 1
    DICT_ARITY = 2
    INCLUDE_EXTRAS = True
    UNSUPPORTED = "Unsupported Tool parameter annotation"
    ASYNC_REQUIRED = "Tool requires async main()"
    DESCRIPTION_REQUIRED = "Tool main() requires a nonempty docstring"
    PARAMETER_KIND = "Tool parameters cannot be positional-only, *args or **kwargs"
    ANNOTATION_REQUIRED = "Tool parameters require annotations"
    INVALID_DEFAULT = "Tool parameter default does not satisfy its annotation: {name}"

    @classmethod
    def check(cls, annotation):
        origin, args = typing.get_origin(annotation), typing.get_args(annotation)
        if annotation in cls.SCALARS:
            return
        if origin is typing.Annotated:
            cls.check(args[0])
            return
        if origin in cls.UNIONS:
            for item in args:
                cls.check(item)
            return
        if origin is typing.Literal and args and all(type(v) in cls.SCALARS for v in args):
            JsonObject.validate({SchemaKeyword.ENUM: list(args)})
            return
        if origin is list and len(args) == cls.LIST_ARITY:
            cls.check(args[0])
            return
        if origin is dict and len(args) == cls.DICT_ARITY and args[0] is str:
            cls.check(args[1])
            return
        raise ValueError(cls.UNSUPPORTED)


def function_tool(name, main) -> Tool:
    """출력은 기존 ToolExecutor가 검증한다. 입력은 선언한 JSON 타입을 엄격하게 반영한다."""
    if not inspect.iscoroutinefunction(main):
        raise ValueError(FunctionAnnotations.ASYNC_REQUIRED)
    contract = ToolMetadata.contract(main)
    description = inspect.getdoc(main)
    if not description:
        raise ValueError(FunctionAnnotations.DESCRIPTION_REQUIRED)
    hints = typing.get_type_hints(main, include_extras=FunctionAnnotations.INCLUDE_EXTRAS)
    properties, required = {}, []
    for key, parameter in inspect.signature(main).parameters.items():
        if parameter.kind not in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            raise ValueError(FunctionAnnotations.PARAMETER_KIND)
        if key not in hints:
            raise ValueError(FunctionAnnotations.ANNOTATION_REQUIRED)
        FunctionAnnotations.check(hints[key])
        schema = TypeAdapter(hints[key]).json_schema()
        # 호출 기본값은 signature만 소유한다. Annotated의 default로 호출 의미를 바꾸지 않는다.
        schema.pop(SchemaKeyword.DEFAULT, SchemaInput.UNSPECIFIED)
        if parameter.default is inspect.Parameter.empty:
            required.append(key)
        else:
            JsonObject.validate({SchemaKeyword.DEFAULT: parameter.default})
            # 실제 Python 기본값을 변환 없이 provider에 공개하는 동일 schema로 검증한다.
            try:
                Draft202012Validator(schema).validate(parameter.default)
            except ValidationError as error:
                raise ValueError(FunctionAnnotations.INVALID_DEFAULT.format(name=key)) from error
            schema[SchemaKeyword.DEFAULT] = deepcopy(parameter.default)
        properties[key] = schema
    parameters = object_schema(properties, **{SchemaKeyword.REQUIRED: required})
    async def handler(arguments):
        return await main(**arguments)
    definition = ToolMetadata.definition(main, name, description, parameters)
    value = Tool(name, description, parameters, handler, definition, contract=contract)
    ToolRegistry((value,))
    return value
