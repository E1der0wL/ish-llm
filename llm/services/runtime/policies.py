"""실행 한도와 실패 계약. Engine 종류나 저장 구현에 의존하지 않는다."""

import math
from typing import Optional

from llm.core.contracts import Diagnostic
from llm.compat import dataclass


class ExecutionLimitError(RuntimeError):
    """UI가 문자열 파싱 없이 처리할 수 있는 실행 정책 오류."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

    @property
    def diagnostic(self) -> Diagnostic:
        return Diagnostic.from_exception(self)


def positive_seconds(value, name):
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                              or not math.isfinite(value) or value <= 0):
        raise ValueError(f"{name} must be positive and finite")


@dataclass(frozen=True, slots=True)
class RunLimits:
    """Task 대기열과 Run 전체 시간 한도. None은 기존 무제한 동작을 유지한다.

    실행 시간은 문맥 준비부터 Engine 종료까지이며, 대기열 시간은 포함하지 않는다.
    취소에 협조하지 않는 외부 코드/스레드를 강제로 종료하는 OS 제한은 아니다.
    """

    max_queued: Optional[int] = None
    timeout_seconds: Optional[float] = None
    max_capability_rounds: int = 32

    def __post_init__(self):
        if self.max_queued is not None and (type(self.max_queued) is not int or self.max_queued < 1):
            raise ValueError("max_queued must be a positive integer")
        positive_seconds(self.timeout_seconds, "Run timeout")
        if type(self.max_capability_rounds) is not int or self.max_capability_rounds < 1:
            raise ValueError("max_capability_rounds must be a positive integer")


def model_token_count(request: dict) -> int:
    """모델별 LiteLLM 계산기에 메시지·Tool 정의를 전달한다. 원격 모델 호출은 하지 않는다."""
    from litellm import token_counter
    return token_counter(model=request.get("model"), messages=request.get("messages"),
                         tools=request.get("tools"), tool_choice=request.get("tool_choice"))


class ProjectPolicyResolver:
    """영속 JSON 정책을 실행 객체에 연결한다. 계산기만 백엔드에 등록하고 정책값은 Project가 소유한다."""

    def __init__(self, token_counters=None):
        self.token_counters = {"model_default": model_token_count, **dict(token_counters or {})}
        if any(not isinstance(name, str) or not name.strip() or not callable(counter)
               for name, counter in self.token_counters.items()):
            raise ValueError("Token counters require nonempty names and callable implementations")

    def resolve(self, settings: dict):
        from llm.core.policies import normalize_policies
        from llm.services.history.context import ContextPolicy, CompletionPolicy
        values = normalize_policies(settings)
        completion = values["completion"]
        policy = None
        usage = values.get("usage", {})
        if (usage.get("max_tokens") is not None or usage.get("project_max_tokens") is not None) and completion["counter"] not in self.token_counters:
            raise ExecutionLimitError("policy_unavailable", "Usage quota requires a registered token counter")
        if completion["max_tokens"] is not None:
            name = completion["counter"]
            if name not in self.token_counters:
                raise ExecutionLimitError("policy_unavailable", f"Token counter is not registered: {name}")
            policy = CompletionPolicy(completion["max_tokens"], reserve_tokens=completion["reserve_tokens"],
                                      counter=self.token_counters[name])
        return ContextPolicy(**values["context"]), policy, RunLimits(**values["run"])
