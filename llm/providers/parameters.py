"""요청 인자의 컨테이너를 복사한다. SDK 클라이언트나 콜백처럼 복사하면 안 되는 런타임 객체의 동일성은 보존한다.

Copy request containers without copying live SDK clients or callbacks."""

from typing import Any


def copy_params(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: copy_params(item) for key, item in value.items()}
    if isinstance(value, list):
        return [copy_params(item) for item in value]
    if isinstance(value, tuple):
        return tuple(copy_params(item) for item in value)
    return value


def merge_params(defaults: dict, overrides: dict) -> dict:
    """설정과 같은 재귀 병합을 사용하되 실행 중인 SDK 객체의 동일성은 보존한다."""
    result = copy_params(defaults)
    for key, value in overrides.items():
        result[key] = (merge_params(result[key], value) if isinstance(result.get(key), dict) and isinstance(value, dict)
                       else copy_params(value))
    return result
