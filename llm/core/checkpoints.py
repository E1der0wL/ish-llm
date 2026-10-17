"""체크포인트 수명 공통 계약. 파일과 Engine/Component payload는 소유자가 관리한다."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .contracts import Diagnostic, JsonValue
from .plans import CheckpointPlan
from llm.errors import CodedError


class CheckpointError(CodedError, ValueError):
    """Structured rejection; retains ValueError catch compatibility, never grants access."""

    def __init__(self, code, message, *, blockers=()):
        self.code, self.blockers = code, tuple(blockers)
        super().__init__(f"{code}: {message}")
        self.diagnostic = Diagnostic(code, str(self), details={
            "boundary": "checkpoint", "blockers": [item.to_dict() for item in self.blockers]})


def select_checkpoint(names, name=None):
    """Omission selects the sole stored checkpoint, never an Engine preset."""
    if name is None:
        if len(names) != 1:
            raise ValueError("Specify a checkpoint name unless exactly one checkpoint exists")
        name = names[0]
    # Explicit names are resolved by the repository. In particular, a Run that
    # failed before checkpoint creation must still report missing, not corrupt.
    if not isinstance(name, str) or not name:
        raise ValueError("Checkpoint name must be nonempty text")
    return name


def checkpoint_lifecycle(payload, lifecycle=None):
    """Public nested checkpoint lifecycle; private payloads may supply an explicit projection.

    Standard records use started/completed/waiting; responded and not_applied
    denote persisted no-replay observations. Unknown private states require
    lifecycle={status, container} on the event, never a parent-specific adapter.
    """
    if lifecycle is not None:
        if (not isinstance(lifecycle, dict) or lifecycle.keys() != {"status", "container"}
                or lifecycle["status"] not in ("started", "completed", "waiting")
                or type(lifecycle["container"]) is not bool):
            raise ValueError("Invalid explicit checkpoint lifecycle")
        return lifecycle["status"], lifecycle["container"]
    state = payload.get("status")
    if state not in ("started", "completed", "waiting", "responded", "not_applied"):
        raise ValueError("Private checkpoint states require an explicit lifecycle projection")
    return (state if state in ("started", "completed", "waiting") else "completed",
            state != "started" or payload.get("container") is True)


def build_envelope(*, engine_scope, checkpoint_name, local_key, status, container, requires_retry):
    """Common nested-record envelope, independent of the orchestrating Engine.

    The selected child owns payload. The envelope only carries identity and
    lifecycle facts required by persistence, steering and approval bindings.
    Field names are the existing storage contract; no migration is performed.
    """
    if any(not isinstance(value, str) or not value.strip()
           for value in (engine_scope, checkpoint_name, local_key)):
        raise ValueError("Checkpoint envelope requires scope, name and local key")
    if (status not in ("started", "completed", "waiting")
            or type(container) is not bool or type(requires_retry) is not bool
            or requires_retry != (status == "started" and not container)):
        raise ValueError("Invalid checkpoint envelope lifecycle")
    return {"node_type": "engine_record", "engine_scope": engine_scope,
            "checkpoint_name": checkpoint_name, "local_key": local_key,
            "status": status, "container": container, "requires_retry": requires_retry}


def unwrap_record(record):
    """Return one owner's payload and optional scope, not arbitrary nested JSON."""
    if record.get("node_type") != "engine_record":
        return record, None
    build_envelope(**{key: record.get(key) for key in (
        "engine_scope", "checkpoint_name", "local_key", "status", "container", "requires_retry")})
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("Checkpoint envelope requires an object payload")
    return payload, record["engine_scope"]


@dataclass(frozen=True, slots=True)
class ResumeAbandonment(JsonValue):
    """재개 권한의 영구 포기. 실행 취소나 파일 삭제, 외부 효과 취소가 아니다."""
    abandoned_at: str
    reason: str
    request_id: Optional[str] = None

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if not self.abandoned_at or self.reason not in ("explicit", "conversation_deleted"):
            raise ValueError("Invalid resume abandonment record")
        if datetime.fromisoformat(self.abandoned_at).utcoffset() is None:
            raise ValueError("Abandonment time requires a timezone")
        if self.reason == "conversation_deleted" and not self.request_id:
            raise ValueError("Conversation abandonment requires request_id")
        if self.reason == "explicit" and self.request_id is not None:
            raise ValueError("Explicit abandonment has no conversation request")


def require_checkpoint_plan(plan: CheckpointPlan, expected_revision: str, *, action: str) -> None:
    """계획은 권한이 아니다. 소유자가 잠금 안에서 재계산한 계획에만 적용한다."""
    if action not in ("abandon", "delete"):
        raise ValueError("Unknown checkpoint action")
    if not expected_revision or expected_revision != plan.revision:
        raise CheckpointError("checkpoint_conflict", "review a fresh checkpoint plan")
    blockers = plan.abandon_blockers if action == "abandon" else plan.delete_blockers
    if blockers:
        raise CheckpointError("checkpoint_protected", "; ".join(item.message for item in blockers), blockers=blockers)
