"""Python main 함수의 실행 선언. 함수를 감싸거나 이름/스키마를 복제하지 않는다."""

from types import MappingProxyType
from .registry import ToolContract


_UNSET = object()


def tool(*, revision="1", effect="unknown", isolation="none", approval_required=False,
         operation_key_required=False, strict=_UNSET):
    """ToolContract를 명시한다. strict는 지정한 경우에만 provider 정의에 포함한다."""
    contract = ToolContract(revision, effect, isolation, approval_required, operation_key_required)
    if strict is not _UNSET and type(strict) is not bool:
        raise TypeError("strict must be boolean")
    options = MappingProxyType({} if strict is _UNSET else {"strict": strict})
    def declare(function):
        if hasattr(function, "__tool_contract__"):
            raise ValueError("Tool metadata is already declared")
        function.__tool_contract__ = contract
        function.__tool_options__ = options
        return function
    return declare
