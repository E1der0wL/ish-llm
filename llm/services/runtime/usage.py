"""호출 전 예약을 통한 누적 사용량 제한. 원본은 Run의 Completion 기록이며 프로젝트 합계 파일은 만들지 않는다."""

from llm.core.policies import UsageErrorCode
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta

from llm.policies import ExecutionLimitError
from llm.core.models import now, new_id, Run, RunStatus, SessionStatus
from llm.core.results import CompletionRecords
from llm.components.usage import ModelUsageRecord, ModelUsageStatus
from llm.providers.observations import ModelCall

_current = ContextVar("llm_usage_scope", default=None)


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
        if self.policy.get("max_tokens") is None and self.policy.get("project_max_tokens") is None:
            return None
        count = await asyncio.to_thread(invocation.count_tokens, self.counter)
        if type(count) is not int or count < 0:
            raise ExecutionLimitError("usage_configuration", "Token counter must return a nonnegative integer")
        return count


def check_admission(run, result, reservation, project_entries, component_entries=()):
    """동일 workspace 저장 잠금 안에서 호출한다. 병렬 분기의 예약도 기존 Run 기록에 포함된다."""
    policy = run.metadata["policies"].get("usage", {})
    def amount(entry):
        tokens = entry.get("usage", {}).get("total_tokens")
        if entry.get("usage_complete") and type(tokens) is int:
            return tokens
        return entry.get("reserved_tokens")
    def check(entries, calls_key, tokens_key):
        maximum = policy.get(calls_key)
        if maximum is not None and len(entries) >= maximum:
            raise ExecutionLimitError(UsageErrorCode.LIMIT, f"{calls_key} exhausted")
        maximum = policy.get(tokens_key)
        if maximum is not None:
            values = [amount(e) for e in entries]
            if reservation is None or any(v is None for v in values):
                raise ExecutionLimitError("usage_unknown", "Unaccounted calls prevent quota admission")
            if sum(values) + reservation > maximum:
                raise ExecutionLimitError(UsageErrorCode.LIMIT, f"{tokens_key} exhausted")
    if has_limit(policy):
        check([*CompletionRecords.read(run.metadata), *component_entries], "max_calls", "max_tokens")
    if has_limit(policy, project=True):
        check(period_entries(project_entries, policy), "project_max_calls", "project_max_tokens")


def has_limit(policy, *, project=False):
    """상한이 있는 범위만 과거 영수증을 조회한다. 새 호출 기록 여부와는 무관하다."""
    prefix = "project_" if project else ""
    return any(policy.get(prefix + key) is not None for key in ("max_calls", "max_tokens"))


def period_entries(entries, policy, *, at=None):
    """The same explicit Project window governs admission, queries and deletion."""
    seconds = policy.get("period_seconds")
    if seconds is None:
        return list(entries)
    clock = datetime.now(timezone.utc) if at is None else at
    cutoff = clock - timedelta(seconds=seconds)
    selected = []
    for entry in entries:
        started = datetime.fromisoformat(entry["started_at"])
        if started.utcoffset() is None:
            raise ValueError("Usage period evidence requires a timezone")
        if started >= cutoff:
            selected.append(entry)
    return selected


def project_usage_policies(project, sessions):
    """Read current policy and active Run snapshots under workspace ownership."""
    policies = [project.config.policies.get("usage", {})]
    for session in sessions.list(project, include_deleted=True):
        if sessions.run_repository is None:
            raise ValueError("Quota evidence requires a Run repository")
        runs = sessions.run_repository.list(session)
        by_id = {run.id: run for run in runs}
        if any(identifier not in by_id or by_id[identifier].status != RunStatus.RUNNING
               for identifier in session.active_run_ids):
            raise ValueError("Session quota owner is not an active Run")
        # Dispatched recipients waiting behind their Agent's FIFO predecessor
        # already own their admitted policy, even after a sibling has finished.
        policies.extend(run.metadata["policies"].get("usage", {})
                        for run in runs if run.status in Run.ACTIVE_STATES)
    return policies


def protects_project_usage(entries, policies, *, at=None):
    """Evidence required by any live quota cannot be physically removed."""
    clock = datetime.now(timezone.utc) if at is None else at
    return any(has_limit(policy, project=True) and period_entries(entries, policy, at=clock)
               for policy in policies)


