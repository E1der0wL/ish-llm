"""Run 체크포인트의 사용자 요청과 별도 응답 영수증. 호출자는 소유권 잠금을 보유한다."""

from copy import deepcopy
from dataclasses import replace
from contextlib import contextmanager

from llm.core.interactions import (
    InteractionOption,
    InteractionRequest,
    InteractionResponse,
    InteractionView,
    same_interaction_value,
)
from llm.core.checkpoints import CheckpointStatus
from llm.core.checkpoint_records import CheckpointInteractions, CheckpointStep
from llm.core.models import RunStatus, Run
from llm.services.infrastructure.storage import atomic_json, child, read_json, tree_snapshot
from .interaction_records import InteractionRecord, InteractionControl, InteractionStorage, RetryInteraction


class InteractionRepository:
    """요청은 체크포인트가 원본이다. 응답 저장만으로 작업을 실행하거나 큐에 넣지 않는다."""

    RETRY_CATEGORY = RetryInteraction.CATEGORY
    RETRY_NODES_TARGET = RetryInteraction.TARGET
    LINKED_PATH = "Interaction paths cannot follow links"
    RUN_MISMATCH = "Interaction response Run mismatch"
    ALREADY_INITIALIZED = "Interaction already initialized"
    ALREADY_INHERITED = "Inherited interaction already exists"
    CHANGED_RENEWAL = "Renewal cannot change the execution target"
    ALREADY_RENEWED = "Request has already been renewed"
    NOT_RENEWABLE = "Only expired or cancelled requests may be renewed"
    EXPIRED_RENEWAL = "Renewal expiry must be in the future"
    INVALID_RESPONSE = "response must be an InteractionResponse"
    UNOWNED_REQUEST = "Interaction does not belong to this Run checkpoint"
    CANCELLED = "Interaction is cancelled"
    CONFLICTING_RESPONSE = "Interaction was already answered differently"
    RETRY_DENIED = "Uncertain execution retry was denied"
    EXPLICIT_CONFIRMATION_REQUIRED = "Confirmation requires an explicit option and any required input"
    CONFLICTING_DECISION = "Explicit decision conflicts with stored interaction response"
    EXPIRED_REQUEST = "Interaction has expired"
    BACKUP_OWNERSHIP = "Interaction backup ownership mismatch"
    MISSING_RENEWAL = "Interaction renewal record is missing"
    CHECKPOINT_IDENTITY = "Interaction checkpoint identity mismatch"
    NO_READ_SCOPE = None
    NO_CACHED_CONTROL = None
    NO_RESPONSE = None
    NO_REQUEST = None
    NO_RESUME = None
    RESUME_NOT_ABANDONED = False
    ACTIONABLE_STATES = (RunStatus.PAUSED, RunStatus.INTERRUPTED, RunStatus.FAILED)

    def __init__(self):
        self._envelopes = self.NO_READ_SCOPE

    def snapshot(self, run):
        """Storage evidence projection; callers do not interpret this repository's directory."""
        return tree_snapshot(InteractionStorage.root(run))

    def _root(self, run):
        path = InteractionStorage.root(run)
        for item in (path, *path.parents):
            if item.is_symlink():
                raise ValueError(self.LINKED_PATH)
        return path

    def _path(self, run, request):
        path = child(self._root(run), request.id).with_suffix(InteractionStorage.EXTENSION)
        if path.is_symlink():
            raise ValueError(self.LINKED_PATH)
        return path

    def _save(self, run, request, data):
        path = self._path(run, request)
        value = InteractionRecord.from_dict(data)
        if value.run_id != run.id:
            raise ValueError(self.RUN_MISMATCH)
        value.validate_request(request)
        atomic_json(path, value.to_dict())
        if self._envelopes is not self.NO_READ_SCOPE:
            self._envelopes.pop(path, self.NO_CACHED_CONTROL)

    def initialize(self, run, request):
        """Only request publication creates pending state; reads never create it."""
        if self._path(run, request).exists():
            raise ValueError(self.ALREADY_INITIALIZED)
        self._save(run, request, InteractionRecord.initial(run, request).to_dict())

    def publish(self, run, request, *, existing):
        """Checkpoint writers must distinguish a new request from retained proof."""
        if existing or self._path(run, request).exists():
            self.control(run, request)
        else:
            self.initialize(run, request)

    def inherit(self, source, run, checkpoint):
        """Copy existing approval evidence when creating a distinct resume Run."""
        originals = {r.checkpoint_binding.required_key: r for r in self.requests(checkpoint, source)}
        for request in self.requests(checkpoint, run):
            original = originals[request.checkpoint_binding.required_key]
            if request.id != original.id:
                self.effective(source, [original])
                # An uncertain operation in a new attempt needs its own retry
                # approval. The prior attempt's response remains in the source.
                self.initialize(run, request)
                continue
            visited = set()
            while True:
                if request.id in visited:
                    raise ValueError(InteractionRecord.INVALID_RENEWAL)
                visited.add(request.id)
                value = self.control(source, request)
                if self._path(run, request).exists():
                    raise ValueError(self.ALREADY_INHERITED)
                self._save(run, request, value.for_run(run.id))
                if not value.has_replacement:
                    break
                request = value.parsed_replacement

    @contextmanager
    def read_scope(self):
        """한 workspace 잠금 작업 안에서만 읽기를 재사용한다. 다음 작업에는 남기지 않는다."""
        previous, self._envelopes = self._envelopes, {}
        try:
            yield self
        finally:
            if previous is not self.NO_READ_SCOPE:
                previous.clear()
            self._envelopes = previous

    def requests(self, checkpoint: CheckpointInteractions, run: Run) -> list[InteractionRequest]:
        values = []
        ids = set()
        for key, record in checkpoint.records.items():
            step = CheckpointStep(record)
            if step.status == CheckpointStatus.STARTED and step.requires_retry:
                # Each new attempt retains its own deterministic retry approval identity.
                request = RetryInteraction.request(checkpoint, run, key, record)
            elif step.status == CheckpointStatus.WAITING and step.has_interaction:
                request = step.interaction
            else:
                continue
            if request.checkpoint_binding.key != key or request.id in ids:
                raise ValueError(self.CHECKPOINT_IDENTITY)
            ids.add(request.id)
            values.append(request)
        return values

    def envelope(self, run, request):
        path = self._path(run, request)
        if self._envelopes is not self.NO_READ_SCOPE and path in self._envelopes:
            value = self._envelopes[path]
        else:
            value = InteractionRecord.from_dict(read_json(path))
            if value.run_id != run.id:
                raise ValueError(self.RUN_MISMATCH)
            if self._envelopes is not self.NO_READ_SCOPE:
                self._envelopes[path] = value
        value.validate_request(request)
        return value.to_dict()

    def control(self, run, request):
        return InteractionControl(self.envelope(run, request))

    def effective(self, run, requests):
        result = []
        for original in requests:
            request, visited = original, set()
            while True:
                # 순환은 무결성 오류다. 정상 체인의 길이를 제품 숫자로 제한하지 않는다.
                if request.id in visited:
                    raise ValueError(InteractionRecord.INVALID_RENEWAL)
                visited.add(request.id)
                data = self.control(run, request)
                if not data.has_replacement:
                    break
                replacement = data.parsed_replacement
                if (
                    replacement.checkpoint_binding != request.checkpoint_binding
                    or replacement.action != request.action
                ):
                    raise ValueError(self.CHANGED_RENEWAL)
                request = replacement
            result.append(request)
        return result

    def cancel(self, run, request):
        data = self.control(run, request)
        if data.has_replacement:
            raise ValueError(self.ALREADY_RENEWED)
        self._save(run, request, data.cancelled_record())

    def renew(self, run, request, *, expires_at=InteractionRequest.NO_EXPIRY):
        from llm.core.models import new_id, now

        data = self.control(run, request)
        if not request.expired and not data.cancelled:
            raise ValueError(self.NOT_RENEWABLE)
        if data.has_replacement:
            raise ValueError(self.ALREADY_RENEWED)
        replacement = request.renewal(new_id(), created_at=now(), expires_at=expires_at)
        if replacement.expired:
            raise ValueError(self.EXPIRED_RENEWAL)
        updated = data.renewed_record(replacement)
        self.initialize(run, replacement)
        self._save(run, request, updated)
        return replacement

    def responses(self, run: Run, requests: list[InteractionRequest]) -> list[InteractionResponse]:
        values = []
        for request in requests:
            item = self.control(run, request)
            if item.has_response:
                values.append(item.response_for(request))
        return values

    def respond(
        self, run: Run, requests: list[InteractionRequest], response: InteractionResponse
    ) -> InteractionResponse:
        if not isinstance(response, InteractionResponse):
            raise TypeError(self.INVALID_RESPONSE)
        request = next((r for r in requests if r.id == response.request_id), self.NO_REQUEST)
        if request is self.NO_REQUEST:
            raise ValueError(self.UNOWNED_REQUEST)
        control = self.control(run, request)
        if control.cancelled:
            raise ValueError(self.CANCELLED)
        if control.has_replacement:
            raise ValueError(self.ALREADY_RENEWED)
        response.decision(request)
        prior = self.responses(run, [request])
        if prior:
            # 같은 선택 재전송은 멱등, 다른 선택으로 승인 기록을 덮어쓰는 것은 금지한다.
            before, after = prior[0].comparable(), response.comparable()
            if before != after:
                raise ValueError(self.CONFLICTING_RESPONSE)
            return prior[0]
        self._save(run, request, self.control(run, request).responded_record(response))
        return deepcopy(response)

    def decisions(
        self,
        requests: list[InteractionRequest],
        responses: list[InteractionResponse],
        explicit: dict,
        *,
        retry_nodes=(),
        confirm=InteractionRequest.DISALLOW_EMPTY_CONFIRMATION,
    ):
        """공통 응답을 엔진의 기존 재개 값으로 변환하고 상충하는 응답을 거부한다."""
        stored = {v.request_id: v for v in responses}
        decisions = deepcopy(explicit)
        receipts = []
        retries = list(retry_nodes)
        for request in requests:
            key = request.checkpoint_binding.required_key
            response = stored.get(request.id)
            if request.checkpoint_binding.target == InteractionRepository.RETRY_NODES_TARGET:
                if response is self.NO_RESPONSE and key in retries:
                    response = request.respond(InteractionOption.DEFAULT_APPROVAL_ID)
                    receipts.append(response)
                if response is not self.NO_RESPONSE:
                    if response.decision(request) is not InteractionOption.APPROVED:
                        raise ValueError(self.RETRY_DENIED)
                    if key not in retries:
                        retries.append(key)
                continue
            if (
                response is self.NO_RESPONSE
                and key not in decisions
                and confirm
                and request.kind == InteractionRequest.KIND_CONFIRMATION
            ):
                option = request.find_approval()
                if option is InteractionRequest.NO_OPTION:
                    raise ValueError(self.EXPLICIT_CONFIRMATION_REQUIRED)
                response = request.respond(option.id)
                receipts.append(response)
            if response is not self.NO_RESPONSE:
                value = response.decision(request)
                if key in decisions:
                    try:
                        option, supplied = request.select_decision(
                            decisions[key], confirm_empty=InteractionRequest.ALLOW_EMPTY_CONFIRMATION
                        )
                        explicit_value = request.decision_for(option.id, value=supplied)
                    except (ValueError, TypeError) as error:
                        raise ValueError(self.CONFLICTING_DECISION) from error
                    if not same_interaction_value(explicit_value, value):
                        raise ValueError(self.CONFLICTING_DECISION)
                decisions[key] = value
            elif key in decisions:
                # 저수준 엔진 재개 API도 동일한 선택지/입력 검증과 응답 영수증을 사용한다.
                option, supplied = request.select_decision(
                    decisions[key], confirm_empty=InteractionRequest.ALLOW_EMPTY_CONFIRMATION
                )
                response = request.respond(option.id, value=supplied)
                decisions[key] = response.decision(request)
                receipts.append(response)
            elif request.expired:
                raise ValueError(self.EXPIRED_REQUEST)
        return decisions, receipts, tuple(retries)

    def views(
        self,
        run,
        requests,
        responses,
        *,
        resume_message=NO_RESUME,
        resumed_run=NO_RESUME,
        resume_abandoned=RESUME_NOT_ABANDONED,
    ):
        answers = {v.request_id: v for v in responses}
        views = []
        for request in requests:
            response = answers.get(request.id)
            control = self.control(run, request)
            reason = InteractionView.NO_BLOCK_REASON
            if resume_abandoned:
                status, reason = InteractionView.UNAVAILABLE, InteractionView.RESUME_ABANDONED
            elif resumed_run is not self.NO_RESUME or resume_message is not self.NO_RESUME:
                status, reason = InteractionView.SUBMITTED, InteractionView.ALREADY_RESUMED
            elif str(run.status) not in self.ACTIONABLE_STATES:
                status, reason = InteractionView.UNAVAILABLE, InteractionView.RUN_ACTIVE_OR_FINISHED
            elif control.cancelled:
                status, reason = InteractionView.CANCELLED, InteractionView.REQUEST_CANCELLED
            elif request.expired:
                status, reason = InteractionView.EXPIRED, InteractionView.REQUEST_EXPIRED
            elif response is not self.NO_RESPONSE:
                option = next(o for o in request.options if o.id == response.option_id)
                status = (
                    InteractionView.DENIED
                    if option.effect == InteractionOption.EFFECT_DENY
                    else InteractionView.ANSWERED
                )
            else:
                status = InteractionView.PENDING
            views.append(
                InteractionView(
                    request,
                    response,
                    status,
                    status == InteractionView.PENDING,
                    status == InteractionView.ANSWERED,
                    reason,
                    resumed_run.id if resumed_run else self.NO_RESUME,
                    (
                        str(resumed_run.status)
                        if resumed_run
                        else RunStatus.QUEUED.value if resume_message else self.NO_RESUME
                    ),
                )
            )
        ready = bool(views) and all(v.can_resume for v in views)
        return [replace(v, can_resume=ready) for v in views]

    def validate_backup(self, run):
        """Validate this owner's archived controls, including superseded ones."""
        root = self._root(run)
        records = {}
        for path in root.glob(InteractionStorage.RECORDS):
            if path.is_symlink():
                raise ValueError(self.LINKED_PATH)
            value = InteractionRecord.from_dict(
                read_json(child(root, path.stem).with_suffix(InteractionStorage.EXTENSION))
            )
            if value.run_id != run.id or value.request_id != path.stem:
                raise ValueError(self.BACKUP_OWNERSHIP)
            records[value.request_id] = value
        for value in records.values():
            if value.has_replacement:
                request = value.parsed_replacement
                if request.id not in records:
                    raise ValueError(self.MISSING_RENEWAL)
                records[request.id].validate_request(request)
