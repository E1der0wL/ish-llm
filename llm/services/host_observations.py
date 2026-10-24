"""Host service observations; never serialize live adapters or grant execution authority."""

from dataclasses import dataclass, fields
from enum import Enum
from llm.core.configuration_views import ConfigurationPresentation
from llm.services.runtime.tools import ToolRuntime


class ServiceProjection(Enum):
    STRUCTURE = "structure"
    NONNULL = "nonnull"
    PRIMITIVE = "primitive"
    TOOL = "tool"

    @staticmethod
    def selected(declarations, role):
        return sorted(
            (item for item in declarations if role in item.metadata), key=lambda item: item.metadata[role]
        )


@dataclass(frozen=True, slots=True)
class ServiceIdentity:
    ABSENT = None
    TYPE_SEPARATOR = "."

    configured: bool
    implementation: str | None

    @classmethod
    def observe(cls, value):
        return cls(
            value is not cls.ABSENT,
            (
                type(value).__module__ + cls.TYPE_SEPARATOR + type(value).__qualname__
                if value is not cls.ABSENT
                else cls.ABSENT
            ),
        )

    def to_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}


class ToolRuntimeObservation:
    ADAPTERS = (
        ToolRuntime.authorize.__name__,
        ToolRuntime.runner.__name__,
        ToolRuntime.operation_key.__name__,
        ToolRuntime.operation_probe.__name__,
        ToolRuntime.classify.__name__,
        ToolRuntime.decide_retry.__name__,
    )

    @classmethod
    def describe(cls, runtime):
        value = {name: ServiceIdentity.observe(getattr(runtime, name)).to_dict() for name in cls.ADAPTERS}
        value.update(
            {
                ToolRuntime.revision.__name__: runtime.revision,
                ToolRuntime.retry_safe_tools.__name__: list(runtime.retry_safe_tools),
            }
        )
        return value


@dataclass(frozen=True, slots=True)
class HostServiceSnapshot:
    values: dict

    def to_dict(self):
        return {type(self).values.__name__: self.values, **ConfigurationPresentation.host()}
