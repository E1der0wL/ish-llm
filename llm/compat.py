"""Python 버전별 차이를 흡수하는 호환 계층. 도메인 코드에서 버전 분기를 반복하지 않도록 표준 API 형태로 제공한다.

Small Python 3.9 compatibility adapters; never patch the standard library."""

import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass as _dataclass
from typing import Any, AsyncIterator, Callable, TypeVar, overload


T = TypeVar("T")


@overload
def dataclass(cls: type[T], **kwargs: Any) -> type[T]: ...


@overload
def dataclass(cls: None = None, **kwargs: Any) -> Callable[[type[T]], type[T]]: ...


def dataclass(cls=None, *, slots: bool = False, **kwargs: Any):
    """Preserve dataclass fields/defaults/frozen behavior on all supported versions.

    Python 3.9 has no slots option; those instances have a __dict__. On 3.10+
    retain native slots. Persistence always serializes declared fields only.
    """
    if sys.version_info >= (3, 10):
        kwargs["slots"] = slots
    return _dataclass(cls, **kwargs)


if sys.version_info >= (3, 11):
    from enum import StrEnum
    from asyncio import timeout
else:
    from enum import Enum
    from async_timeout import timeout

    class StrEnum(str, Enum):
        """String-valued persisted enums with native StrEnum formatting semantics.

        Project enums declare explicit string values; auto() is not used.
        """

        __str__ = str.__str__
        __format__ = str.__format__


if sys.version_info >= (3, 10):
    from contextlib import aclosing
else:
    @asynccontextmanager
    async def aclosing(stream: T) -> AsyncIterator[T]:
        try:
            yield stream
        finally:
            await stream.aclose()
