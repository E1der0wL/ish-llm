"""Binary file selection shared by Component file APIs."""

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class FileRange:
    START: ClassVar[int] = 0
    MINIMUM: ClassVar[int] = 0
    TO_END: ClassVar[None] = None
    READ_REMAINDER: ClassVar[int] = -1
    INVALID: ClassVar[str] = "File ranges must be nonnegative integers"

    offset: int = START
    length: int | None = TO_END

    def __post_init__(self):
        if (type(self.offset) is not int or self.offset < self.MINIMUM
                or self.length is not self.TO_END and (type(self.length) is not int or self.length < self.MINIMUM)):
            raise ValueError(self.INVALID)

    @property
    def read_size(self):
        return self.READ_REMAINDER if self.length is self.TO_END else self.length
