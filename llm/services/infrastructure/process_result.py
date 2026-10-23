"""The public result of an executed command, separate from Tool JSON output."""

from dataclasses import dataclass
from llm.core.contracts import JsonValue


@dataclass(frozen=True, slots=True)
class ProcessResult(JsonValue):
    ENCODING = "utf-8"
    ERRORS = "replace"

    returncode: int
    stdout: str
    stderr: str

    @classmethod
    def decoded(cls, code, stdout, stderr):
        return cls(code, stdout.decode(cls.ENCODING, errors=cls.ERRORS),
                   stderr.decode(cls.ENCODING, errors=cls.ERRORS))
