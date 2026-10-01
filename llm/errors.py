"""분류된 실행 오류의 명시적 계약. 오류 의미·retry 정책은 각 subsystem이 소유한다."""

from typing import Iterator, Optional


class CodedError:
    """안정적인 code를 제공한다고 명시하는 mixin. 임의 SDK의 .code와 구분한다.

    기존 ValueError/RuntimeError 상속과 생성자를 유지한다. 확장 구현도 의도적으로
    이 계약을 채택할 수 있지만 code 자체가 재시도나 권한을 부여하지는 않는다.
    """

    code: str


def exception_chain(error: BaseException) -> Iterator[BaseException]:
    """명시적 cause 우선, 없으면 숨기지 않은 context. 순환/과도한 체인은 중단한다."""
    seen = set()
    # 실행 정책이 아닌 오류 관찰의 작업량 상한. 비정상 체인이 종료 처리를 막지 못하게 한다.
    for _ in range(32):
        if not isinstance(error, BaseException) or id(error) in seen:
            break
        seen.add(id(error))
        yield error
        error = error.__cause__ if error.__cause__ is not None else (
            None if error.__suppress_context__ else error.__context__)


def stable_error_code(error: BaseException) -> Optional[str]:
    """명시적으로 분류한 오류만 보존한다. message/status/vendor code를 추측하지 않는다."""
    for current in exception_chain(error):
        if isinstance(current, CodedError):
            code = getattr(current, "code", None)
            if isinstance(code, str) and code.strip():
                return str(code)
    return None
