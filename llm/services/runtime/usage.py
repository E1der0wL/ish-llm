"""호출 전 예약을 통한 누적 사용량 제한. 원본은 Run의 Completion 기록이며 프로젝트 합계 파일은 만들지 않는다."""

from llm.core.policies import UsageErrorCode, UsagePolicy
from llm.core.usage import UsageReceipt, UsageCall
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from llm.policies import ExecutionLimitError
from llm.policies.usage import check_admission, has_limit, period_entries
from llm.core.models import now, Run, RunStatus
from llm.core.results import CompletionRecords
from llm.components.usage import ModelUsageRecord
from llm.providers.observations import ModelCall

_current = ContextVar("llm_usage_scope", default=None)

INVALID_COUNTER = "Token counter must return a nonnegative integer"
MISSING_REPOSITORY = "Quota evidence requires a Run repository"
INACTIVE_OWNER = "Session quota owner is not an active Run"
MODEL_USAGE_HOOK = "model_usage"
HISTORY_HOOK = "completion_history"
MINIMUM_RESERVATION = 0


def current_usage():
    return _current.get()


class UsageScope:
    def __init__(self, policy, counter, source=None):
        self.policy, self.counter = policy, counter
        self.source = source

    @contextmanager
    def scope(self):
        token = _current.set(self)
        try:
            yield self
        finally:
            _current.reset(token)

    async def reservation(self, invocation: ModelCall):
        """토큰 상한 사용 시 입력 계수와 명시적 출력 상한이 필요하다. 누락 사용량은 예약을 유지한다."""
        if not self.policy.requires_reservation:
            return None
        count = await asyncio.to_thread(invocation.count_tokens, self.counter)
        if type(count) is not int or count < MINIMUM_RESERVATION:
            raise ExecutionLimitError(UsageErrorCode.CONFIGURATION, INVALID_COUNTER)
        return count


def project_usage_policies(project, sessions):
    """Read current policy and active Run snapshots under workspace ownership."""
    policies = [UsagePolicy.from_policies(project.config.policies)]
    for session in sessions.list(project, include_deleted=True):
        if sessions.run_repository is None:
            raise ValueError(MISSING_REPOSITORY)
        runs = sessions.run_repository.list(session)
        by_id = {run.id: run for run in runs}
        if any(identifier not in by_id or by_id[identifier].status != RunStatus.RUNNING
               for identifier in session.active_run_ids):
            raise ValueError(INACTIVE_OWNER)
        # Dispatched recipients waiting behind their Agent's FIFO predecessor
        # already own their admitted policy, even after a sibling has finished.
        policies.extend(UsagePolicy.from_run(run)
                        for run in runs if run.status in Run.ACTIVE_STATES)
    return policies


def protects_project_usage(entries, policies, *, at=None):
    """Evidence required by any live quota cannot be physically removed."""
    clock = datetime.now(timezone.utc) if at is None else at
    receipts = [UsageReceipt.from_record(entry) for entry in entries]
    return any(policy.project.active and period_entries(receipts, policy, at=clock)
               for policy in policies)


def component_usage(registry, project):
    """비활성화해도 기존 영수증은 집계한다. 소유 컴포넌트만 자신의 경로를 해석한다."""
    entries = []
    registry.stored(project)
    for name in registry.names():
        component = registry.get(name)
        reader = getattr(component, MODEL_USAGE_HOOK, None)
        if reader:
            entries.extend(reader(project))
    return entries


def completion_usage(repository, sessions):
    """Read the Repository projection, including injected repositories without a catalog."""
    reader = getattr(repository, HISTORY_HOOK, None)
    return [UsageReceipt.from_record(entry) for session in sessions
            for entry in (reader(session) if reader else [e for run in repository.list(session)
                for e in CompletionRecords.usage(run.metadata)])]


