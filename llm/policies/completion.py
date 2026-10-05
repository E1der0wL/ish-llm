"""모델 입력 선택 알고리즘. 설정은 호출하는 구현체가 해석하며 저장소를 소유하지 않는다."""

from collections.abc import Callable
from .errors import ExecutionLimitError


class CompletionPolicy:
    """전체 요청을 세는 tokenizer를 주입받아 과거 턴만 제거한다.

    counter(request)는 messages, tools, 모델별 오버헤드를 포함한 토큰 수를 반환한다.
    시스템 지시와 현재 요청 이후의 Tool 호출/결과는 항상 함께 보존한다.
    저장된 대화는 변경하지 않으며, 현재 작업 자체가 너무 크면 호출 전에 거부한다.
    """

    def __init__(self, max_tokens: int, *, counter: Callable, reserve_tokens: int = 0):
        if type(max_tokens) is not int or max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if type(reserve_tokens) is not int or not 0 <= reserve_tokens < max_tokens:
            raise ValueError("reserve_tokens must be smaller than max_tokens")
        if not callable(counter):
            raise TypeError("counter must be callable")
        self.max_tokens, self.reserve_tokens, self.counter = max_tokens, reserve_tokens, counter

    @staticmethod
    def configuration_schema() -> dict:
        """설정값을 생성하지 않고 입력 선택기의 허용 형태만 제공한다."""
        from llm.core.schema import object_schema, field
        return {**object_schema({
            "max_tokens": field(["integer", "null"], minimum=1),
            "reserve_tokens": field("integer", minimum=0,
                description="입력 예산에서 제외할 출력 여유. SDK 출력 제한을 설정하지 않는다"),
            "counter": field("string", minLength=1, **{"x-resource": "token_counters"}),
        }, additionalProperties=False), "type": ["object", "null"]}

    @classmethod
    def validate_settings(cls, settings):
        from llm.core.models import ProjectConfig
        from jsonschema import Draft202012Validator
        ProjectConfig.validate_settings({"completion": settings})
        error = next(Draft202012Validator(cls.configuration_schema()).iter_errors(settings), None)
        if error:
            raise ValueError("Invalid completion policy: " + error.message)
        if settings is None:
            return
        for key in ("max_tokens", "reserve_tokens"):
            if settings.get(key) is not None and type(settings[key]) is not int:
                raise ValueError(f"completion policy.{key} requires an integer")
        if "counter" in settings and not settings["counter"].strip():
            raise ValueError("completion policy.counter must be a nonempty name")
        maximum, reserve = settings.get("max_tokens"), settings.get("reserve_tokens")
        if reserve and (maximum is None or reserve >= maximum):
            raise ValueError("completion policy.reserve_tokens requires max_tokens and must be smaller")

    @classmethod
    def from_settings(cls, settings, counters):
        """정책을 사용하는 구현체가 실행별 사본을 구성한다. 계산기는 호스트의 공유 자원이다."""
        cls.validate_settings(settings)
        if settings is None or settings.get("max_tokens") is None:
            return None
        name = settings.get("counter")
        if name not in counters:
            raise ExecutionLimitError("policy_unavailable", f"Token counter is not registered: {name}")
        return cls(settings["max_tokens"], counter=counters[name],
                   **({"reserve_tokens": settings["reserve_tokens"]} if "reserve_tokens" in settings else {}))

    def prepare(self, request: dict) -> dict:
        return self.prepare_turn(request)

    def prepare_turn(self, request: dict, current_index=None, turn_starts=None) -> dict:
        """provider 호출 직전에 사용한다. 반환 요청과 입력 요청은 독립적이다."""
        from llm.providers.parameters import copy_params
        result = copy_params(request)
        messages = result.get("messages", [])
        latest = (next((i for i in range(len(messages) - 1, -1, -1)
                        if messages[i].get("role") == "user"), 0) if current_index is None else current_index)
        if type(latest) is not int or latest < 0 or (messages and latest >= len(messages)):
            raise ValueError("Invalid current conversation boundary")
        pinned = [item for item in messages[:latest] if item.get("role") in ("system", "developer")]
        groups = []
        for index, item in enumerate(messages[:latest]):
            if item.get("role") in ("system", "developer"):
                continue
            start = item.get("role") == "user" if turn_starts is None else index in turn_starts
            if start or not groups:
                groups.append([])
            groups[-1].append(item)

        def fits():
            # tokenizer가 요청을 변경해도 실제 전송할 요청에는 반영하지 않는다.
            count = self.counter(copy_params(result))
            if type(count) is not int or count < 0:
                raise ValueError("Token counter must return a nonnegative integer")
            return count <= self.max_tokens - self.reserve_tokens

        if fits():
            return result
        def select(index):
            result["messages"] = pinned + [item for group in groups[index:] for item in group] + messages[latest:]

        select(len(groups))
        if not fits():
            raise ExecutionLimitError("context_budget_exceeded", "Current request, tools and output reserve exceed the context budget")
        # 턴이 많아도 전체 tokenizer를 턴 수만큼 반복 호출하지 않는다. 일반적인
        # 단조 토큰 계수에서는 최대한 많은 최근 턴을 보존하며 최종 후보는 항상 검증한다.
        low, high = 0, len(groups)
        while low < high:
            middle = (low + high) // 2
            select(middle)
            if fits():
                high = middle
            else:
                low = middle + 1
        select(low)
        if not fits():
            raise ValueError("Token counter returned inconsistent results")
        return result
