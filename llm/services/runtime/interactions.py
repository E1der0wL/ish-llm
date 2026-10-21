"""Run 체크포인트의 사용자 요청과 별도 응답 영수증. 호출자는 소유권 잠금을 보유한다."""

from copy import deepcopy
import json
import hashlib
from dataclasses import asdict, dataclass, fields, replace
from contextlib import contextmanager
from datetime import datetime

from llm.core.interactions import (
    InteractionOption, InteractionRequest, InteractionResponse, InteractionStatus, InteractionView,
    approval_request, same_interaction_value,
)
from llm.core.checkpoints import CheckpointStatus
from llm.core.models import RunStatus, ProjectConfig, Run
from llm.services.infrastructure.storage import atomic_json, child, read_json


@dataclass(frozen=True, slots=True)
class InteractionRecord:
    """The complete persisted control state, including an unanswered request."""

    STORAGE_VERSION = 1

    storage_version: int
    run_id: str
    request_id: str
    request_revision: int
    request_fingerprint: str
    cancelled: bool
    response: dict | None
    replacement: dict | None

    def __post_init__(self):
        if type(self.storage_version) is not int or self.storage_version != self.STORAGE_VERSION:
            raise ValueError("Unsupported interaction storage_version")
        InteractionRequest.validate_identity(self.request_id)
        InteractionRequest.validate_fingerprint(self.request_fingerprint)
        if not isinstance(self.run_id, str) or not self.run_id:
            raise ValueError("Interaction record requires its Run")
        if type(self.request_revision) is not int or self.request_revision < 1:
            raise ValueError("Invalid interaction record revision")
        if type(self.cancelled) is not bool:
            raise ValueError("Interaction cancellation must be a boolean")
        if self.response is not None:
            response = InteractionResponse.from_dict(self.response)
            if (response.request_id != self.request_id or response.request_revision != self.request_revision
                    or response.request_fingerprint != self.request_fingerprint):
                raise ValueError("Interaction response identity changed")
        if self.replacement is not None:
            replacement = InteractionRequest.from_dict(self.replacement)
            if (replacement.id == self.request_id or replacement.revision != self.request_revision + 1
                    or replacement.status != InteractionStatus.PENDING):
                raise ValueError("Invalid interaction renewal chain")

    @classmethod
    def initial(cls, run, request):
        return cls(cls.STORAGE_VERSION, run.id, request.id, request.revision,
                   request.fingerprint, False, None, None)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError("Interaction record fields changed or are incomplete")
        ProjectConfig.validate_json(value)
        return cls(**deepcopy(value))

    def to_dict(self):
        return asdict(self)

    def validate_request(self, request):
        if (request.id != self.request_id or request.revision != self.request_revision
                or request.fingerprint != self.request_fingerprint):
            raise ValueError("Interaction request changed after publication")
        if self.response is not None:
            response = InteractionResponse.from_dict(self.response)
            request.decision_for(response.option_id, value=response.value)
        if self.replacement is not None:
            replacement = InteractionRequest.from_dict(self.replacement)
            expected = replace(request, id=replacement.id, revision=request.revision + 1,
                               created_at=replacement.created_at, expires_at=replacement.expires_at,
                               status=InteractionStatus.PENDING)
            if not same_interaction_value(replacement.to_dict(), expected.to_dict()):
                raise ValueError("Renewal cannot change the execution target or choices")
            if not self.cancelled and (request.expires_at is None or
                    datetime.fromisoformat(request.expires_at) > datetime.fromisoformat(replacement.created_at)):
                raise ValueError("Renewal requires cancellation or expiry evidence")


