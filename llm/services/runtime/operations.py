"""Session 소유 외부 작업 원장. 예약을 저장한 뒤 실행하고 불확실한 작업은 재시도하지 않는다."""

from typing import Any, TYPE_CHECKING

from llm.core.models import RunErrorCode, Session, now
from llm.core.json_data import JsonObject
from llm.core.step_tool_metadata import ToolStepMetadata, StepOperationReceipt, OperationReceiptView
from llm.services.infrastructure.storage import atomic_json, read_json, revision_token, reject_links
from llm.policies import ExecutionLimitError
from .operation_records import (
    OperationIdentity,
    OperationStatus,
    OperationEvidence,
    OperationRequest,
    OperationStorage,
    OperationManifest,
    OperationAttempt,
    OperationObservation,
    OperationClaim,
    OperationRecord,
)

if TYPE_CHECKING:
    from llm.services.runtime.tools import ToolCall


OBSERVATION_STATUSES = OperationObservation.STATUSES


def operation_token(project_id: str, session_id: str, key: str) -> str:
    return OperationIdentity.token(project_id, session_id, key)


def _timestamp(value):
    return OperationEvidence.timestamp(value)


def _evidence(value):
    OperationEvidence.validate(value)


def _request_digest(name, arguments, contract):
    return OperationRequest.digest(name, arguments, contract)


class OperationRepository:
    """호출자는 workspace 소유권 잠금/StorageIO를 유지해야 한다. 임의 reset API는 없다."""

    SCHEMA_VERSION = OperationManifest.SCHEMA_VERSION
    ALREADY_INITIALIZED = "Operation ledger already exists"
    INCOMPLETE_INVENTORY = "Operation ledger files are missing or undeclared"
    OWNER_REVISION_MISMATCH = "Operation owner or stored revision mismatch"
    SAVE_OWNER_MISMATCH = "Operation save owner mismatch"
    LINKED_PATH = "Operation paths cannot follow links"
    MISSING_RECORD = "Operation has not been recorded"
    CONFLICT = "Operation key was used for a different Tool or arguments"
    UNCERTAIN = "Operation may have executed; reconcile its external result before reuse"
    COMPLETION_OWNER = "Operation completion owner mismatch"
    FAILURE_OWNER = "Operation failure owner mismatch"
    MISSING_RECONCILIATION = "External reconciliation evidence is required"
    NOT_UNCERTAIN = "Only uncertain operations can be reconciled"
    VERIFICATION_CONFLICT = "Operation changed during verification"

    def _root(self, session):
        return reject_links(OperationStorage.root(session))

    def initialize(self, session):
        """Only new Session creation establishes the empty ledger."""
        root = self._root(session)
        if root.exists():
            raise ValueError(self.ALREADY_INITIALIZED)
        atomic_json(
            root / OperationStorage.MANIFEST, OperationManifest.create(session, self.SCHEMA_VERSION).to_dict()
        )

    def _manifest(self, session):
        root = self._root(session)
        value = read_json(reject_links(root / OperationStorage.MANIFEST))
        manifest = OperationManifest.read(value, session, self.SCHEMA_VERSION)
        files = {reject_links(path).name for path in root.iterdir()}
        if files != manifest.filenames:
            raise ValueError(self.INCOMPLETE_INVENTORY)
        return manifest

    def has_records(self, session):
        return self._manifest(session).has_records

    def validate_backup(self, session):
        manifest = self._manifest(session)
        for token in manifest.tokens:
            self._read(session, token, manifest)

    def _read(self, session, token, manifest):
        value = read_json(reject_links(self._root(session) / OperationStorage.record_name(token)))
        record = OperationRecord.from_dict(value)
        if (
            record.project_id != session.project_id
            or record.session_id != session.id
            or record.idempotency_key != token
            or revision_token(value) != manifest.revision(token)
        ):
            raise ValueError(self.OWNER_REVISION_MISMATCH)
        return record.to_dict()

    def _save(self, session, value):
        record = OperationRecord.from_dict(value)
        manifest = self._manifest(session)
        if record.project_id != session.project_id or record.session_id != session.id:
            raise ValueError(self.SAVE_OWNER_MISMATCH)
        manifest.record_revision(record.idempotency_key, revision_token(value))
        atomic_json(self._path(session, record.key), value)
        atomic_json(self._root(session) / OperationStorage.MANIFEST, manifest.to_dict())

    def _path(self, session, key):
        token = operation_token(session.project_id, session.id, key)
        path = self._root(session) / OperationStorage.record_name(token)
        for item in (path, *path.parents):
            if item.is_symlink():
                raise ValueError(self.LINKED_PATH)
        return path

    def _complete(self, session, record, result, evidence):
        OperationEvidence.validate_result(result)
        self._save(session, record.completed(result, evidence, now()).to_dict())

    def load(self, session: Session, key: str) -> dict:
        manifest = self._manifest(session)
        token = operation_token(session.project_id, session.id, key)
        if not manifest.contains(token):
            raise FileNotFoundError(self.MISSING_RECORD)
        return self._read(session, token, manifest)

    def _load_record(self, session, key):
        # Preserve the public load/injection boundary and reuse the one complete Reader.
        return OperationRecord.from_dict(self.load(session, key))

    def claim(self, session: Session, call: "ToolCall") -> dict:
        manifest = self._manifest(session)
        request = OperationRequest.create(call.name, call.arguments, call.contract)
        JsonObject.validate(request)
        digest = _request_digest(call.name, call.arguments, call.contract)
        token = operation_token(session.project_id, session.id, call.operation_key)
        if manifest.contains(token):
            value = self._load_record(session, call.operation_key)
            if value.request_digest != digest:
                raise ExecutionLimitError(RunErrorCode.OPERATION_CONFLICT, self.CONFLICT)
            if value.status == OperationStatus.NOT_APPLIED:
                self._save(session, value.restart(call, now()).to_dict())
                return OperationClaim.started()
            if value.status != OperationStatus.COMPLETED:
                raise ExecutionLimitError(RunErrorCode.OPERATION_UNCERTAIN, self.UNCERTAIN)
            return OperationClaim.completed(value.result)
        self._save(session, OperationRecord.initial(session, call, digest, now()).to_dict())
        return OperationClaim.started()

    def complete(self, session: Session, call: "ToolCall", result: Any) -> None:
        value = self._load_record(session, call.operation_key)
        if value.status != OperationStatus.STARTED or (value.run_id, value.step_id) != (
            call.run_id,
            call.step_id,
        ):
            raise ValueError(self.COMPLETION_OWNER)
        self._complete(session, value, result, OperationEvidence.EXECUTION)

    def not_applied(self, session, call, evidence):
        """실행 소유자가 효과 없음으로 분류한 실패만 기록한다. 불확실한 원장을 초기화하지 않는다."""
        value = self._load_record(session, call.operation_key)
        if value.status != OperationStatus.STARTED or (value.run_id, value.step_id) != (
            call.run_id,
            call.step_id,
        ):
            raise ValueError(self.FAILURE_OWNER)
        self._save(session, value.not_applied(evidence, now()).to_dict())

    def reconcile(self, session: Session, key: str, *, result: Any, evidence: str) -> None:
        """외부 시스템에서 확인한 결과만 명시적으로 확정한다. 호출자는 Session 비활성을 검증한다."""
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError(self.MISSING_RECONCILIATION)
        value = self._load_record(session, key)
        if value.status != OperationStatus.STARTED:
            raise ValueError(self.NOT_UNCERTAIN)
        self._complete(session, value, result, evidence)

    def verify(self, session, key, observation, expected_version):
        """호스트 조회 결과만 CAS로 반영한다. 확인 과정 자체는 외부 효과를 실행하지 않는다."""
        raw = self.load(session, key)
        value = OperationRecord.from_dict(raw)
        if revision_token(raw) != expected_version or value.status != OperationStatus.STARTED:
            raise ValueError(self.VERIFICATION_CONFLICT)
        observed = OperationObservation.read(observation)
        status = observed.status
        value = value.observed(observed, now())
        if status == OperationStatus.COMPLETED:
            self._complete(session, value, observed.result, observed.evidence)
        else:
            if status == OperationStatus.NOT_APPLIED:
                value = value.not_applied(observed.evidence, now())
            self._save(session, value.to_dict())
        return self.load(session, key)


