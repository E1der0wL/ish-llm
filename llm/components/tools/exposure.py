"""Application-selected Tool exposure for one model invocation, never authority."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from llm.core.model_protocol import ModelMessage, copy_model


@dataclass(frozen=True, slots=True)
class ToolExposure:
    """Detached input; names are the existing authorized ceiling, not new grants.

    Selectors receive no handlers, mutable scope or provider credentials. They
    must keep per-invocation state local and never perform Tool effects.
    """

    INVALID_NAMES = "Exposed Tools must be a sequence of unique nonempty names"
    OUTSIDE_SCOPE = "Exposed Tool is outside the available invocation scope"
    REQUIRED_EMPTY = "Tool selection is empty while a Tool call is required"
    UNEXPOSED_CALL = "Model called a Tool not exposed for this invocation"
    INVALID_SELECTOR = "Tool selector requires select_tools(request) and an explicit nonempty revision"
    CHANGED_SELECTOR = "Tool selector changed during execution; start a new request"
    SELECTOR_TYPE = "type"
    SELECTOR_REVISION = "revision"
    TYPE_SEPARATOR = "."
    ABSENT = None

    iteration: int
    messages: tuple[ModelMessage, ...]
    available: tuple[str, ...]
    required: bool

    def __post_init__(self):
        object.__setattr__(self, type(self).messages.__name__, tuple(copy_model(m) for m in self.messages))
        object.__setattr__(self, type(self).available.__name__, tuple(self.available))

    @classmethod
    def validate_names(cls, names, available):
        if (isinstance(names, (str, bytes)) or not isinstance(names, Sequence)
                or any(not isinstance(name, str) or not name for name in names)):
            raise ValueError(cls.INVALID_NAMES)
        result = tuple(names)
        if len(set(result)) != len(result):
            raise ValueError(cls.INVALID_NAMES)
        if not set(result).issubset(available):
            raise ValueError(cls.OUTSIDE_SCOPE)
        return result

    def validate(self, names):
        result = self.validate_names(names, self.available)
        if self.required and not result:
            raise ValueError(self.REQUIRED_EMPTY)
        return result

    @classmethod
    def require_calls(cls, names, calls):
        if any(call.name not in names for call in calls):
            raise ValueError(cls.UNEXPOSED_CALL)

    @classmethod
    def bind_selector(cls, selector):
        if selector is cls.ABSENT:
            return cls.ABSENT
        revision = getattr(selector, cls.SELECTOR_REVISION, cls.ABSENT)
        if (not isinstance(revision, str) or not revision.strip()
                or not callable(getattr(selector, ToolSelector.select_tools.__name__, cls.ABSENT))):
            raise TypeError(cls.INVALID_SELECTOR)
        return {cls.SELECTOR_TYPE: type(selector).__module__ + cls.TYPE_SEPARATOR + type(selector).__qualname__,
                cls.SELECTOR_REVISION: revision}


class ToolSelector(Protocol):
    """Application hook. Change revision when selection behavior/config changes.

    An empty sequence explicitly disables exposure for this invocation. Missing
    hooks preserve the existing list; errors/None never fall back to all Tools.
    """

    revision: str

    async def select_tools(self, request: ToolExposure) -> Sequence[str]: ...