def component_usage(registry, project):
    """비활성화해도 기존 영수증은 집계한다. 소유 컴포넌트만 자신의 경로를 해석한다."""
    entries = []
    registry.stored(project)
    for name in registry.names():
        component = registry.get(name)
        reader = getattr(component, "model_usage", None)
        if reader:
            entries.extend(reader(project))
    return entries


class ComponentUsage:
    """ComponentData에 주입되는 비스트리밍 모델 호출 관찰자. 별도의 Run을 만들지 않는다."""

    def __init__(self, data, sessions, counters):
        self.data, self.sessions, self.counters = data, sessions, counters

    def _get_policies(self):
        project, _ = self.data._current()
        return deepcopy(project.config.policies)

    def _reserve(self, invocation, reservation, source):
        project, component = self.data._current()
        policies = project.config.policies
        repository = self.sessions.run_repository
        run = None
        if source is not None:
            if not isinstance(source, dict) or source.keys() != {"project_id", "session_id", "run_id"}:
                raise ValueError("Component model call requires current source ownership fields")
            if source["project_id"] != project.id:
                raise ValueError("Model usage cannot cross Project boundaries")
            session = self.sessions.load(project, source["session_id"])
            run = repository.load(session, source["run_id"])
            if run.status != RunStatus.RUNNING:
                raise ValueError("Component model call requires an active owning Run")
            policies = run.metadata["policies"]
        policy = policies.get("usage", {})
        project_limited = has_limit(policy, project=True)
        auxiliary = component_usage(self.data.registry, project) if (
            project_limited or run is not None and has_limit(policy)) else []
        entries = []
        if project_limited:
            reader = getattr(repository, "completion_history", None)
            entries = [entry for session in self.sessions.list(project, include_deleted=True)
                       for entry in (reader(session) if reader else [e for r in repository.list(session) for e in CompletionRecords.read(r.metadata)])]
        if run is None:
            policies = deepcopy(policies)
            policies.get("usage", {}).update(max_calls=None, max_tokens=None)
            run = SimpleNamespace(metadata={"policies": policies}, id=None)
        owned = [e for e in auxiliary if run.id is not None and e.get("run_id") == run.id]
        check_admission(run, None, reservation, [*entries, *auxiliary], owned)
        entry = ModelUsageRecord(schema_version=ModelUsageRecord.SCHEMA_VERSION,
            id=new_id(), project_id=project.id, component=component.name,
            operation=invocation.operation, model=invocation.model,
            session_id=source["session_id"] if source is not None else None,
            run_id=source["run_id"] if source is not None else None,
            started_at=now(), ended_at=None, status=ModelUsageStatus.STARTED,
            usage={}, usage_complete=False, reserved_tokens=reservation,
            error=None, diagnostic_code=None).to_dict()
        component.save_model_usage(project, entry)
        return entry

    def _finish(self, invocation, entry, response, error):
        project, component = self.data._current()
        usage = invocation.read_usage(response) if error is None else {}
        if not isinstance(usage, dict):
            raise TypeError("Model usage must be a normalized dictionary")
        valid = type(usage.get("total_tokens")) is int and usage["total_tokens"] >= 0
        entry.update(ended_at=now(), status=ModelUsageStatus.FAILED if error is not None else ModelUsageStatus.COMPLETED,
                     usage=usage if valid else {}, usage_complete=valid)
        if error is not None:
            # 공급자 예외의 원문에는 endpoint 응답/요청 내용이 섞일 수 있다.
            code = invocation.classify_error(error)
            if not isinstance(code, str) or not code:
                raise TypeError("Model error classification requires a code")
            entry["error"] = code
            entry["diagnostic_code"] = code
        component.save_model_usage(project, entry)

    async def __call__(self, invocation: ModelCall, request, call):
        if not isinstance(invocation, ModelCall):
            raise TypeError("Model observation requires ModelCall")
        policies = await self.data._async_call(self._get_policies)
        scope = current_usage()
        source = deepcopy(scope.source) if scope else None
        policy = scope.policy if source else policies.get("usage", {})
        reservation = None
        if policy.get("project_max_tokens") is not None or source and policy.get("max_tokens") is not None:
            counter = scope.counter if source else self.counters.get(policies.get("usage", {}).get("counter"))
            if counter is None:
                raise ExecutionLimitError("usage_configuration", "Component model quota requires a token counter")
            count = await asyncio.to_thread(invocation.count_tokens, counter)
            if type(count) is not int or count < 0:
                raise ExecutionLimitError("usage_configuration", "Token counter requires a nonnegative integer")
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
