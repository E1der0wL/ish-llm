"""Detached model-page projections built from native reader field declarations."""

from dataclasses import dataclass, fields

from llm.core.contracts import JsonValue
from llm.components.tools.builtin.file_values import FileTextPage
from llm.components.tools.builtin.process_values import ProcessOutput, ProcessSnapshot, StoredOutput
from .reader_calls import ReadCall


@dataclass(frozen=True, slots=True)
class PageHint(JsonValue):
    next_read: ReadCall
    notice: str


@dataclass(frozen=True, slots=True)
class FilePage(FileTextPage, JsonValue):
    """Use native fields/types; this model view is never a stored file record."""

    @classmethod
    def read(cls, value):
        return cls.from_dict({item.name: value[item.name] for item in fields(FileTextPage)})


@dataclass(frozen=True, slots=True)
class ProcessPage(ProcessOutput, JsonValue):
    """Use native fields/types without making this preview a process authority."""


@dataclass(frozen=True, slots=True)
class StreamPage:
    text: str
    offset: int
    total: int


@dataclass(frozen=True, slots=True)
class ProcessPreview:
    """Read a retained snapshot or an existing page once at the model boundary."""

    STREAM_FIELDS = (
        (ProcessOutput.stdout.__name__, ProcessOutput.next_stdout_offset.__name__,
         ProcessOutput.stdout_remaining.__name__, StoredOutput.stdout_chars.__name__),
        (ProcessOutput.stderr.__name__, ProcessOutput.next_stderr_offset.__name__,
         ProcessOutput.stderr_remaining.__name__, StoredOutput.stderr_chars.__name__),
    )
    MISSING_STREAM = 'Process preview requires retained character counts or page cursors'

    page: ProcessPage
    stdout: StreamPage
    stderr: StreamPage
    cwd: str | None
    sources: dict | None

    @classmethod
    def read(cls, value):
        streams = []
        for text_key, next_key, remaining_key, total_key in cls.STREAM_FIELDS:
            text = value[text_key]
            if not isinstance(text, str):
                raise ValueError(cls.MISSING_STREAM)
            if type(value.get(next_key)) is int and type(value.get(remaining_key)) is int:
                offset = value[next_key] - len(text)
                total = value[next_key] + value[remaining_key]
            elif type(value.get(total_key)) is int:
                offset, total = 0, value[total_key]
            else:
                raise ValueError(cls.MISSING_STREAM)
            if offset < 0 or total < offset + len(text):
                raise ValueError(cls.MISSING_STREAM)
            streams.append(StreamPage(text, offset, total))
        stdout, stderr = streams
        page = ProcessPage(
            value[ProcessOutput.process_id.__name__], value[ProcessOutput.status.__name__],
            value[ProcessOutput.returncode.__name__], value[ProcessOutput.truncated.__name__],
            stdout.text, stdout.offset + len(stdout.text), stdout.total - stdout.offset - len(stdout.text),
            stderr.text, stderr.offset + len(stderr.text), stderr.total - stderr.offset - len(stderr.text))
        return cls(page, stdout, stderr, value.get(ProcessSnapshot.cwd.__name__),
                   value.get(StoredOutput.output_sources.__name__))

    def to_dict(self, page, *, include_sources):
        value = page.to_dict()
        value[StoredOutput.stdout_chars.__name__] = self.stdout.total
        value[StoredOutput.stderr_chars.__name__] = self.stderr.total
        if self.cwd is not None:
            value[ProcessSnapshot.cwd.__name__] = self.cwd
        if include_sources and self.sources is not None:
            value[StoredOutput.output_sources.__name__] = self.sources
        return value
