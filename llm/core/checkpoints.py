"""체크포인트 수명 공통 계약. 파일과 Engine/Component payload는 소유자가 관리한다."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .contracts import JsonValue
from .plans import CheckpointPlan


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
        raise ValueError("checkpoint_conflict: review a fresh checkpoint plan")
    blockers = plan.abandon_blockers if action == "abandon" else plan.delete_blockers
    if blockers:
        raise ValueError("checkpoint_protected: " + "; ".join(item.message for item in blockers))
