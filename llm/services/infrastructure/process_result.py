"""The public result of an executed command, separate from Tool JSON output."""

from dataclasses import dataclass
from llm.core.contracts import JsonValue
from .text_stream import ENCODING as TEXT_ENCODING


@dataclass(frozen=True, slots=True)
class ProcessResult(JsonValue):
    ENCODING = TEXT_ENCODING
    ERRORS = "replace"
    SUCCESS = 0

    returncode: int
    stdout: str
    stderr: str

    @classmethod
    def decoded(cls, code, stdout, stderr):
        return cls(code, cls.text(stdout), cls.text(stderr))

    @classmethod
    def text(cls, value):
        return value.decode(cls.ENCODING, errors=cls.ERRORS)
