"""UI와 실행기가 공유하는 사용자 요청·선택지·응답. 실행이나 파일 저장을 수행하지 않는다."""

from copy import deepcopy
from dataclasses import asdict, field, replace
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Optional

from llm.compat import dataclass
from .models import ProjectConfig, new_id, now
from .schema import checked_schema


@dataclass(frozen=True, slots=True)
class InteractionOption:
    """표시용 선택지와 엔진에 전달할 JSON 값. effect는 승인/거절 등의 의미를 나타낸다."""

    id: str
    label: str
    description: str = ""
    effect: str = "select"
    value: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id or not isinstance(self.label, str) or not self.label:
            raise ValueError("Interaction options require an ID and label")
        if not isinstance(self.description, str) or not isinstance(self.effect, str):
            raise TypeError("Invalid interaction option text")
        ProjectConfig.validate_settings({"value": self.value})

    def to_dict(self) -> dict:
        value = asdict(self)
        ProjectConfig.validate_settings(value)
        return deepcopy(value)

    @classmethod
    def from_dict(cls, value: dict) -> "InteractionOption":
        return cls(**deepcopy(value))


@dataclass(frozen=True, slots=True)
class InteractionRequest:
    """실행 대상과 UI 선택지를 담은 스냅샷. 추천 선택지는 자동 승인 권한이 아니다."""

    title: str
    options: tuple[InteractionOption, ...]
    id: str = field(default_factory=new_id)
    revision: int = 1
    kind: str = "approval"
    category: str = "tool.execute"
    priority: str = "normal"
    risk: str = "unknown"
    description: str = ""
    recommended_option_id: Optional[str] = None
    input_schema: Optional[dict] = None
    source: dict = field(default_factory=dict)
    action: dict = field(default_factory=dict)
    binding: dict = field(default_factory=dict)
    status: str = "pending"
    created_at: str = field(default_factory=now)
    expires_at: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or len(self.id) != 32 or any(c not in "0123456789abcdef" for c in self.id):
            raise ValueError("Invalid interaction ID")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("Interaction revision must be positive")
        if not isinstance(self.title, str) or not self.title or not isinstance(self.description, str):
            raise ValueError("Interaction title/description must be text")
        if self.kind not in ("approval", "confirmation", "choice", "input") or self.status not in ("pending", "answered", "expired", "cancelled"):
            raise ValueError("Invalid interaction kind/status")
        if self.priority not in ("low", "normal", "high", "urgent") or self.risk not in ("low", "medium", "high", "unknown"):
            raise ValueError("Invalid interaction priority/risk")
        if not isinstance(self.category, str) or not self.category:
            raise ValueError("Interaction category is required")
        if not isinstance(self.options, tuple) or not self.options or any(not isinstance(o, InteractionOption) for o in self.options):
            raise TypeError("Interaction options must be a nonempty tuple")
        ids = [o.id for o in self.options]
        if len(ids) != len(set(ids)) or self.recommended_option_id is not None and self.recommended_option_id not in ids:
            raise ValueError("Invalid or duplicate interaction option selection")
        for value in (self.source, self.action, self.binding):
            ProjectConfig.validate_settings(value)
        for value in (self.created_at, self.expires_at):
            if value is not None and datetime.fromisoformat(value).utcoffset() is None:
                raise ValueError("Interaction timestamps require a timezone")
        if self.input_schema is not None:
            checked_schema(self.input_schema)

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and datetime.fromisoformat(self.expires_at) <= datetime.now(timezone.utc)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> dict:
        value = asdict(self)
        value["options"] = [asdict(o) for o in self.options]
        ProjectConfig.validate_settings(value)
        return deepcopy(value)

    @classmethod
    def from_dict(cls, value: dict):
        data = deepcopy(value)
        data["options"] = tuple(InteractionOption(**o) for o in data["options"])
        return cls(**data)

    def bind(self, checkpoint: str, key: str, *, decision_key: Optional[str] = None,
             input_key: Optional[str] = None) -> "InteractionRequest":
        """Engine의 재개 형식은 선택지 값으로 표현한다. UI는 이 값을 해석할 필요가 없다."""
        options = tuple(replace(o, value={decision_key: o.value}) if decision_key and type(o.value) is bool else o for o in self.options)
        return replace(self, options=options, binding={"checkpoint": checkpoint, "key": key, "input_key": input_key})

    def respond(self, option_id: str, *, value: Any = None) -> "InteractionResponse":
        """UI가 본 요청 버전과 실행 대상에 묶인 응답을 만든다. 저장/실행은 하지 않는다."""
        response = InteractionResponse(self.id, self.revision, option_id, self.fingerprint, value)
        response.decision(self)
        return response


