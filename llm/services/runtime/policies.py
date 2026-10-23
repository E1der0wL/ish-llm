"""실행 한도와 실패 계약. Engine 종류나 저장 구현에 의존하지 않는다."""

from llm.core.models import RunErrorCode
import math
from collections.abc import Mapping

from llm.policies import errors
from llm.core.policies import RunPolicy, ContextPolicy, UsagePolicy


def positive_seconds(value, name):
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                              or not math.isfinite(value) or value <= 0):
        raise ValueError(f"{name} must be positive and finite")


class ProjectPolicyResolver:
    """영속 JSON 정책을 실행 객체에 연결한다. 계산기만 백엔드에 등록하고 정책값은 Project가 소유한다."""

    def __init__(self, token_counters=None):
        from llm.providers.litellm import model_token_count
        if token_counters is not None and not isinstance(token_counters, Mapping):
            raise TypeError("Token counters must be a mapping or None")
        self.token_counters = {"model_default": model_token_count,
                               **({} if token_counters is None else dict(token_counters))}
        if any(not isinstance(name, str) or not name.strip() or not callable(counter)
               for name, counter in self.token_counters.items()):
            raise ValueError("Token counters require nonempty names and callable implementations")

    def resolve(self, policies: dict):
        from llm.core.policies import normalize_policies
        values = normalize_policies(policies)
        usage = UsagePolicy.from_policies(values)
        if usage.requires_reservation and usage.counter not in self.token_counters:
            raise errors.ExecutionLimitError(RunErrorCode.POLICY_UNAVAILABLE, "Usage quota requires a registered token counter")
        return (ContextPolicy(**values["context"]) if values.get("context") else None), RunPolicy(**values.get("run", {}))
