"""Common model processing values and detached observation copies."""

from dataclasses import dataclass, replace
from typing import Optional
from llm.core.contracts import Diagnostic, ResourceRef
from llm.core.model_protocol import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolDefinition,
    ToolMode,
    copy_model,
)


@dataclass(frozen=True, slots=True)
class CompletionRecovery:
    """Application input-repair proposal boundary; editable indexes are not authority."""

    diagnostic: Diagnostic
    request: ModelRequest
    attempt: int
    editable: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class CompletionRepair:
    """Replace reference bodies only. No SDK, Tool, policy or message-role changes."""

    replacements: dict[int, str]


@dataclass(frozen=True, slots=True)
class CompletionMessage:
    """모델용 메시지와 원본 출처. 추가 참고자료는 source_id=None이며 ID는 공급자에게 보내지 않는다."""

    NO_SOURCE = None
    NO_RESULT_REFERENCE = None
    NEW_INPUT = False
    INVALID_MESSAGE = "CompletionMessage requires ModelMessage"

    message: ModelMessage
    source_id: Optional[str] = NO_SOURCE
    # 같은 작업에 추가된 USER 입력. provider kwargs에는 포함하지 않는다.
    continuation: bool = NEW_INPUT
    # Completed result identity, excluded from provider kwargs. Call IDs are local
    # to an execution scope and cannot identify sibling Agent results in a Run.
    result_ref: Optional[ResourceRef] = NO_RESULT_REFERENCE

    def __post_init__(self):
        if not isinstance(self.message, ModelMessage):
            raise TypeError(self.INVALID_MESSAGE)
        object.__setattr__(self, type(self).message.__name__, copy_model(self.message))

    def with_text(self, text):
        return replace(self, message=self.message.with_text(text))


@dataclass(slots=True)
class CompletionRequest:
    """한 텍스트 모델 호출의 가변 사본. SDK 인자와 출처/압축 계약을 분리한다.

    The current user instruction is text and remains an exact prefix. Active
    compaction appends reference summaries to that body and declares removed
    Tool exchange IDs. This contract does not interpret multimodal content or
    silently translate alternate summary placements.
    """

    UNSELECTED_TOOL_MODE = None

    parameters: dict
    messages: list[CompletionMessage]
    iteration: int
    # 완료된 현재 작업의 접두 Tool 교환만 명시적으로 압축할 수 있다. 공급자 인자가 아니다.
    compacted_tool_calls: tuple[str, ...] = ()

    tools: tuple[ToolDefinition, ...] = ()
    tool_mode: ToolMode | None = UNSELECTED_TOOL_MODE

    def to_model(self) -> ModelRequest:
        return ModelRequest([m.message for m in self.messages], self.parameters, self.tools, self.tool_mode)


@dataclass(frozen=True, slots=True)
class CompletionObservation:
    """예산 적용 후 모델에 전달한 요청과 응답, 가공 전 대화의 독립 사본.

    공급자 어댑터의 stream_options 등 내부 기본값 적용 전 요청이다. 관찰자가 사본을
    수정해도 다른 처리기·Tool 실행·최종 출력에는 영향을 주지 않는다.
    """

    INVALID_EXCHANGE = "Completion observation requires common request and response"
    INVALID_ORIGINAL_MESSAGES = "Completion observation requires common original messages"

    iteration: int
    request: ModelRequest
    response: ModelResponse
    original_messages: list[ModelMessage]

    def __post_init__(self):
        if not isinstance(self.request, ModelRequest) or not isinstance(self.response, ModelResponse):
            raise TypeError(self.INVALID_EXCHANGE)
        if any(not isinstance(message, ModelMessage) for message in self.original_messages):
            raise TypeError(self.INVALID_ORIGINAL_MESSAGES)
        for name in (
            type(self).request.__name__,
            type(self).response.__name__,
            type(self).original_messages.__name__,
        ):
            object.__setattr__(self, name, copy_model(getattr(self, name)))

    def snapshot(self):
        return CompletionObservation(
            self.iteration,
            copy_model(self.request),
            copy_model(self.response),
            copy_model(self.original_messages),
        )
