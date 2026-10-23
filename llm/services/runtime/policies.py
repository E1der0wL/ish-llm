"""실행 한도와 실패 계약. Engine 종류나 저장 구현에 의존하지 않는다."""

from llm.core.models import RunErrorCode
import math
from collections.abc import Mapping

from llm.policies import errors
from llm.core.policies import RunPolicy, ContextPolicy, UsagePolicy

MINIMUM_SECONDS = 0
INVALID_SECONDS = "{name} must be positive and finite"


def positive_seconds(value, name):
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                              or not math.isfinite(value) or value <= MINIMUM_SECONDS):
        raise ValueError(INVALID_SECONDS.format(name=name))


class TokenCounters:
    DEFAULT = "model_default"
    INVALID_MAPPING = "Token counters must be a mapping or None"
    INVALID_ENTRY = "Token counters require nonempty names and callable implementations"

    @classmethod
    def build(cls, selected=None):
        from llm.providers.litellm import model_token_count
        if selected is not None and not isinstance(selected, Mapping):
            raise TypeError(cls.INVALID_MAPPING)
        counters = {cls.DEFAULT: model_token_count, **({} if selected is None else dict(selected))}
        if any(not isinstance(name, str) or not name.strip() or not callable(counter)
               for name, counter in counters.items()):
            raise ValueError(cls.INVALID_ENTRY)
        return counters


class ProjectPolicyResolver:
    """영속 JSON 정책을 실행 객체에 연결한다. 계산기만 백엔드에 등록하고 정책값은 Project가 소유한다."""

    MISSING_COUNTER = "Usage quota requires a registered token counter"

    def __init__(self, token_counters=None):
        self.token_counters = TokenCounters.build(token_counters)

    def resolve(self, policies: dict):
        from llm.core.policies import normalize_policies
        values = normalize_policies(policies)
        usage = UsagePolicy.from_policies(values)
        if usage.requires_reservation and usage.counter not in self.token_counters:
            raise errors.ExecutionLimitError(RunErrorCode.POLICY_UNAVAILABLE, self.MISSING_COUNTER)
        return ContextPolicy.selected(values), RunPolicy.from_policies(values)
