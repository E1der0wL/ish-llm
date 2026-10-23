"""Pure quota admission and time-window selection over validated projections."""

from datetime import datetime, timezone

from llm.core.policies import UsageErrorCode
from llm.policies.errors import ExecutionLimitError

UNKNOWN_USAGE = "Unaccounted calls prevent quota admission"
EXHAUSTED = "{} exhausted"
RUN_SCOPE = False
NO_RUN_ENTRIES = ()


def check_admission(policy, reservation, project_entries, run_entries=NO_RUN_ENTRIES):
    """동일 workspace 저장 잠금 안에서 호출한다. 병렬 분기의 예약도 기존 Run 기록에 포함된다."""
    def check(entries, limits):
        maximum = limits.max_calls
        if maximum is not None and len(entries) >= maximum:
            raise ExecutionLimitError(UsageErrorCode.LIMIT, EXHAUSTED.format(limits.calls_label))
        maximum = limits.max_tokens
        if maximum is not None:
            values = [e.amount for e in entries]
            if reservation is None or any(v is None for v in values):
                raise ExecutionLimitError(UsageErrorCode.UNKNOWN, UNKNOWN_USAGE)
            if sum(values) + reservation > maximum:
                raise ExecutionLimitError(UsageErrorCode.LIMIT, EXHAUSTED.format(limits.tokens_label))
    if policy.run.active:
        check(run_entries, policy.run)
    if policy.project.active:
        check(period_entries(project_entries, policy), policy.project)


def has_limit(policy, *, project=RUN_SCOPE):
    """상한이 있는 범위만 과거 영수증을 조회한다. 새 호출 기록 여부와는 무관하다."""
    return (policy.project if project else policy.run).active


def period_entries(entries, policy, *, at=None):
    """The same explicit Project window governs admission, queries and deletion."""
    clock = datetime.now(timezone.utc) if at is None else at
    return [entry for entry in entries if entry.in_period(policy.period_seconds, at=clock)]