class ToolOperations:
    """Engine에 노출하는 서비스 핸들. 파일 위치와 저장소 구현은 Engine에서 알 필요가 없다."""

    NO_STEPS = None

    def __init__(self, session, io, repository, *, steps=NO_STEPS):
        self.session, self.io, self.repository = session, io, repository
        self.steps = steps

    def _persist(self, method, call, *args):
        value = method(self.session, call, *args)
        if self.steps is not self.NO_STEPS:
            record_operation_step(
                self.repository,
                self.steps,
                self.session,
                self.repository.tool_operation(self.session, call.operation_key),
                run_id=call.run_id,
                step_id=call.step_id,
            )
        return value

    async def claim(self, call):
        return await self.io.run(self._persist, self.repository.claim_tool_operation, call)

    async def complete(self, call, result):
        await self.io.run(self._persist, self.repository.complete_tool_operation, call, result)

    async def not_applied(self, call, evidence):
        await self.io.run(self._persist, self.repository.fail_tool_operation, call, evidence)


def record_operation_step(
    repository,
    steps,
    session,
    receipt,
    *,
    run_id=StepOperationReceipt.NO_OVERRIDE,
    step_id=StepOperationReceipt.NO_OVERRIDE,
):
    """원장과 관찰 Step을 함께 확정한다. 완료 이벤트나 외부 실행을 기다리지 않는다.

    Step 상태는 Engine 이벤트가 소유한다. 원장 영수증은 효과의 재사용 여부를
    결정하는 원본이며 재시작으로 Step이 interrupted여도 그 결과를 잃지 않는다.
    """
    receipt = OperationReceiptView(receipt)
    run = repository.load(session, run_id or receipt.run_id)
    step = steps.load(run, step_id or receipt.step_id)
    ToolStepMetadata.attach_operation(step.metadata, receipt.snapshot())
    steps.save(step)
