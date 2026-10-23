"""Session 소유 외부 작업 원장. 예약을 저장한 뒤 실행하고 불확실한 작업은 재시도하지 않는다."""

from llm.core.models import RunErrorCode
from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, TYPE_CHECKING

from llm.core.models import Session, now
from llm.core.json_data import JsonObject
from llm.services.infrastructure.storage import atomic_json, read_json, revision_token, reject_links
from llm.policies import ExecutionLimitError

if TYPE_CHECKING:
    from llm.services.runtime.tools import ToolCall


def operation_token(project_id: str, session_id: str, key: str) -> str:
    if not isinstance(key, str) or not key or len(key) > 512:
        raise ValueError("Operation key must contain 1..512 characters")
    return hashlib.sha256(json.dumps([project_id, session_id, key], ensure_ascii=False).encode()).hexdigest()


class OperationStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    NOT_APPLIED = "not_applied"


OBSERVATION_STATUSES = {OperationStatus.COMPLETED, OperationStatus.NOT_APPLIED, "uncertain"}


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Operation timestamp must be text")
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("Operation timestamp requires a timezone")
    return stamp


def _evidence(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Operation evidence is required")


def _request_digest(name, arguments, contract):
    return revision_token({"name": name, "arguments": arguments, "contract": contract})


@dataclass(frozen=True)
class OperationRecord:
    """Current ledger fields; nullable state is explicit, never supplied by a reader."""

    SCHEMA_VERSION = 1
    schema_version: int
    project_id: str
    session_id: str
    key: str
    idempotency_key: str
    request_digest: str
    name: str
    arguments: dict
    contract: dict
    status: str
    run_id: str
    step_id: str
    created_at: str
    ended_at: str | None
    evidence: str | None
    result: Any
    attempts: list
    observations: list

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError("Operation record requires all current fields")
        JsonObject.validate(value)
        return cls(**deepcopy(value))

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != self.SCHEMA_VERSION:
            raise ValueError("Invalid operation schema version")
        for name in ("project_id", "session_id", "run_id", "step_id", "name"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"Operation requires {name}")
        if self.idempotency_key != operation_token(self.project_id, self.session_id, self.key):
            raise ValueError("Operation identity mismatch")
        from llm.core.tool_contracts import ToolContract
        ToolContract.from_dict(self.contract)
        if not isinstance(self.arguments, dict) or self.request_digest != _request_digest(self.name, self.arguments, self.contract):
            raise ValueError("Operation request digest mismatch")
        status = OperationStatus(self.status)
        created = _timestamp(self.created_at)
        if status == OperationStatus.STARTED:
            if self.ended_at is not None or self.evidence is not None or self.result is not None:
                raise ValueError("Started operation cannot claim a terminal result")
        else:
            _evidence(self.evidence)
            if _timestamp(self.ended_at) < created:
                raise ValueError("Operation ended before it started")
            if status == OperationStatus.NOT_APPLIED and self.result is not None:
                raise ValueError("Not-applied operation cannot claim a result")
        if not isinstance(self.attempts, list) or not isinstance(self.observations, list):
            raise ValueError("Operation requires attempt and observation lists")
        previous_end = None
        for attempt in self.attempts:
            if not isinstance(attempt, dict) or set(attempt) != {"run_id", "step_id", "status", "evidence", "created_at", "ended_at"}:
                raise ValueError("Invalid operation attempt fields")
            if attempt["status"] != OperationStatus.NOT_APPLIED:
                raise ValueError("Only proven not-applied attempts can precede reuse")
            if any(not isinstance(attempt[key], str) or not attempt[key] for key in ("run_id", "step_id")):
                raise ValueError("Operation attempt requires its owner")
            _evidence(attempt["evidence"])
            start, end = _timestamp(attempt["created_at"]), _timestamp(attempt["ended_at"])
            if end < start or end > created or (previous_end is not None and start < previous_end):
                raise ValueError("Operation attempt chronology mismatch")
            previous_end = end
        previous_at = None
        for observation in self.observations:
            self.validate_observation(observation, stored=True)
            at = _timestamp(observation["at"])
            if previous_at is not None and at < previous_at:
                raise ValueError("Operation observations are out of order")
            previous_at = at

    @staticmethod
    def validate_observation(value, *, stored=False):
        required = {"status", "evidence", *(["at"] if stored else [])}
        if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - {"result"}:
            raise ValueError("Invalid external operation observation fields")
        JsonObject.validate(value)
        if value["status"] not in OBSERVATION_STATUSES:
            raise ValueError("Invalid external operation observation")
        _evidence(value["evidence"])
        if ("result" in value) != (value["status"] == OperationStatus.COMPLETED):
            raise ValueError("Only completed observations require result, including explicit null")


class OperationRepository:
    """호출자는 workspace 소유권 잠금/StorageIO를 유지해야 한다. 임의 reset API는 없다."""

    SCHEMA_VERSION = 1

    def _root(self, session):
        return reject_links(session.paths.state / "tool_operations")

    def initialize(self, session):
        """Only new Session creation establishes the empty ledger."""
        root = self._root(session)
        if root.exists():
            raise ValueError("Operation ledger already exists")
        atomic_json(root / "manifest.json", {"schema_version": self.SCHEMA_VERSION,
            "project_id": session.project_id, "session_id": session.id, "records": {}})

    def _manifest(self, session):
        root = self._root(session)
        value = read_json(reject_links(root / "manifest.json"))
        if (not isinstance(value, dict) or set(value) != {"schema_version", "project_id", "session_id", "records"}
                or type(value["schema_version"]) is not int or value["schema_version"] != self.SCHEMA_VERSION
                or value["project_id"] != session.project_id or value["session_id"] != session.id
                or not isinstance(value["records"], dict)):
            raise ValueError("Invalid operation manifest")
        for token, revision in value["records"].items():
            if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{64}", token) or not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{64}", revision):
                raise ValueError("Invalid operation manifest entry")
        files = {reject_links(path).name for path in root.iterdir()}
        if files != {"manifest.json", *(token + ".json" for token in value["records"])}:
            raise ValueError("Operation ledger files are missing or undeclared")
        return value

    def has_records(self, session):
        return bool(self._manifest(session)["records"])

    def validate_backup(self, session):
        manifest = self._manifest(session)
        for token in manifest["records"]:
            self._read(session, token, manifest)

    def _read(self, session, token, manifest):
        value = read_json(reject_links(self._root(session) / (token + ".json")))
        record = OperationRecord.from_dict(value)
        if (record.project_id != session.project_id or record.session_id != session.id
                or record.idempotency_key != token or revision_token(value) != manifest["records"][token]):
            raise ValueError("Operation owner or stored revision mismatch")
        return asdict(record)

    def _save(self, session, value):
        record = OperationRecord.from_dict(value)
        manifest = self._manifest(session)
        if record.project_id != session.project_id or record.session_id != session.id:
            raise ValueError("Operation save owner mismatch")
        manifest["records"][record.idempotency_key] = revision_token(value)
        atomic_json(self._path(session, record.key), value)
        atomic_json(self._root(session) / "manifest.json", manifest)

    def _path(self, session, key):
        token = operation_token(session.project_id, session.id, key)
        path = self._root(session) / (token + ".json")
        for item in (path, *path.parents):
            if item.is_symlink():
                raise ValueError("Operation paths cannot follow links")
        return path

    def _complete(self, session, value, result, evidence):
        JsonObject.validate({"result": result})
        value.update(status=OperationStatus.COMPLETED, result=deepcopy(result), ended_at=now(), evidence=evidence)
        self._save(session, value)

    def load(self, session: Session, key: str) -> dict:
        manifest = self._manifest(session)
        token = operation_token(session.project_id, session.id, key)
        if token not in manifest["records"]:
            raise FileNotFoundError("Operation has not been recorded")
        return self._read(session, token, manifest)

    def claim(self, session: Session, call: "ToolCall") -> dict:
        manifest = self._manifest(session)
        request = {"name": call.name, "arguments": call.arguments, "contract": call.contract}
        JsonObject.validate(request)
        digest = _request_digest(call.name, call.arguments, call.contract)
        token = operation_token(session.project_id, session.id, call.operation_key)
        if token in manifest["records"]:
            value = self.load(session, call.operation_key)
            if value["request_digest"] != digest:
                raise ExecutionLimitError(RunErrorCode.OPERATION_CONFLICT, "Operation key was used for a different Tool or arguments")
            if value["status"] == OperationStatus.NOT_APPLIED:
                value["attempts"].append({"run_id": value["run_id"], "step_id": value["step_id"],
                    "status": OperationStatus.NOT_APPLIED, "evidence": value["evidence"],
                    "created_at": value["created_at"], "ended_at": value["ended_at"]})
                value.update(status=OperationStatus.STARTED, run_id=call.run_id, step_id=call.step_id,
                             created_at=now(), ended_at=None, evidence=None, result=None)
                self._save(session, value)
                return {"reused": False}
            if value["status"] != OperationStatus.COMPLETED:
                raise ExecutionLimitError(RunErrorCode.OPERATION_UNCERTAIN, "Operation may have executed; reconcile its external result before reuse")
            return {"reused": True, "result": deepcopy(value["result"])}
        self._save(session, {"schema_version": OperationRecord.SCHEMA_VERSION, "project_id": session.project_id, "session_id": session.id,
                          "key": call.operation_key, "idempotency_key": call.idempotency_key,
                          "request_digest": digest, "name": call.name, "arguments": deepcopy(call.arguments),
                          "contract": deepcopy(call.contract), "status": OperationStatus.STARTED,
                          "run_id": call.run_id, "step_id": call.step_id, "created_at": now(),
                          "ended_at": None, "evidence": None, "result": None, "attempts": [], "observations": []})
        return {"reused": False}

    def complete(self, session: Session, call: "ToolCall", result: Any) -> None:
        value = self.load(session, call.operation_key)
        if value["status"] != OperationStatus.STARTED or (value["run_id"], value["step_id"]) != (call.run_id, call.step_id):
            raise ValueError("Operation completion owner mismatch")
        self._complete(session, value, result, "execution")

    def not_applied(self, session, call, evidence):
        """실행 소유자가 효과 없음으로 분류한 실패만 기록한다. 불확실한 원장을 초기화하지 않는다."""
        value = self.load(session, call.operation_key)
        if value["status"] != OperationStatus.STARTED or (value["run_id"], value["step_id"]) != (call.run_id, call.step_id):
            raise ValueError("Operation failure owner mismatch")
        value.update(status=OperationStatus.NOT_APPLIED, ended_at=now(), evidence=evidence)
        self._save(session, value)

    def reconcile(self, session: Session, key: str, *, result: Any, evidence: str) -> None:
        """외부 시스템에서 확인한 결과만 명시적으로 확정한다. 호출자는 Session 비활성을 검증한다."""
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError("External reconciliation evidence is required")
        value = self.load(session, key)
        if value["status"] != OperationStatus.STARTED:
            raise ValueError("Only uncertain operations can be reconciled")
        self._complete(session, value, result, evidence)

    def verify(self, session, key, observation, expected_version):
        """호스트 조회 결과만 CAS로 반영한다. 확인 과정 자체는 외부 효과를 실행하지 않는다."""
        value = self.load(session, key)
        if revision_token(value) != expected_version or value["status"] != OperationStatus.STARTED:
            raise ValueError("Operation changed during verification")
        OperationRecord.validate_observation(observation)
        status = observation["status"]
        value["observations"].append({**deepcopy(observation), "at": now()})
        if status == OperationStatus.COMPLETED:
            self._complete(session, value, observation["result"], observation["evidence"])
        else:
            if status == OperationStatus.NOT_APPLIED:
                value.update(status=status, ended_at=now(), evidence=observation["evidence"])
            self._save(session, value)
        return self.load(session, key)