class ComponentUsage:
    """ComponentData에 주입되는 비스트리밍 모델 호출 관찰자. 별도의 Run을 만들지 않는다."""

    CROSS_PROJECT = "Model usage cannot cross Project boundaries"
    INACTIVE_RUN = "Component model call requires an active owning Run"
    MISSING_COUNTER = "Component model quota requires a token counter"
    INVALID_INVOCATION = "Model observation requires ModelCall"

    def __init__(self, data, sessions, counters):
        self.data, self.sessions, self.counters = data, sessions, counters

    def _get_policies(self):
        project, _ = self.data._current()
        return UsagePolicy.from_policies(project.config.policies)

    def _reserve(self, invocation, reservation, source):
        project, component = self.data._current()
        policy = UsagePolicy.from_policies(project.config.policies)
        repository = self.sessions.run_repository
        run = None
        if source is not None:
            if source.project_id != project.id:
                raise ValueError(self.CROSS_PROJECT)
            session = self.sessions.load(project, source.session_id)
            run = repository.load(session, source.run_id)
            if run.status != RunStatus.RUNNING:
                raise ValueError(self.INACTIVE_RUN)
            policy = UsagePolicy.from_run(run)
        project_limited = policy.project.active
        auxiliary = component_usage(self.data.registry, project) if (
            project_limited or run is not None and policy.run.active) else []
        entries = []
        if project_limited:
            entries = completion_usage(repository, self.sessions.list(project, include_deleted=True))
        if run is None:
            from dataclasses import replace
            policy = replace(policy, max_calls=None, max_tokens=None)
        owned = [] if run is None else [UsageReceipt.from_result(result) for result in CompletionRecords.results(run.metadata)]
        owned.extend(UsageReceipt.from_record(e) for e in auxiliary if run is not None and UsageCall.belongs_to_run(e, run.id))
        check_admission(policy, reservation, [*entries, *[UsageReceipt.from_record(e) for e in auxiliary]], owned)
        entry = ModelUsageRecord.reserve(project_id=project.id, component=component.name,
            invocation=invocation, source=source, reservation=reservation)
        component.save_model_usage(project, entry.to_dict())
        return entry

    def _finish(self, invocation, entry, response, error):
        project, component = self.data._current()
        usage = invocation.read_usage(response) if error is None else {}
        code = None
        if error is not None:
            # 공급자 예외의 원문에는 endpoint 응답/요청 내용이 섞일 수 있다.
            code = invocation.classify_error(error)
            if not isinstance(code, str) or not code:
                raise TypeError(ModelUsageRecord.INVALID_DIAGNOSTIC)
        result = entry.finish(ended_at=now(), usage=usage, diagnostic_code=code)
        component.save_model_usage(project, result.to_dict())

    async def __call__(self, invocation: ModelCall, request, call):
        if not isinstance(invocation, ModelCall):
            raise TypeError(self.INVALID_INVOCATION)
        project_policy = await self.data._async_call(self._get_policies)
        scope = current_usage()
        source = scope.source if scope else None
        policy = scope.policy if source else project_policy
        reservation = None
        if policy.project_max_tokens is not None or source and policy.max_tokens is not None:
            counter = scope.counter if source else self.counters.get(project_policy.counter)
            if counter is None:
                raise ExecutionLimitError(UsageErrorCode.CONFIGURATION, self.MISSING_COUNTER)
            count = await asyncio.to_thread(invocation.count_tokens, counter)
            if type(count) is not int or count < MINIMUM_RESERVATION:
                raise ExecutionLimitError(UsageErrorCode.CONFIGURATION, INVALID_COUNTER)
            reservation = count
        # 예약과 검사 전체를 같은 workspace 잠금으로 처리하여 다른 Run/색인 작업과 경쟁하지 않는다.
        entry = await self.data._async_call(self._reserve, invocation, reservation, source)
        try:
            response = await call(**request)
        except BaseException as error:
            await self.data._async_call(self._finish, invocation, entry, None, error)
            raise
        await self.data._async_call(self._finish, invocation, entry, response, None)
        return response
