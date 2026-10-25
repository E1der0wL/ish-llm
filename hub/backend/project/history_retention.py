"""Explicit Application retention policy over the Backend's eligible history."""

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import ClassVar

from llm.core.models import RunStatus
from llm.core.json_data import JsonObject
from llm.core.plans import RetentionPlan, RetentionUnit
from llm.services.history.retention_records import RetentionRow, RetentionCounter


@dataclass(frozen=True, slots=True)
class HistoryRetentionPolicy:
    MIN_AGE_SECONDS: ClassVar[float] = 0.0
    MIN_KEEP_RUNS: ClassVar[int] = 0
    MIN_LIMIT: ClassVar[int] = 1
    ABSENT: ClassVar[None] = None
    INVALID_KEEP_RUNS: ClassVar[str] = "keep_runs must be a nonnegative integer"
    INVALID_LIMIT: ClassVar[str] = "Retention limits must be positive integers"
    INVALID_AGE: ClassVar[str] = "Retention age must be positive and finite"
    INVALID_COUNTER_PARAMETERS: ClassVar[str] = (
        "Counter parameters cannot override messages"
    )
    unit: RetentionUnit
    keep_runs: int = 0
    max_age_seconds: float | None = None
    max_bytes: int | None = None
    max_tokens: int | None = None
    counter_params: dict = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(
            self, HistoryRetentionPolicy.unit.__name__, RetentionUnit(self.unit)
        )
        if type(self.keep_runs) is not int or self.keep_runs < self.MIN_KEEP_RUNS:
            raise ValueError(self.INVALID_KEEP_RUNS)
        for value in (self.max_bytes, self.max_tokens):
            if value is not self.ABSENT and (
                type(value) is not int or value < self.MIN_LIMIT
            ):
                raise ValueError(self.INVALID_LIMIT)
        if self.max_age_seconds is not self.ABSENT and (
            type(self.max_age_seconds) not in (int, float)
            or not math.isfinite(self.max_age_seconds)
            or self.max_age_seconds <= self.MIN_AGE_SECONDS
        ):
            raise ValueError(self.INVALID_AGE)
        RetentionCounter.validate_parameters(
            self.counter_params, error_message=self.INVALID_COUNTER_PARAMETERS
        )
        JsonObject.validate(self.counter_params)
        object.__setattr__(
            self,
            HistoryRetentionPolicy.counter_params.__name__,
            deepcopy(self.counter_params),
        )


@dataclass(frozen=True, slots=True)
class HistoryRetentionReview:
    source: RetentionPlan
    policy: HistoryRetentionPolicy
    candidates: list[dict]
    protected: list[dict]
    bytes_after: int
    tokens_after: int

    @property
    def bytes_before(self):
        return self.source.bytes

    @property
    def tokens_before(self):
        return self.source.tokens

    @property
    def version(self):
        return self.source.version


class HistoryRetention:
    ABSENT = None
    NEWEST_FIRST = True
    NO_RUN_ID = ""
    KEEP_REASON = "application_keep_runs"
    INVALID_POLICY = "History retention requires an Application policy"
    MISSING_COUNTER = "Token retention requires an explicitly selected counter"
    POLICY_CONFLICT = (
        "retention_conflict: review the current Application policy and Project"
    )
    SELECTION_CONFLICT = "retention_conflict: reviewed selection or source changed"

    def __init__(self, project, policy: HistoryRetentionPolicy, *, counter=ABSENT):
        if not isinstance(policy, HistoryRetentionPolicy):
            raise TypeError(self.INVALID_POLICY)
        if policy.max_tokens is not self.ABSENT and not callable(counter):
            raise ValueError(self.MISSING_COUNTER)
        self.project, self.policy, self.counter = project, policy, counter

    async def preview(self):
        policy = deepcopy(self.policy)
        measured = policy.max_tokens is not self.ABSENT
        plan = await self.project.aretention_plan(
            policy.unit,
            counter=self.counter if measured else self.ABSENT,
            counter_params=policy.counter_params if measured else self.ABSENT,
        )
        keep = set()
        if policy.unit == RetentionUnit.RUN:
            sessions = {}
            for row in (*plan.candidates, *plan.protected):
                if RetentionRow(row).status == RunStatus.COMPLETED:
                    sessions.setdefault(RetentionRow(row).session_id, []).append(row)
            for rows in sessions.values():
                rows.sort(
                    key=lambda row: (
                        RetentionRow(row).last_activity,
                        RetentionRow(row).run_id,
                    ),
                    reverse=self.NEWEST_FIRST,
                )
                keep.update(
                    identifier
                    for row in rows[: policy.keep_runs]
                    for identifier in RetentionRow(row).group_run_ids
                )
        selected, protected = [], list(plan.protected)
        size, tokens = plan.bytes, plan.tokens
        clock = datetime.now(timezone.utc)
        groups = {}
        for row in plan.candidates:
            key = (
                (RetentionRow(row).session_id, tuple(RetentionRow(row).group_run_ids))
                if policy.unit == RetentionUnit.RUN
                else RetentionRow(row).session_id
            )
            groups.setdefault(key, []).append(row)
        for group in sorted(
            groups.values(),
            key=lambda rows: (
                max(RetentionRow(row).last_activity for row in rows),
                RetentionRow(rows[0]).session_id,
                RetentionRow(rows[0]).optional_run_id(self.NO_RUN_ID),
            ),
        ):
            if policy.unit == RetentionUnit.RUN and any(
                RetentionRow(row).run_id in keep for row in group
            ):
                protected.extend(
                    RetentionRow(row).with_reasons([self.KEEP_REASON]).record
                    for row in group
                )
                continue
            expired = policy.max_age_seconds is not self.ABSENT and all(
                (
                    clock - datetime.fromisoformat(RetentionRow(row).last_activity)
                ).total_seconds()
                > policy.max_age_seconds
                for row in group
            )
            oversize = policy.max_bytes is not self.ABSENT and size > policy.max_bytes
            overtokens = (
                policy.max_tokens is not self.ABSENT and tokens > policy.max_tokens
            )
            if expired or oversize or overtokens:
                selected.extend(group)
                size -= sum(RetentionRow(row).bytes for row in group)
                tokens -= sum(RetentionRow(row).tokens for row in group)
        return HistoryRetentionReview(plan, policy, selected, protected, size, tokens)

    async def apply(self, review):
        if (
            not isinstance(review, HistoryRetentionReview)
            or review.policy != self.policy
            or review.source.source.project_id != self.project.id
            or review.source.unit != self.policy.unit
        ):
            raise ValueError(self.POLICY_CONFLICT)
        current = await self.preview()
        if review != current:
            raise ValueError(self.SELECTION_CONFLICT)
        targets = [
            RetentionRow(row).target(self.policy.unit) for row in current.candidates
        ]
        return await self.project.aprune_history(
            self.policy.unit, targets, expected_version=review.version
        )
