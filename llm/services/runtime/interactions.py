"""Run 체크포인트의 사용자 요청과 별도 응답 영수증. 호출자는 소유권 잠금을 보유한다."""

from copy import deepcopy
import json
import hashlib
from dataclasses import replace

from llm.core.interactions import (
    InteractionRequest, InteractionResponse, InteractionView, approval_request, same_interaction_value,
)
from llm.core.models import Run
from llm.services.infrastructure.storage import atomic_json, read_json


class InteractionRepository:
    """요청은 체크포인트가 원본이다. 응답 저장만으로 작업을 실행하거나 큐에 넣지 않는다."""

    def _root(self, run):
        path = run.paths.state / "interactions"
        for item in (path, *path.parents):
            if item.is_symlink():
                raise ValueError("Interaction paths cannot follow links")
        return path

    def _path(self, run, request):
        path = self._root(run) / (request.id + ".json")
        if path.is_symlink():
            raise ValueError("Interaction paths cannot follow links")
        return path

    def requests(self, checkpoint: dict, run: Run) -> list[InteractionRequest]:
        values = []
        ids = set()
        for key, record in checkpoint["records"].items():
            if record.get("status") == "started" and record.get("requires_retry"):
                request = approval_request("결과가 불확실한 작업 재실행", category="execution.retry_uncertain", risk="high",
                    source={"run_id": run.id, "session_id": run.session_id}, action={"checkpoint_record": deepcopy(record)})
                identity = hashlib.sha256(json.dumps([run.id, checkpoint.get("name"), key]).encode()).hexdigest()[:32]
                request = replace(request, id=identity, created_at=run.created_at,
                    binding={"checkpoint": checkpoint.get("name", ""), "key": key, "target": "retry_nodes"})
            elif record.get("status") == "waiting" and "interaction" in record:
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
        value = read_json(path) if path.exists() else {"run_id": run.id}
        if value.get("run_id") != run.id:
            raise ValueError("Interaction response Run mismatch")
        return value

    def effective(self, run, requests):
        result = []
        for original in requests:
            request, visited = original, set()
            while True:
                # 손상된 영속 renewal 참조를 따라 무한 파일 읽기/메모리 증가를
                # 만들지 않는 traversal safety ceiling이다. 자동 승인 기준이 아니다.
                if request.id in visited or len(visited) >= 128:
                    raise ValueError("Invalid interaction renewal chain")
                visited.add(request.id)
                data = self.envelope(run, request)
                if "replacement" not in data:
                    break
                replacement = InteractionRequest.from_dict(data["replacement"])
                if replacement.binding != request.binding or replacement.action != request.action:
                    raise ValueError("Renewal cannot change the execution target")
                request = replacement
            result.append(request)
        return result

    def cancel(self, run, request):
        data = self.envelope(run, request)
        data["cancelled"] = True
        atomic_json(self._path(run, request), data)

    def renew(self, run, request, *, expires_at=None):
        from llm.core.models import new_id, now
        data = self.envelope(run, request)
        if not request.expired and not data.get("cancelled"):
            raise ValueError("Only expired or cancelled requests may be renewed")
        if "replacement" in data:
            raise ValueError("Request has already been renewed")
        replacement = replace(request, id=new_id(), revision=request.revision + 1,
                              created_at=now(), expires_at=expires_at, status="pending")
        if replacement.expired:
            raise ValueError("Renewal expiry must be in the future")
        data["replacement"] = replacement.to_dict()
        atomic_json(self._path(run, request), data)
        return replacement

    def responses(self, run: Run, requests: list[InteractionRequest]) -> list[InteractionResponse]:
        values = []
        for request in requests:
            path = self._path(run, request)
            if path.exists():
                item = read_json(path)
                if "response" not in item:
                    continue
                if item["run_id"] != run.id:
                    raise ValueError("Interaction response Run mismatch")
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
        if self.envelope(run, request).get("cancelled"):
            raise ValueError("Interaction is cancelled")
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
        atomic_json(self._path(run, request), {**self.envelope(run, request), "response": response.to_dict()})
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
            if response is None and key not in decisions and confirm and request.kind == "confirmation":
                response = request.respond("approve")
                receipts.append(response)
            if response is not None:
                value = response.decision(request)
                if key in decisions and not same_interaction_value(decisions[key], value):
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


    def views(self, run, requests, responses, *, resume_message=None, resumed_run=None):
        answers = {v.request_id: v for v in responses}
        views = []
        for request in requests:
            response = answers.get(request.id)
            control = self.envelope(run, request)
            reason = None
            if resumed_run is not None or resume_message is not None:
                status, reason = "submitted", "already_resumed"
            elif str(run.status) not in ("paused", "interrupted", "failed"):
                status, reason = "unavailable", "run_active_or_finished"
            elif control.get("cancelled"):
                status, reason = "cancelled", "request_cancelled"
            elif request.expired:
                status, reason = "expired", "request_expired"
            elif response is not None:
                option = next(o for o in request.options if o.id == response.option_id)
                status = "denied" if option.effect == "deny" else "answered"
            else:
                status = "pending"
            views.append(InteractionView(request, response, status, status == "pending",
                status == "answered", reason,
                resumed_run.id if resumed_run else None,
                str(resumed_run.status) if resumed_run else "queued" if resume_message else None))
        ready = bool(views) and all(v.can_resume for v in views)
        return [replace(v, can_resume=ready) for v in views]