class ToolOperations:
    """Engine에 노출하는 서비스 핸들. 파일 위치와 저장소 구현은 Engine에서 알 필요가 없다."""

    def __init__(self, session, io, repository, *, steps=None):
        self.session, self.io, self.repository = session, io, repository
        self.steps = steps

    def _persist(self, method, call, *args):
        value = method(self.session, call, *args)
        if self.steps is not None:
            record_operation_step(self.repository, self.steps, self.session,
                                  self.repository.tool_operation(self.session, call.operation_key),
                                  run_id=call.run_id, step_id=call.step_id)
        return value

    async def claim(self, call):
        return await self.io.run(self._persist, self.repository.claim_tool_operation, call)

    async def complete(self, call, result):
        await self.io.run(self._persist, self.repository.complete_tool_operation, call, result)

    async def not_applied(self, call, evidence):
        await self.io.run(self._persist, self.repository.fail_tool_operation, call, evidence)


def record_operation_step(repository, steps, session, receipt, *, run_id=None, step_id=None):
    """원장과 관찰 Step을 함께 확정한다. 완료 이벤트나 외부 실행을 기다리지 않는다.

    Step 상태는 Engine 이벤트가 소유한다. 원장 영수증은 효과의 재사용 여부를
    결정하는 원본이며 재시작으로 Step이 interrupted여도 그 결과를 잃지 않는다.
    """
    run = repository.load(session, run_id or receipt["run_id"])
    step = steps.load(run, step_id or receipt["step_id"])
    step.metadata["operation_receipt"] = {
        "key": receipt["key"], "status": receipt["status"],
        "run_id": receipt["run_id"], "step_id": receipt["step_id"],
        "ended_at": receipt["ended_at"], "evidence": receipt["evidence"]}
    steps.save(step)
