"""Explicit Application retention policy over the Backend's eligible history."""

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import ClassVar

from llm.core.models import ProjectConfig, RunStatus
from llm.core.plans import RetentionPlan, RetentionUnit


@dataclass(frozen=True, slots=True)
class HistoryRetentionPolicy:
    MIN_AGE_SECONDS: ClassVar[float] = 0.0
    unit: RetentionUnit
    keep_runs: int = 0
    max_age_seconds: float | None = None
    max_bytes: int | None = None
    max_tokens: int | None = None
    counter_params: dict = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, 'unit', RetentionUnit(self.unit))
        if type(self.keep_runs) is not int or self.keep_runs < 0:
            raise ValueError('keep_runs must be a nonnegative integer')
        for value in (self.max_bytes, self.max_tokens):
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError('Retention limits must be positive integers')
        if self.max_age_seconds is not None and (type(self.max_age_seconds) not in (int, float)
                or not math.isfinite(self.max_age_seconds) or self.max_age_seconds <= self.MIN_AGE_SECONDS):
            raise ValueError('Retention age must be positive and finite')
        if not isinstance(self.counter_params, dict) or 'messages' in self.counter_params:
            raise ValueError('Counter parameters cannot override messages')
        ProjectConfig.validate_json(self.counter_params)
        object.__setattr__(self, 'counter_params', deepcopy(self.counter_params))


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
    def __init__(self, project, policy: HistoryRetentionPolicy, *, counter=None):
        if not isinstance(policy, HistoryRetentionPolicy):
            raise TypeError('History retention requires an Application policy')
        if policy.max_tokens is not None and not callable(counter):
            raise ValueError('Token retention requires an explicitly selected counter')
        self.project, self.policy, self.counter = project, policy, counter

    async def preview(self):
        policy = deepcopy(self.policy)
        measured = policy.max_tokens is not None
        plan = await self.project.aretention_plan(policy.unit,
            counter=self.counter if measured else None, counter_params=policy.counter_params if measured else None)
        keep = set()
        if policy.unit == RetentionUnit.RUN:
            sessions = {}
            for row in (*plan.candidates, *plan.protected):
                if row['status'] == RunStatus.COMPLETED:
                    sessions.setdefault(row['session_id'], []).append(row)
            for rows in sessions.values():
                rows.sort(key=lambda row: (row['last_activity'], row['run_id']), reverse=True)
                keep.update(identifier for row in rows[:policy.keep_runs]
                            for identifier in row['group_run_ids'])
        selected, protected = [], list(plan.protected)
        size, tokens = plan.bytes, plan.tokens
        clock = datetime.now(timezone.utc)
        groups = {}
        for row in plan.candidates:
            key = (row['session_id'], tuple(row['group_run_ids'])) if policy.unit == RetentionUnit.RUN else row['session_id']
            groups.setdefault(key, []).append(row)
        for group in sorted(groups.values(), key=lambda rows: (
                max(row['last_activity'] for row in rows), rows[0]['session_id'], rows[0].get('run_id', ''))):
            if policy.unit == RetentionUnit.RUN and any(row['run_id'] in keep for row in group):
                protected.extend({**row, 'reasons': ['application_keep_runs']} for row in group)
                continue
            expired = policy.max_age_seconds is not None and all((
                clock - datetime.fromisoformat(row['last_activity'])).total_seconds() > policy.max_age_seconds for row in group)
            oversize = policy.max_bytes is not None and size > policy.max_bytes
            overtokens = policy.max_tokens is not None and tokens > policy.max_tokens
            if expired or oversize or overtokens:
                selected.extend(group)
                size -= sum(row['bytes'] for row in group)
                tokens -= sum(row['tokens'] for row in group)
        return HistoryRetentionReview(plan, policy, selected, protected, size, tokens)

    async def apply(self, review):
        if (not isinstance(review, HistoryRetentionReview) or review.policy != self.policy
                or review.source.source.project_id != self.project.id or review.source.unit != self.policy.unit):
            raise ValueError('retention_conflict: review the current Application policy and Project')
        current = await self.preview()
        if review != current:
            raise ValueError('retention_conflict: reviewed selection or source changed')
        fields = ('session_id', 'run_id') if self.policy.unit == RetentionUnit.RUN else ('session_id',)
        targets = [{name: row[name] for name in fields} for row in current.candidates]
        return await self.project.aprune_history(self.policy.unit, targets, expected_version=review.version)
