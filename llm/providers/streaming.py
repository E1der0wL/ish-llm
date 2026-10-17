"""Normalized streaming observations; adapters own SDK chunks and final messages."""

from dataclasses import dataclass
from typing import Protocol, Iterator, AsyncIterator

from llm.errors import CodedError


class CompletionError(CodedError, ValueError):
    """Invalid/incomplete response or an explicit completion limit; never retry-safe."""

    def __init__(self, message, *, code="completion_invalid_response"):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CompletionUpdate:
    text: str = ""
    reasoning: str = ""
    response_id: str | None = None
    model: str | None = None
    usage: dict | None = None
    finish_reason: str | None = None

    def __post_init__(self):
        if not isinstance(self.text, str) or not isinstance(self.reasoning, str):
            raise TypeError("Completion deltas must be text")
        if any(value is not None and not isinstance(value, str)
               for value in (self.response_id, self.model, self.finish_reason)):
            raise TypeError("Completion identities and termination must be text or None")
        if self.usage is not None:
            def validate(value):
                if not isinstance(value, dict):
                    raise TypeError("Completion usage must be a normalized dictionary")
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise TypeError("Completion usage keys must be text")
                    if isinstance(item, dict):
                        validate(item)
                    elif type(item) is not int or item < 0:
                        raise ValueError("Completion usage must contain nonnegative integers")
            validate(self.usage)


class CompletionDecoder(Protocol):
    """One instance per attempt. Decode may yield observations before rejecting a chunk.

    finish validates termination and returns the adapter-owned message only after
    success. Decoders do not retry, execute Tools, or persist lifecycle state.
    The factory accepts max_tool_calls, max_argument_chars and max_output_chars.
    None means no added limit. The decoder must enforce supplied limits using
    its protocol semantics; it must not invent limits or silently ignore them.
    """

    def decode(self, chunk) -> Iterator[CompletionUpdate]: ...
    def finish(self) -> dict: ...


class CompletionAdapter(Protocol):
    """Selected SDK boundary. Requests and retry fields are owned by the adapter.

    stream yields raw chunks (including empty chunks); decoder translates them.
    Receiving any chunk prohibits replay. Cancellation must close the attempt.
    The adapter must participate in provider admission/observations, preserve
    explicitly supplied limits, and never execute Tools or write domain state.
    Exceptions crossing stream must be normalized CodedErrors when their code
    should become the Run failure code. Cancellation is never a retry signal.
    A replaceable host adapter may expose JSON execution_binding; change it
    whenever its runtime semantics change, so durable approvals cannot outlive it.

    Loop additionally requires describe_config() and validate_request(request,
    require_model=False). Its canonical messages/tools/assistant protocol stays
    explicit; an adapter for another SDK translates that protocol at this boundary.
    """

    def prepare(self, request: dict) -> dict: ...
    def describe_call(self, request: dict): ...
    def describe_request(self, request: dict) -> dict: ...
    def effective_attempts(self, request: dict, maximum: int) -> int: ...
    def classify_error(self, error: Exception) -> str: ...
    def retryable(self, error: Exception) -> bool: ...
    def retry_fields(self, request: dict) -> tuple[str, ...]: ...
    def validate_retry(self, request: dict, *, original: dict) -> None: ...
    def validate_processing(self, request: dict, *, original: dict) -> None:
        """Reject processor changes to adapter-owned retry/authority parameters."""
        ...
    def decoder(self, **limits) -> CompletionDecoder: ...
    def stream(self, request: dict, *, buffer_size: int) -> AsyncIterator: ...