@dataclass(frozen=True, slots=True)
class InteractionResponse:
    """사용자가 확인한 요청 버전/지문에 묶인 응답. 저장과 실행은 서비스가 담당한다."""

    request_id: str
    request_revision: int
    option_id: str
    request_fingerprint: str
    value: Any = None
    actor: str = "user"
    created_at: str = field(default_factory=now)
    policy_id: Optional[str] = None

    def __post_init__(self) -> None:
        if type(self.request_revision) is not int or self.request_revision < 1:
            raise ValueError("Invalid interaction response revision")
        if any(not isinstance(v, str) or not v for v in (
                self.request_id, self.option_id, self.request_fingerprint)):
            raise ValueError("Invalid interaction response identity")
        if self.actor not in ("user", "policy", "host"):
            raise ValueError("Invalid interaction response actor")
        if datetime.fromisoformat(self.created_at).utcoffset() is None:
            raise ValueError("Interaction timestamps require a timezone")
        ProjectConfig.validate_settings({"value": self.value})
        if self.policy_id is not None and (not isinstance(self.policy_id, str) or not self.policy_id):
            raise ValueError("Invalid interaction policy ID")

    @classmethod
    def from_dict(cls, value: dict):
        return cls(**deepcopy(value))

    def to_dict(self) -> dict:
        value = asdict(self)
        ProjectConfig.validate_settings(value)
        return deepcopy(value)

    def decision(self, request: InteractionRequest) -> Any:
        from jsonschema import Draft202012Validator, ValidationError
        if (self.request_id != request.id or self.request_revision != request.revision
                or self.request_fingerprint != request.fingerprint):
            raise ValueError("Interaction changed; reload before responding")
        if request.expired or request.status != "pending":
            raise ValueError("Interaction is no longer pending")
        option = next((o for o in request.options if o.id == self.option_id), None)
        if option is None:
            raise ValueError("Unknown interaction option")
        self.to_dict()
        if self.actor not in ("user", "policy", "host"):
            raise ValueError("Invalid interaction response actor")
        decision = deepcopy(option.value)
        if option.effect == "deny" and self.value is not None:
            raise ValueError("Denial cannot include input")
        if option.effect != "deny" and request.input_schema is not None:
            try:
                Draft202012Validator(request.input_schema).validate({} if self.value is None else self.value)
            except ValidationError as error:
                raise ValueError(f"Invalid interaction input: {error.message}") from error
        if self.value is not None:
            if request.input_schema is None:
                raise ValueError("Interaction does not accept input")
            key = request.binding.get("input_key")
            if key:
                if not isinstance(decision, dict) or key in decision:
                    raise ValueError("Interaction input cannot replace the selected decision")
                decision[key] = deepcopy(self.value)
            else:
                decision = deepcopy(self.value)
        return decision


def approval_request(title: str, *, description: str = "", source: Optional[dict] = None,
                     action: Optional[dict] = None, category: str = "tool.execute",
                     risk: str = "unknown") -> InteractionRequest:
    return InteractionRequest(title, (
        InteractionOption("approve", "승인", effect="approve", value=True),
        InteractionOption("deny", "거절", effect="deny", value=False)),
        description=description, source=source or {}, action=action or {}, category=category, risk=risk)


@dataclass(frozen=True, slots=True)
class InteractionView:
    """불변 요청과 현재 처리 상태의 투영. 사용자 결정과 실제 실행 결과를 구분한다."""
    request: InteractionRequest
    response: Optional[InteractionResponse]
    status: str
    can_respond: bool
    can_resume: bool
    blocked_reason: Optional[str] = None
    resumed_run_id: Optional[str] = None
    execution_status: Optional[str] = None

    def to_dict(self) -> dict:
        return {**asdict(self), "request": self.request.to_dict(),
                "response": self.response.to_dict() if self.response else None}
