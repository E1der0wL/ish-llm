"""Task 소유 외부 작업 원장. 예약을 저장한 뒤 실행하고 불확실한 작업은 재시도하지 않는다."""

from copy import deepcopy
import hashlib
import json
from typing import Any, TYPE_CHECKING

from llm.core.models import ProjectConfig, Task, now
from llm.services.infrastructure.storage import atomic_json, read_json, sync_directory
from llm.services.runtime.policies import ExecutionLimitError

if TYPE_CHECKING:
    from llm.services.runtime.tools import ToolCall


def operation_token(project_id: str, task_id: str, key: str) -> str:
    if not isinstance(key, str) or not key or len(key) > 512:
        raise ValueError("Operation key must contain 1..512 characters")
    return hashlib.sha256(json.dumps([project_id, task_id, key], ensure_ascii=False).encode()).hexdigest()


class OperationRepository:
    """호출자는 workspace 소유권 잠금/StorageIO를 유지해야 한다. 임의 reset API는 없다."""

    def _path(self, task, key):
        token = operation_token(task.project_id, task.id, key)
        path = task.paths.state / "tool_operations" / (token + ".json")
        for item in (path, *path.parents):
            if item.is_symlink():
                raise ValueError("Operation paths cannot follow links")
        return path

    def _complete(self, task, value, result, evidence):
        ProjectConfig.validate_settings({"result": result})
        value.update(status="completed", result=deepcopy(result), ended_at=now(), evidence=evidence)
        atomic_json(self._path(task, value["key"]), value)

    def load(self, task: Task, key: str) -> dict:
        value = read_json(self._path(task, key))
        if (value["schema_version"] != 1 or value["task_id"] != task.id
                or value["project_id"] != task.project_id or value["key"] != key
                or value["idempotency_key"] != operation_token(task.project_id, task.id, key)
                or value["status"] not in ("started", "completed", "not_applied")):
            raise ValueError("Invalid operation record")
        return value

    def claim(self, task: Task, call: "ToolCall") -> dict:
        path = self._path(task, call.operation_key)
        request = {"name": call.name, "arguments": call.arguments}
        if call.contract is not None:
            request["contract"] = call.contract
        ProjectConfig.validate_settings(request)
        digest = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False,
                                          allow_nan=False).encode()).hexdigest()
        if path.exists():
            value = self.load(task, call.operation_key)
            if value["request_digest"] != digest:
                raise ExecutionLimitError("operation_conflict", "Operation key was used for a different Tool or arguments")
            if value["status"] == "not_applied":
                value.setdefault("attempts", []).append({"run_id": value["run_id"], "step_id": value["step_id"],
                    "status": "not_applied", "evidence": value.get("evidence"),
                    "created_at": value["created_at"], "ended_at": value["ended_at"]})
                value.pop("ended_at", None)
                value.pop("evidence", None)
                value.update(status="started", run_id=call.run_id, step_id=call.step_id, created_at=now())
                atomic_json(path, value)
                return {"reused": False}
            if value["status"] != "completed":
                raise ExecutionLimitError("operation_uncertain", "Operation may have executed; reconcile its external result before reuse")
            return {"reused": True, "result": deepcopy(value["result"])}
        atomic_json(path, {"schema_version": 1, "project_id": task.project_id, "task_id": task.id,
                          "key": call.operation_key, "idempotency_key": call.idempotency_key,
                          "request_digest": digest, "name": call.name, "arguments": deepcopy(call.arguments),
                          "contract": deepcopy(call.contract), "status": "started",
                          "run_id": call.run_id, "step_id": call.step_id, "created_at": now()})
        # 처음 만든 state/tool_operations 디렉토리의 부모 엔트리도 POSIX에서 동기화한다.
        sync_directory(path.parent.parent)
        sync_directory(task.paths.root)
        return {"reused": False}

    def complete(self, task: Task, call: "ToolCall", result: Any) -> None:
        value = self.load(task, call.operation_key)
        if value["status"] != "started" or (value["run_id"], value["step_id"]) != (call.run_id, call.step_id):
            raise ValueError("Operation completion owner mismatch")
        self._complete(task, value, result, "execution")

    def not_applied(self, task, call, evidence):
        """실행 소유자가 효과 없음으로 분류한 실패만 기록한다. 불확실한 원장을 초기화하지 않는다."""
        value = self.load(task, call.operation_key)
        if value["status"] != "started" or (value["run_id"], value["step_id"]) != (call.run_id, call.step_id):
            raise ValueError("Operation failure owner mismatch")
        value.update(status="not_applied", ended_at=now(), evidence=str(evidence))
        atomic_json(self._path(task, call.operation_key), value)

    def reconcile(self, task: Task, key: str, *, result: Any, evidence: str) -> None:
        """외부 시스템에서 확인한 결과만 명시적으로 확정한다. 호출자는 Task 비활성을 검증한다."""
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("External reconciliation evidence is required")
        value = self.load(task, key)
        if value["status"] != "started":
            raise ValueError("Only uncertain operations can be reconciled")
        self._complete(task, value, result, evidence)

    def verify(self, task, key, observation, expected_version):
        """호스트 조회 결과만 CAS로 반영한다. 확인 과정 자체는 외부 효과를 실행하지 않는다."""
        from llm.services.infrastructure.storage import revision_token
        value = self.load(task, key)
        if revision_token(value) != expected_version or value["status"] != "started":
            raise ValueError("Operation changed during verification")
        ProjectConfig.validate_settings(observation)
        status = observation.get("status")
        if status not in ("completed", "not_applied", "uncertain"):
            raise ValueError("Invalid external operation observation")
        if not isinstance(observation.get("evidence"), str) or not observation["evidence"].strip():
            raise ValueError("External observation requires evidence")
        if status == "completed" and "result" not in observation:
            raise ValueError("Completed observation requires result, including explicit null")
        value.setdefault("observations", []).append({**deepcopy(observation), "at": now()})
        if status == "completed":
            self._complete(task, value, observation["result"], observation["evidence"])
        else:
            if status == "not_applied":
                value.update(status=status, ended_at=now(), evidence=observation["evidence"])
            atomic_json(self._path(task, key), value)
        return self.load(task, key)


