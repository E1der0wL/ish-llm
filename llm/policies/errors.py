"""계층에 독립적인 실행 정책 오류 계약."""
from llm.errors import CodedError
from llm.core.contracts import Diagnostic


class ExecutionLimitError(CodedError, RuntimeError):
    """UI가 문자열 파싱 없이 처리할 수 있는 실행 정책 오류."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

    @property
    def diagnostic(self) -> Diagnostic:
        return Diagnostic.from_exception(self)


