"""모델 입력 선택 알고리즘. 설정은 호출하는 구현체가 해석하며 저장소를 소유하지 않는다."""

from llm.core.models import RunErrorCode
from llm.core.model_protocol import ModelRequest, ModelRole, copy_model

from collections.abc import Callable
from enum import StrEnum
from .errors import ExecutionLimitError


class CompletionPolicy:
    """전체 요청을 세는 tokenizer를 주입받아 과거 턴만 제거한다.

    counter(request)는 messages, tools, 모델별 오버헤드를 포함한 토큰 수를 반환한다.
    시스템 지시와 현재 요청 이후의 Tool 호출/결과는 항상 함께 보존한다.
    저장된 대화는 변경하지 않으며, 현재 작업 자체가 너무 크면 호출 전에 거부한다.
    """

    class Field(StrEnum):
        MAX_TOKENS = "max_tokens"
        RESERVE_TOKENS = "reserve_tokens"
        COUNTER = "counter"

    NAME = "completion"
    MINIMUM_TOKENS = 1
    MINIMUM_RESERVE = 0
    DEFAULT_RESERVE = MINIMUM_RESERVE
    MINIMUM_COUNT = 0
    MINIMUM_COUNTER_NAME = 1
    PINNED_ROLES = (ModelRole.SYSTEM, ModelRole.DEVELOPER)
    RESERVE_DESCRIPTION = "입력 예산에서 제외할 출력 여유. SDK 출력 제한을 설정하지 않는다"
    INVALID_MAXIMUM = "max_tokens must be positive"
    INVALID_COUNTER = "counter must be callable"
    INVALID_POLICY = "Invalid completion policy: "
    INTEGER_REQUIRED = "completion policy.{key} requires an integer"
    INVALID_COUNTER_NAME = "completion policy.counter must be a nonempty name"
    INVALID_RESERVE = "completion policy.reserve_tokens requires max_tokens and must be smaller"
    UNREGISTERED_COUNTER = "Token counter is not registered: {name}"
    INVALID_REQUEST = "Completion policy requires ModelRequest"
    INVALID_BOUNDARY = "Invalid current conversation boundary"
    INVALID_COUNT = "Token counter must return a nonnegative integer"
    BUDGET_EXCEEDED = "Current request, tools and output reserve exceed the context budget"
    INCONSISTENT_COUNT = "Token counter returned inconsistent results"

    def __init__(self, max_tokens: int, *, counter: Callable, reserve_tokens: int = DEFAULT_RESERVE):
        self.validate_config({self.Field.MAX_TOKENS: max_tokens, self.Field.RESERVE_TOKENS: reserve_tokens})
        if max_tokens is None:
            raise ValueError(self.INVALID_MAXIMUM)
        if not callable(counter):
            raise TypeError(self.INVALID_COUNTER)
        self.max_tokens, self.reserve_tokens, self.counter = max_tokens, reserve_tokens, counter

    @classmethod
    def describe_config(cls) -> dict:
        """설정값을 생성하지 않고 입력 선택기의 허용 형태만 제공한다."""
        from llm.core.schema import (object_schema, field, SchemaKeyword, SchemaType,
                                     SchemaBoolean, SchemaExtension, SchemaNarrowing, SchemaResource)
        return {**object_schema({
            cls.Field.MAX_TOKENS: field([SchemaType.INTEGER, SchemaType.NULL],
                **{SchemaKeyword.MINIMUM: cls.MINIMUM_TOKENS, SchemaExtension.NARROWING: SchemaNarrowing.MAXIMUM}),
            cls.Field.RESERVE_TOKENS: field(SchemaType.INTEGER, description=cls.RESERVE_DESCRIPTION,
                **{SchemaKeyword.MINIMUM: cls.MINIMUM_RESERVE}),
            cls.Field.COUNTER: field(SchemaType.STRING,
                **{SchemaKeyword.MIN_LENGTH: cls.MINIMUM_COUNTER_NAME, SchemaExtension.RESOURCE: SchemaResource.TOKEN_COUNTERS}),
        }, **{SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT}),
            SchemaKeyword.TYPE: [SchemaType.OBJECT, SchemaType.NULL]}

    @classmethod
    def validate_config(cls, policy):
        from llm.core.json_data import JsonObject
        from jsonschema import Draft202012Validator
        JsonObject.validate({cls.NAME: policy})
        error = next(Draft202012Validator(cls.describe_config()).iter_errors(policy), None)
        if error:
            raise ValueError(cls.INVALID_POLICY + error.message)
        if policy is None:
            return
        for key in (cls.Field.MAX_TOKENS, cls.Field.RESERVE_TOKENS):
            if policy.get(key) is not None and type(policy[key]) is not int:
                raise ValueError(cls.INTEGER_REQUIRED.format(key=key))
        if cls.Field.COUNTER in policy and not policy[cls.Field.COUNTER].strip():
            raise ValueError(cls.INVALID_COUNTER_NAME)
        maximum, reserve = policy.get(cls.Field.MAX_TOKENS), policy.get(cls.Field.RESERVE_TOKENS)
        if reserve and (maximum is None or reserve >= maximum):
            raise ValueError(cls.INVALID_RESERVE)

    @classmethod
    def from_config(cls, policy, counters):
        """정책을 사용하는 구현체가 실행별 사본을 구성한다. 계산기는 호스트의 공유 자원이다."""
        cls.validate_config(policy)
        if policy is None or policy.get(cls.Field.MAX_TOKENS) is None:
            return None
        name = policy.get(cls.Field.COUNTER)
        if name not in counters:
            raise ExecutionLimitError(RunErrorCode.POLICY_UNAVAILABLE, cls.UNREGISTERED_COUNTER.format(name=name))
        return cls(policy[cls.Field.MAX_TOKENS], counter=counters[name],
                   **({cls.Field.RESERVE_TOKENS: policy[cls.Field.RESERVE_TOKENS]} if cls.Field.RESERVE_TOKENS in policy else {}))

    def prepare(self, request: ModelRequest) -> ModelRequest:
        return self.prepare_turn(request)

    def prepare_turn(self, request: ModelRequest, current_index=None, turn_starts=None) -> ModelRequest:
        """provider 호출 직전에 사용한다. 반환 요청과 입력 요청은 독립적이다."""
        if not isinstance(request, ModelRequest):
            raise TypeError(self.INVALID_REQUEST)
        result = copy_model(request)
        messages = result.messages
        latest = (next((i for i in range(len(messages) - 1, -1, -1)
                        if messages[i].role == ModelRole.USER), 0) if current_index is None else current_index)
        if type(latest) is not int or latest < 0 or (messages and latest >= len(messages)):
            raise ValueError(self.INVALID_BOUNDARY)
        pinned = [item for item in messages[:latest] if item.role in self.PINNED_ROLES]
        groups = []
        for index, item in enumerate(messages[:latest]):
            if item.role in self.PINNED_ROLES:
                continue
            start = item.role == ModelRole.USER if turn_starts is None else index in turn_starts
            if start or not groups:
                groups.append([])
            groups[-1].append(item)

        def fits():
            # tokenizer가 요청을 변경해도 실제 전송할 요청에는 반영하지 않는다.
            count = self.counter(copy_model(result))
            if type(count) is not int or count < self.MINIMUM_COUNT:
                raise ValueError(self.INVALID_COUNT)
            return count <= self.max_tokens - self.reserve_tokens

        if fits():
            return result
        def select(index):
            result.messages = pinned + [item for group in groups[index:] for item in group] + messages[latest:]

        select(len(groups))
        if not fits():
            raise ExecutionLimitError(RunErrorCode.CONTEXT_BUDGET_EXCEEDED, self.BUDGET_EXCEEDED)
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
            raise ValueError(self.INCONSISTENT_COUNT)
        return result
