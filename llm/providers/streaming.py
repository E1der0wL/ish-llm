"""Normalized streaming observations; adapters own SDK chunks and final messages."""

from typing import Protocol, Iterator, AsyncIterator

from llm.errors import CodedError
from llm.core import model_protocol


class CompletionError(CodedError, ValueError):
    """Invalid/incomplete response or an explicit completion limit; never retry-safe."""

    def __init__(self, message, *, code="completion_invalid_response"):
        self.code = code
        super().__init__(message)


class CompletionDecoder(Protocol):
    """One instance per attempt. Decode may yield observations before rejecting a chunk.

    finish validates termination and returns the adapter-owned message only after
    success. Decoders do not retry, execute Tools, or persist lifecycle state.
    The factory accepts max_tool_calls, max_argument_chars and max_output_chars.
    None means no added limit. The decoder must enforce supplied limits using
    its protocol semantics; it must not invent limits or silently ignore them.
    """

    def decode(self, chunk) -> Iterator[model_protocol.CompletionUpdate]: ...
    def finish(self) -> model_protocol.ModelResponse: ...


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

    Loop additionally requires describe_config() and validate_request(parameters,
    require_model=False). SDK options belong to the adapter; message/Tool/response
    meanings belong to the common model values.
    """

    def validate_input_ownership(self, parameters: dict, *, auxiliary=False) -> None: ...
    def auxiliary_parameters(self, parameters: dict, *, preserve_retries=False, preserve_output_limits=True) -> dict:
        """Detach adapter settings for a separate Tool-free analysis invocation."""
        ...
    def prepare(self, request: model_protocol.ModelRequest) -> model_protocol.ModelRequest: ...
    def describe_call(self, request: model_protocol.ModelRequest): ...
    def describe_request(self, request: model_protocol.ModelRequest) -> dict: ...
    def effective_attempts(self, request: model_protocol.ModelRequest, maximum: int) -> int: ...
    def classify_error(self, error: Exception) -> str: ...
    def retryable(self, error: Exception) -> bool: ...
    def retry_fields(self, request: model_protocol.ModelRequest) -> tuple[str, ...]: ...
    def validate_retry(self, request: model_protocol.ModelRequest, *, original: model_protocol.ModelRequest) -> None: ...
    def validate_processing(self, request: dict, *, original: dict) -> None:
        """Reject processor changes to adapter-owned retry/authority parameters."""
        ...
    def decoder(self, **limits) -> CompletionDecoder: ...
    def stream(self, request: model_protocol.ModelRequest, *, buffer_size: int) -> AsyncIterator: ...