class ToolOperations:
    """Engine에 노출하는 서비스 핸들. 파일 위치와 저장소 구현은 Engine에서 알 필요가 없다."""

    def __init__(self, task, io, repository, *, steps=None):
        self.task, self.io, self.repository = task, io, repository
        self.steps = steps

    def _persist(self, method, call, *args):
        value = method(self.task, call, *args)
        if self.steps is not None:
            record_operation_step(self.repository, self.steps, self.task,
                                  self.repository.tool_operation(self.task, call.operation_key),
                                  run_id=call.run_id, step_id=call.step_id)
        return value

    async def claim(self, call):
        return await self.io.run(self._persist, self.repository.claim_tool_operation, call)

    async def complete(self, call, result):
        await self.io.run(self._persist, self.repository.complete_tool_operation, call, result)

    async def not_applied(self, call, evidence):
        await self.io.run(self._persist, self.repository.fail_tool_operation, call, evidence)


def record_operation_step(repository, steps, task, receipt, *, run_id=None, step_id=None):
    """원장과 관찰 Step을 함께 확정한다. 완료 이벤트나 외부 실행을 기다리지 않는다.

    Step 상태는 Engine 이벤트가 소유한다. 원장 영수증은 효과의 재사용 여부를
    결정하는 원본이며 재시작으로 Step이 interrupted여도 그 결과를 잃지 않는다.
    """
    run = repository.load(task, run_id or receipt["run_id"])
    step = steps.load(run, step_id or receipt["step_id"])
    step.metadata["operation_receipt"] = {
        "key": receipt["key"], "status": receipt["status"],
        "run_id": receipt["run_id"], "step_id": receipt["step_id"],
        "ended_at": receipt.get("ended_at"), "evidence": receipt.get("evidence")}
    steps.save(step)