class InteractionRepository:
    """요청은 체크포인트가 원본이다. 응답 저장만으로 작업을 실행하거나 큐에 넣지 않는다."""

    RETRY_CATEGORY = "execution.retry_uncertain"

    def __init__(self):
        self._envelopes = None

    def _root(self, run):
        path = run.paths.state / "interactions"
        for item in (path, *path.parents):
            if item.is_symlink():
                raise ValueError("Interaction paths cannot follow links")
        return path

    def _path(self, run, request):
        path = child(self._root(run), request.id).with_suffix(".json")
        if path.is_symlink():
            raise ValueError("Interaction paths cannot follow links")
        return path

    def _save(self, run, request, data):
        path = self._path(run, request)
        value = InteractionRecord.from_dict(data)
        if value.run_id != run.id:
            raise ValueError("Interaction response Run mismatch")
        value.validate_request(request)
        atomic_json(path, value.to_dict())
        if self._envelopes is not None:
            self._envelopes.pop(path, None)

    def initialize(self, run, request):
        """Only request publication creates pending state; reads never create it."""
        if self._path(run, request).exists():
            raise ValueError("Interaction already initialized")
        self._save(run, request, InteractionRecord.initial(run, request).to_dict())

    def publish(self, run, request, *, existing):
        """Checkpoint writers must distinguish a new request from retained proof."""
        if existing or self._path(run, request).exists():
            self.envelope(run, request)
        else:
            self.initialize(run, request)

    def inherit(self, source, run, checkpoint):
        """Copy existing approval evidence when creating a distinct resume Run."""
        originals = {r.binding["key"]: r for r in self.requests(checkpoint, source)}
        for request in self.requests(checkpoint, run):
            original = originals[request.binding["key"]]
            if request.id != original.id:
                self.effective(source, [original])
                # An uncertain operation in a new attempt needs its own retry
                # approval. The prior attempt's response remains in the source.
                self.initialize(run, request)
                continue
            visited = set()
            while True:
                if request.id in visited:
                    raise ValueError("Invalid interaction renewal chain")
                visited.add(request.id)
                value = self.envelope(source, request)
                if self._path(run, request).exists():
                    raise ValueError("Inherited interaction already exists")
                self._save(run, request, {**value, "run_id": run.id})
                if value["replacement"] is None:
                    break
                request = InteractionRequest.from_dict(value["replacement"])

    @contextmanager
    def read_scope(self):
        """한 workspace 잠금 작업 안에서만 읽기를 재사용한다. 다음 작업에는 남기지 않는다."""
        previous, self._envelopes = self._envelopes, {}
        try:
            yield self
        finally:
            if previous is not None:
                previous.clear()
            self._envelopes = previous

    def requests(self, checkpoint: dict, run: Run) -> list[InteractionRequest]:
        values = []
        ids = set()
        for key, record in checkpoint["records"].items():
            if record.get("status") == CheckpointStatus.STARTED and record.get("requires_retry"):
                request = approval_request("결과가 불확실한 작업 재실행", category=self.RETRY_CATEGORY,
                    source={"run_id": run.id, "session_id": run.session_id}, action={"checkpoint_record": deepcopy(record)})
                # The same node can start a distinct attempt after resume. Its
                # prior evidence must not be overwritten under the same ID.
                identity = hashlib.sha256(json.dumps([run.id, checkpoint.get("name"), key, record],
                    sort_keys=True, allow_nan=False).encode()).hexdigest()[:InteractionRequest.ID_LENGTH]
                request = replace(request, id=identity, created_at=run.created_at,
                    binding={"checkpoint": checkpoint.get("name", ""), "key": key, "target": "retry_nodes"})
            elif record.get("status") == CheckpointStatus.WAITING and "interaction" in record:
                request = InteractionRequest.from_dict(record["interaction"])
            else:
                continue
            if request.binding.get("key") != key or request.id in ids:
                raise ValueError("Interaction checkpoint identity mismatch")
            ids.add(request.id)
            values.append(request)
        return values

    def envelope(self, run, request):
        path = self._path(run, request)
        if self._envelopes is not None and path in self._envelopes:
            value = self._envelopes[path]
        else:
            value = InteractionRecord.from_dict(read_json(path))
            if value.run_id != run.id:
                raise ValueError("Interaction response Run mismatch")
            if self._envelopes is not None:
                self._envelopes[path] = value
        value.validate_request(request)
        return value.to_dict()

    def effective(self, run, requests):
        result = []
        for original in requests:
            request, visited = original, set()
            while True:
                # 순환은 무결성 오류다. 정상 체인의 길이를 제품 숫자로 제한하지 않는다.
                if request.id in visited:
                    raise ValueError("Invalid interaction renewal chain")
                visited.add(request.id)
                data = self.envelope(run, request)
                if data["replacement"] is None:
                    break
                replacement = InteractionRequest.from_dict(data["replacement"])
                if replacement.binding != request.binding or replacement.action != request.action:
                    raise ValueError("Renewal cannot change the execution target")
                request = replacement
            result.append(request)
        return result

    def cancel(self, run, request):
        data = self.envelope(run, request)
        if data["replacement"] is not None:
            raise ValueError("Request has already been renewed")
        data["cancelled"] = True
        self._save(run, request, data)

    def renew(self, run, request, *, expires_at=None):
        from llm.core.models import new_id, now
        data = self.envelope(run, request)
        if not request.expired and not data["cancelled"]:
            raise ValueError("Only expired or cancelled requests may be renewed")
        if data["replacement"] is not None:
            raise ValueError("Request has already been renewed")
        replacement = replace(request, id=new_id(), revision=request.revision + 1,
                              created_at=now(), expires_at=expires_at, status=InteractionStatus.PENDING)
        if replacement.expired:
            raise ValueError("Renewal expiry must be in the future")
        data["replacement"] = replacement.to_dict()
        self.initialize(run, replacement)
        self._save(run, request, data)
        return replacement

    def responses(self, run: Run, requests: list[InteractionRequest]) -> list[InteractionResponse]:
        values = []
        for request in requests:
            item = self.envelope(run, request)
            if item["response"] is not None:
                response = InteractionResponse.from_dict(item["response"])
                if (response.request_fingerprint != request.fingerprint or response.request_id != request.id
                        or response.request_revision != request.revision
                        or response.option_id not in {o.id for o in request.options}):
                    raise ValueError("Interaction changed after responding")
                values.append(response)
        return values

    def respond(self, run: Run, requests: list[InteractionRequest], response: InteractionResponse) -> InteractionResponse:
        if not isinstance(response, InteractionResponse):
            raise TypeError("response must be an InteractionResponse")
        request = next((r for r in requests if r.id == response.request_id), None)
        if request is None:
            raise ValueError("Interaction does not belong to this Run checkpoint")
        control = self.envelope(run, request)
        if control["cancelled"]:
            raise ValueError("Interaction is cancelled")
        if control["replacement"] is not None:
            raise ValueError("Request has already been renewed")
        response.decision(request)
        prior = self.responses(run, [request])
        if prior:
            # 같은 선택 재전송은 멱등, 다른 선택으로 승인 기록을 덮어쓰는 것은 금지한다.
            before, after = prior[0].to_dict(), response.to_dict()
            before.pop("created_at")
            after.pop("created_at")
            if before != after:
                raise ValueError("Interaction was already answered differently")
            return prior[0]
        self._save(run, request, {**self.envelope(run, request), "response": response.to_dict()})
        return deepcopy(response)

    def decisions(self, requests: list[InteractionRequest], responses: list[InteractionResponse],
                  explicit: dict, *, retry_nodes=(), confirm=False):
        """공통 응답을 엔진의 기존 재개 값으로 변환하고 상충하는 응답을 거부한다."""
        stored = {v.request_id: v for v in responses}
        decisions = deepcopy(explicit)
        receipts = []
        retries = list(retry_nodes)
        for request in requests:
            key = request.binding["key"]
            response = stored.get(request.id)
            if request.binding.get("target") == "retry_nodes":
                if response is None and key in retries:
                    response = request.respond("approve")
                    receipts.append(response)
                if response is not None:
                    if response.decision(request) is not True:
                        raise ValueError("Uncertain execution retry was denied")
                    if key not in retries:
                        retries.append(key)
                continue
            if response is None and key not in decisions and confirm and request.kind == InteractionRequest.KIND_CONFIRMATION:
                option = request.find_approval()
                if option is None:
                    raise ValueError("Confirmation requires an explicit option and any required input")
                response = request.respond(option.id)
                receipts.append(response)
            if response is not None:
                value = response.decision(request)
                if key in decisions:
                    try:
                        option, supplied = request.select_decision(decisions[key], confirm_empty=True)
                        explicit_value = request.decision_for(option.id, value=supplied)
                    except (ValueError, TypeError) as error:
                        raise ValueError("Explicit decision conflicts with stored interaction response") from error
                    if not same_interaction_value(explicit_value, value):
                        raise ValueError("Explicit decision conflicts with stored interaction response")
                decisions[key] = value
            elif key in decisions:
                # 저수준 엔진 재개 API도 동일한 선택지/입력 검증과 응답 영수증을 사용한다.
                option, supplied = request.select_decision(decisions[key], confirm_empty=True)
                response = request.respond(option.id, value=supplied)
                decisions[key] = response.decision(request)
                receipts.append(response)
            elif request.expired:
                raise ValueError("Interaction has expired")
        return decisions, receipts, tuple(retries)


    def views(self, run, requests, responses, *, resume_message=None, resumed_run=None, resume_abandoned=False):
        answers = {v.request_id: v for v in responses}
        views = []
        for request in requests:
            response = answers.get(request.id)
            control = self.envelope(run, request)
            reason = None
            if resume_abandoned:
                status, reason = "unavailable", "resume_abandoned"
            elif resumed_run is not None or resume_message is not None:
                status, reason = "submitted", "already_resumed"
            elif str(run.status) not in (RunStatus.PAUSED, RunStatus.INTERRUPTED, RunStatus.FAILED):
                status, reason = "unavailable", "run_active_or_finished"
            elif control["cancelled"]:
                status, reason = "cancelled", "request_cancelled"
            elif request.expired:
                status, reason = "expired", "request_expired"
            elif response is not None:
                option = next(o for o in request.options if o.id == response.option_id)
                status = "denied" if option.effect == InteractionOption.EFFECT_DENY else "answered"
            else:
                status = "pending"
            views.append(InteractionView(request, response, status, status == "pending",
                status == "answered", reason,
                resumed_run.id if resumed_run else None,
                str(resumed_run.status) if resumed_run else "queued" if resume_message else None))
        ready = bool(views) and all(v.can_resume for v in views)
        return [replace(v, can_resume=ready) for v in views]

    def validate_backup(self, run):
        """Validate this owner's archived controls, including superseded ones."""
        root = self._root(run)
        records = {}
        for path in root.glob("*.json"):
            if path.is_symlink():
                raise ValueError("Interaction paths cannot follow links")
            value = InteractionRecord.from_dict(read_json(child(root, path.stem).with_suffix(".json")))
            if value.run_id != run.id or value.request_id != path.stem:
                raise ValueError("Interaction backup ownership mismatch")
            records[value.request_id] = value
        for value in records.values():
            if value.replacement is not None:
                request = InteractionRequest.from_dict(value.replacement)
                if request.id not in records:
                    raise ValueError("Interaction renewal record is missing")
                records[request.id].validate_request(request)
