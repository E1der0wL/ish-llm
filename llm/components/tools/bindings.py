"""Trusted registration observations and optional handler selection hooks."""

from copy import deepcopy
from dataclasses import dataclass, field, fields
from inspect import getattr_static
import json
from typing import Any

from llm.core.tool_contracts import ToolClassification, ToolContract


@dataclass(frozen=True, slots=True)
class ToolRegistrationBinding:
    ALLOW_NONFINITE = False

    contract: ToolContract
    classification: ToolClassification | None = None
    execution_binding: Any = None

    def __post_init__(self):
        if self.execution_binding is not None:
            # This extension is Application-owned. Preserve the existing JSON
            # encoder acceptance and Python shape; do not normalize it to a schema.
            json.dumps(self.execution_binding, allow_nan=self.ALLOW_NONFINITE)
            object.__setattr__(self, self.__class__.execution_binding.__name__, deepcopy(self.execution_binding))

    def to_dict(self):
        value = ({self.__class__.classification.__name__: self.classification.to_dict()}
                 if self.classification is not None else {})
        value.update(self.contract.to_dict())
        if self.execution_binding is not None:
            value[self.__class__.execution_binding.__name__] = deepcopy(self.execution_binding)
        return value


@dataclass(frozen=True, slots=True)
class ToolExecutionBinding:
    WIRE_NAME = "wire_name"

    tools: list[dict]
    tool_contracts: dict
    policy: Any = field(metadata={WIRE_NAME: "tool_policy"})

    def to_dict(self):
        return {item.metadata.get(self.WIRE_NAME, item.name): deepcopy(getattr(self, item.name))
                for item in fields(self)}


class ToolHandlerBindings:
    """Discovery of existing optional hooks, without invoking them or granting authority."""

    BIND_ARGUMENTS = "bind_arguments"
    VALIDATE_SELECTION = "validate_selection"
    ABSENT = None

    @classmethod
    def declares_selection(cls, handler):
        return getattr_static(handler, cls.BIND_ARGUMENTS, cls.ABSENT) is not cls.ABSENT

    @classmethod
    def binder(cls, handler):
        return getattr(handler, cls.BIND_ARGUMENTS, cls.ABSENT)

    @classmethod
    def declared_binder(cls, handler):
        return getattr(handler, cls.BIND_ARGUMENTS) if cls.declares_selection(handler) else cls.ABSENT

    @classmethod
    def validator(cls, handler):
        return getattr(handler, cls.VALIDATE_SELECTION, cls.ABSENT)

    @classmethod
    def execution_binding(cls, handler):
        return getattr(handler, ToolRegistrationBinding.execution_binding.__name__, cls.ABSENT)
