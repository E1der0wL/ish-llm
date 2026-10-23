"""The existing Tool execution policy snapshot used in approval/Resume bindings."""

from copy import deepcopy
from dataclasses import dataclass, fields

from .policies import ToolPolicyValues


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolExecutionBinding:
    """An in-memory wire projection, never a new stored policy or execution grant."""

    SELECTOR_PRESENT = True

    policy: ToolPolicyValues
    revision: str
    approval: bool
    classifier: bool
    isolated_runner: bool
    operation_key: bool
    retry_safe_tools: list[str]
    max_retries: int | None
    retry_delay: float | None
    retry_selector: bool | None = None

    @staticmethod
    def constraints(value):
        """Project constraints after the caller has checked the complete current binding."""
        return deepcopy(value.get(ToolPolicyValues.argument_constraints.__name__, {}))

    def to_dict(self):
        value = self.policy.to_dict()
        for item in fields(self):
            if item.name == type(self).policy.__name__:
                continue
            if item.name == type(self).retry_selector.__name__ and self.retry_selector is None:
                continue
            value[item.name] = deepcopy(getattr(self, item.name))
        return value
