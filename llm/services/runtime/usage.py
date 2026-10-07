"""호출 전 예약을 통한 누적 사용량 제한. 원본은 Run의 Completion 기록이며 프로젝트 합계 파일은 만들지 않는다."""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta

from llm.policies import ExecutionLimitError
from llm.core.models import now, new_id

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

    async def reservation(self, request):
        """토큰 상한 사용 시 입력 계수와 명시적 출력 상한이 필요하다. 누락 사용량은 예약을 유지한다."""
        if self.policy.get("max_tokens") is None and self.policy.get("project_max_tokens") is None:
            return None
        output = request.get("max_completion_tokens", request.get("max_tokens"))
        if type(output) is not int or output < 1:
            raise ExecutionLimitError("usage_configuration", "Token quota requires an explicit completion output limit")
        count = await asyncio.to_thread(self.counter, request)
        if type(count) is not int or count < 0:
            raise ExecutionLimitError("usage_configuration", "Token counter must return a nonnegative integer")
        return count + output


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
            raise ExecutionLimitError("usage_limit", f"{calls_key} exhausted")
        maximum = policy.get(tokens_key)
        if maximum is not None:
            values = [amount(e) for e in entries]
            if reservation is None or any(v is None for v in values):
                raise ExecutionLimitError("usage_unknown", "Unaccounted calls prevent quota admission")
            if sum(values) + reservation > maximum:
                raise ExecutionLimitError("usage_limit", f"{tokens_key} exhausted")
    check([*run.metadata.get("completions", []), *component_entries], "max_calls", "max_tokens")
    if policy.get("project_max_calls") is not None or policy.get("project_max_tokens") is not None:
        seconds = policy.get("period_seconds")
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds) if seconds is not None else None
        entries = [e for e in project_entries
                   if cutoff is None or datetime.fromisoformat(e["started_at"]) >= cutoff]
        check(entries, "project_max_calls", "project_max_tokens")


def component_usage(registry, project):
    """비활성화해도 기존 영수증은 집계한다. 소유 컴포넌트만 자신의 경로를 해석한다."""
    entries = []
    for name in registry.names():
        component = registry.get(name)
        reader = getattr(component, "model_usage", None)
        if reader and component.root(project).exists():
            entries.extend(reader(project))
    return entries


class ComponentUsage:
    """ComponentData에 주입되는 비스트리밍 모델 호출 관찰자. 별도의 Run을 만들지 않는다."""

    def __init__(self, data, sessions, counters):
        self.data, self.sessions, self.counters = data, sessions, counters

    def _get_policies(self):
        project, _ = self.data._current()
        return deepcopy(project.config.policies)

    def _reserve(self, operation, request, reservation, source):
        project, component = self.data._current()
        policies = project.config.policies
        repository = self.sessions.run_repository
        auxiliary = component_usage(self.data.registry, project)
        run = None
        if source is not None:
            if source["project_id"] != project.id:
                raise ValueError("Model usage cannot cross Project boundaries")
            session = self.sessions.load(project, source["session_id"])
            run = repository.load(session, source["run_id"])
            if run.status != "running":
                raise ValueError("Component model call requires an active owning Run")
            policies = run.metadata["policies"]
        entries = []
        if policies.get("usage", {}).get("project_max_calls") is not None or policies.get("usage", {}).get("project_max_tokens") is not None:
            reader = getattr(repository, "completion_history", None)
            entries = [entry for session in self.sessions.list(project, include_deleted=True)
                       for entry in (reader(session) if reader else [e for r in repository.list(session) for e in r.metadata.get("completions", [])])]
        if run is None:
            policies = deepcopy(policies)
            policies.get("usage", {}).update(max_calls=None, max_tokens=None)
            run = SimpleNamespace(metadata={"policies": policies}, id=None)
        owned = [e for e in auxiliary if run.id is not None and e.get("run_id") == run.id]
        check_admission(run, None, reservation, [*entries, *auxiliary], owned)
        entry = {"id": new_id(), "operation": operation, "model": request.get("model"),
                 "component": component.name, "started_at": now(), "status": "started",
                 "usage": {}, "usage_complete": False, "reserved_tokens": reservation, **(source or {})}
        component.save_model_usage(project, entry)
        return entry

    def _finish(self, entry, response, error):
        project, component = self.data._current()
        usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
        usage = usage if isinstance(usage, dict) else {}
        valid = type(usage.get("total_tokens")) is int and usage["total_tokens"] >= 0
        entry.update(ended_at=now(), status="failed" if error else "completed",
                     usage=usage if valid else {}, usage_complete=valid)
        if error:
            # 공급자 예외의 원문에는 endpoint 응답/요청 내용이 섞일 수 있다.
            from llm.providers.requests import error_code
            entry["error"] = error_code(error)
            entry["diagnostic_code"] = error_code(error)
        component.save_model_usage(project, entry)

    async def __call__(self, operation, request, call):
        policies = await self.data._async_call(self._get_policies)
        scope = current_usage()
        source = deepcopy(scope.source) if scope else None
        policy = scope.policy if source else policies.get("usage", {})
        reservation = None
        if policy.get("project_max_tokens") is not None or source and policy.get("max_tokens") is not None:
            counter = scope.counter if source else self.counters.get(policies.get("usage", {}).get("counter"))
            if counter is None:
                raise ExecutionLimitError("usage_configuration", "Component model quota requires a token counter")
            counted = deepcopy(request)
            if operation in ("aembedding", "arerank"):
                payload = request.get("input") if operation == "aembedding" else [request.get("query"), request.get("documents")]
                counted["messages"] = [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
                output = 0
            else:
                output = request.get("max_completion_tokens", request.get("max_tokens"))
                if type(output) is not int or output < 1:
                    raise ExecutionLimitError("usage_configuration", "Component completion quota requires an output token limit")
            count = await asyncio.to_thread(counter, counted)
            if type(count) is not int or count < 0:
                raise ExecutionLimitError("usage_configuration", "Token counter requires a nonnegative integer")
            reservation = count + output
        # 예약과 검사 전체를 같은 workspace 잠금으로 처리하여 다른 Run/색인 작업과 경쟁하지 않는다.
        entry = await self.data._async_call(self._reserve, operation, request, reservation, source)
        try:
            response = await call(**request)
        except BaseException as error:
            await self.data._async_call(self._finish, entry, None, error)
            raise
        await self.data._async_call(self._finish, entry, response, None)
        return response
