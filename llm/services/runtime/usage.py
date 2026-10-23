"""호출 전 예약을 통한 누적 사용량 제한. 원본은 Run의 Completion 기록이며 프로젝트 합계 파일은 만들지 않는다."""

from llm.core.policies import UsageErrorCode, UsagePolicy
from llm.core.usage import TokenUsage, UsageReceipt, UsageCall
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from llm.policies import ExecutionLimitError
from llm.policies.usage import check_admission, has_limit, period_entries
from llm.core.models import now, Run, RunStatus
from llm.core.results import CompletionRecords
from llm.components.usage import ModelUsageRecord
from llm.components.base import Component
from llm.providers.observations import ModelCall

MISSING_REPOSITORY = "Quota evidence requires a Run repository"
INACTIVE_OWNER = "Session quota owner is not an active Run"
INCLUDE_DELETED_EVIDENCE = True


class TokenReservation:
    INVALID_COUNTER = "Token counter must return a nonnegative integer"
    UNRESERVED = None

    @classmethod
    async def count(cls, invocation, counter):
        value = await asyncio.to_thread(invocation.count_tokens, counter)
        if not TokenUsage.valid_count(value):
            raise ExecutionLimitError(UsageErrorCode.CONFIGURATION, cls.INVALID_COUNTER)
        return value


def current_usage():
    return UsageScope.current()


class UsageScope:
    NAME = "llm_usage_scope"
    UNBOUND = None
    _CURRENT = ContextVar(NAME, default=UNBOUND)

    @staticmethod
    def current():
        return UsageScope._CURRENT.get()

    @staticmethod
    def is_bound():
        return UsageScope._CURRENT.get() is not UsageScope.UNBOUND

    def __init__(self, policy, counter, source=UNBOUND):
        self.policy, self.counter = policy, counter
        self.source = source

    @contextmanager
    def scope(self):
        token = UsageScope._CURRENT.set(self)
        try:
            yield self
        finally:
            UsageScope._CURRENT.reset(token)

    async def reservation(self, invocation: ModelCall):
        """토큰 상한 사용 시 입력 계수와 명시적 출력 상한이 필요하다. 누락 사용량은 예약을 유지한다."""
        if not self.policy.requires_reservation:
            return TokenReservation.UNRESERVED
        return await TokenReservation.count(invocation, self.counter)


def project_usage_policies(project, sessions):
    """Read current policy and active Run snapshots under workspace ownership."""
    policies = [UsagePolicy.from_policies(project.config.policies)]
    for session in sessions.list(project, include_deleted=INCLUDE_DELETED_EVIDENCE):
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
        reader = getattr(component, Component.model_usage.__name__, None)
        if reader:
            entries.extend(reader(project))
    return entries


def completion_usage(repository, sessions):
    """Read the Repository projection, including injected repositories without a catalog."""
    from .runs import RunRepository
    reader = getattr(repository, RunRepository.completion_history.__name__, None)
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
            entries = completion_usage(repository, self.sessions.list(project, include_deleted=INCLUDE_DELETED_EVIDENCE))
        if run is None:
            policy = policy.without_run_limits()
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
        reservation = TokenReservation.UNRESERVED
        if policy.project_max_tokens is not None or source and policy.max_tokens is not None:
            counter = scope.counter if source else self.counters.get(project_policy.counter)
            if counter is None:
                raise ExecutionLimitError(UsageErrorCode.CONFIGURATION, self.MISSING_COUNTER)
            reservation = await TokenReservation.count(invocation, counter)
        # 예약과 검사 전체를 같은 workspace 잠금으로 처리하여 다른 Run/색인 작업과 경쟁하지 않는다.
        entry = await self.data._async_call(self._reserve, invocation, reservation, source)
        try:
            response = await call(**request)
        except BaseException as error:
            await self.data._async_call(self._finish, invocation, entry, None, error)
            raise
        await self.data._async_call(self._finish, invocation, entry, response, None)
        return response
