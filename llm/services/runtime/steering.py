"""추가 지시의 메시지/체크포인트 연결. 호출자는 저장 잠금과 트랜잭션을 소유한다."""

from copy import deepcopy

from llm.core.models import Message, MessageRole, MessageStatus, now, new_id
from llm.core.steering import (is_instruction, RunInstruction, SteeringTarget, SteeringMode,
                               InstructionDataError, validate_instruction_record, SteeringRoute)
from llm.services.history.conversation import Conversation
from llm.services.query import Query


def initial_channel(run, mode, input_message_id):
    """단독 소비자는 기존 Run 시작 직후 접수를 유지한다. Graph는 자식 등록을 기다린다."""
    targets = {}
    if mode == SteeringMode.CONSUME:
        targets["root"] = SteeringTarget("root", run.id, run.engine, mode, accepting=True).to_dict()
    return {"accepting": True, "mode": mode, "input_message_id": input_message_id,
            "targets": targets, "root_opened": False, "routes": {}, "bindings": {}, "routes_declared": False}


def instruction_routes(run) -> list[SteeringRoute]:
    """실행 스냅샷에서 검증된 예약 경로. 종료된 Run에서는 조회만 가능하다."""
    channel = run.metadata.get("steering", {})
    if not isinstance(channel, dict):
        raise InstructionDataError("run reservation channel")
    routes = channel.get("routes", {})
    if not isinstance(routes, dict):
        raise InstructionDataError("run reservation routes")
    result = []
    for key, value in routes.items():
        try:
            route = SteeringRoute.from_dict(value)
        except (TypeError, ValueError) as error:
            raise InstructionDataError("run reservation route") from error
        if route.key != key:
            raise InstructionDataError("run reservation route key")
        result.append(route)
    bindings = channel.get("bindings", {})
    if not isinstance(bindings, dict) or any(not isinstance(k, str) or not k
            or not isinstance(v, str) or v not in routes for k, v in bindings.items()):
        raise InstructionDataError("run reservation bindings")
    return result


def reservation_receipts(run, routes):
    """접수 전체를 검증한 뒤 반환한다. 실행 중 대상 ID와 예약 ID는 별개다."""
    available = {route.key: route for route in instruction_routes(run)}
    if not routes or len({r.key for r in routes}) != len(routes):
        raise ValueError("Choose distinct reservation routes")
    if any(available.get(route.key) != route for route in routes):
        raise ValueError("Reservation route is not a consuming Agent in this Run snapshot")
    return [{"id": new_id(), "scope": None, "status": "pending", "applications": [],
             "reservation": route.to_dict(), "execution_id": None} for route in routes]


def instructions(store: Conversation, run_id: str, *, pending: bool = False) -> list[Message]:
    # 접수/종료 경계에서는 대기 메시지만 복사한다. 긴 대화 본문을 매 반복 복사하지 않는다.
    query = Query(status=MessageStatus.QUEUED) if pending else None
    result = []
    for message in store.list(query=query):
        if is_instruction(message):
            RunInstruction.validate_message(message)
            if (message.metadata["steering"]["run_id"] == run_id
                    and (not pending or message.metadata["steering"]["status"] == "pending")):
                result.append(message)
    return result


def instruction_targets(run) -> list[SteeringTarget]:
    """조회·복구가 같은 저장 descriptor 계약을 사용한다."""
    channel = run.metadata.get("steering")
    if "steering" not in run.metadata:
        return []
    # 원본 검증 전인 재개 시도는 아직 채널이 없다. 실패한 시도도 이 상태를 보존한다.
    if "resume" in run.metadata and channel == {"accepting": False}:
        return []
    if not isinstance(channel, dict) or not isinstance(channel.get("targets"), dict):
        raise InstructionDataError("run targets")
    instruction_routes(run)
    targets, scopes = [], set()
    for identifier, value in channel["targets"].items():
        target = SteeringTarget.from_dict(value)
        if target.id != identifier or target.run_id != run.id or target.scope in scopes:
            raise InstructionDataError("run target ownership/scope")
        scopes.add(target.scope)
        targets.append(target)
    return targets


def _save_instruction(store, message, value):
    states = {v["status"] for v in value["targets"]}
    value["status"] = ("pending" if "pending" in states else "applied" if states == {"applied"}
                       else "unapplied" if states == {"unapplied"} else "partially_applied")
    value["applications"] = [deepcopy(a) for target in value["targets"] for a in target.get("applications", [])]
    if value["status"] == "unapplied":
        value["reason"] = value["targets"][0]["reason"]
    else:
        value.pop("reason", None)
    store.update_metadata(message.id, {"steering": value})
    store.set_status(message.id, MessageStatus.QUEUED if "pending" in states else
                     MessageStatus.COMMITTED if "applied" in states else MessageStatus.CANCELLED)
    return RunInstruction.from_message(store.get(message.id))


def finish_instructions(store: Conversation, run_id: str, reason: str, *, target_id=None) -> list[RunInstruction]:
    """미반영 입력을 일반 요청으로 재생하지 않는다. 이미 사용한 지시는 그대로 둔다."""
    changed = []
    for message in instructions(store, run_id, pending=True):
        value = deepcopy(message.metadata["steering"])
        affected = [v for v in value["targets"] if v["status"] == "pending"
                    and (target_id is None or v.get("execution_id", v["id"]) == target_id)]
        if affected:
            for target in affected:
                target.update(status="unapplied", reason=("node_not_reached" if "reservation" in target
                    and target["scope"] is None and reason == "completed" else reason))
            changed.append(_save_instruction(store, message, value))
    return changed


def instruction_records(checkpoint):
    """직접/중첩 체크포인트의 공통 input 기록. 메시지 본문은 복제하지 않는다."""
    for key, record in checkpoint["records"].items():
        payload = record.get("payload", {}) if record.get("node_type") == "engine_record" else record
        if isinstance(payload, dict) and payload.get("kind") == "instruction":
            validate_instruction_record(payload)
            if record.get("node_type") == "engine_record" and (
                    not isinstance(record.get("engine_scope"), str) or not record["engine_scope"].strip()
                    or payload["target_scope"] != record["engine_scope"]):
                raise InstructionDataError("checkpoint scope mismatch")
            yield key, payload


def checkpoint_messages(store: Conversation, checkpoint: dict) -> dict[str, Message]:
    """체크포인트에는 본문 대신 ID만 저장한다. 원본 소실은 추측하지 않고 거부한다."""
    result = {}
    seen = set()
    for key, record in instruction_records(checkpoint):
        ids = record["message_ids"]
        for identifier in ids:
            identity = (record["target_scope"], identifier)
            if identity in seen:
                raise InstructionDataError("repeated checkpoint message")
            seen.add(identity)
            try:
                message = store.get(identifier)
            except KeyError as error:
                raise InstructionDataError("checkpoint message is unavailable") from error
            RunInstruction.validate_message(message)
            if (not is_instruction(message) or message.role != MessageRole.USER
                    or message.metadata["steering"]["input_message_id"] != checkpoint["header"]["input_message_id"]
                    or not any(t["scope"] == record["target_scope"] for t in message.metadata["steering"]["targets"])):
                raise InstructionDataError("checkpoint message ownership mismatch")
            result[identifier] = message
    return result


def resume_instructions(store: Conversation, checkpoint: dict) -> dict:
    """새 Run 사본에서만 미소비 예약을 제외한다. 원본 checkpoint/메시지는 변경하지 않는다.

    선택 저장과 적용 저장 사이에서 중단될 수 있다. 선택 ID의 존재만으로 소비를 추정하지 않는다.
    일반 활성 실행 지시의 기존 재개 계약은 유지한다.
    """
    history = checkpoint_messages(store, checkpoint)
    result = deepcopy(checkpoint)
    for _, record in instruction_records(result):
        kept = []
        for identifier in record["message_ids"]:
            receipt = next(t for t in history[identifier].metadata["steering"]["targets"]
                           if t["scope"] == record["target_scope"])
            if "reservation" not in receipt or receipt["status"] == "applied":
                kept.append(identifier)
        record["message_ids"] = kept
    return result


def apply_instructions(store: Conversation, messages: tuple[Message, ...], run_id: str,
                       boundary: str, step_id: str, *, target, details=None) -> list[RunInstruction]:
    """모델 호출 직전 입력 사용을 기록한다. 명시적 재개의 사용 이력도 보존한다."""
    changed = []
    for selected in messages:
        message = store.get(selected.id)
        value = deepcopy(message.metadata["steering"])
        receipt = next(t for t in value["targets"] if t["scope"] == target["scope"])
        # 재개는 기존 입력 문맥 복원이다. 예약을 다시 소비하거나 원본 영수증을 갱신하지 않는다.
        if "reservation" in receipt and receipt["applications"]:
            continue
        applications = receipt.setdefault("applications", [])
        if not any(v["run_id"] == run_id and v["boundary"] == boundary for v in applications):
            applications.append({**(details or {}), "run_id": run_id, "boundary": boundary,
                                 "target_id": target["id"], "step_id": step_id, "time": now()})
        receipt.update(status="applied")
        receipt.pop("reason", None)
        changed.append(_save_instruction(store, message, value))
    return changed


def close_targets(run, reason):
    for target in run.metadata["steering"].get("targets", {}).values():
        if target["reason"] is None:
            target.update(accepting=False, reason=reason)
    run.metadata["steering"]["accepting"] = False


def steering_boundary(repository, run, store, event):
    """엔진이 제공한 경계/대상으로 처리한다. 저장 잠금·트랜잭션은 RunManager가 소유한다."""
    from llm.engines.base import EngineEvent, EngineEventType
    data = event.metadata
    channel = run.metadata["steering"]
    targets = channel["targets"]
    identifier, operation = data["target_id"], data["operation"]
    if operation == "routes":
        if channel["mode"] != SteeringMode.FORWARD or channel["routes_declared"]:
            raise ValueError("Reservation routes must be declared once by the forwarding Run")
        routes = [SteeringRoute.from_dict(v) for v in data["routes"]]
        if len({r.key for r in routes}) != len(routes):
            raise ValueError("Ambiguous reservation routes")
        channel.update(routes={r.key: r.to_dict() for r in routes}, routes_declared=True)
        repository.save(run)
        return (), []
    if operation == "bind":
        route = SteeringRoute.from_dict(data["route"])
        scope = data["scope"]
        if (not channel["accepting"] or channel["routes"].get(route.key) != route.to_dict()
                or not isinstance(scope, str) or not scope or scope in channel["bindings"]):
            raise ValueError("Invalid or repeated reservation execution binding")
        channel["bindings"][scope] = route.key
        changed = []
        for message in instructions(store, run.id, pending=True):
            value = deepcopy(message.metadata["steering"])
            affected = [t for t in value["targets"] if t.get("reservation") == route.to_dict()
                        and t["scope"] is None and t["status"] == "pending"]
            if affected:
                for receipt in affected:
                    receipt["scope"] = scope
                changed.append(_save_instruction(store, message, value))
        repository.save(run)
        return (), changed
    if operation == "open":
        target = SteeringTarget.from_dict(data["target"])
        reserved_root = (identifier == "root" and targets.get(identifier) == target.to_dict()
                         and not channel["root_opened"])
        if (target.id != identifier or target.run_id != run.id or identifier in targets and not reserved_root
                or not channel["accepting"] or target.reason is not None
                or target.accepting != (target.mode == SteeringMode.CONSUME)
                or any(t["scope"] == target.scope and t["id"] != identifier for t in targets.values())):
            raise ValueError("Invalid instruction target registration")
        targets[identifier] = target.to_dict()
        if identifier == "root":
            channel["root_opened"] = True
        changed = []
        for message in instructions(store, run.id, pending=True):
            value = deepcopy(message.metadata["steering"])
            affected = [t for t in value["targets"] if "reservation" in t and t["scope"] == target.scope
                        and t["status"] == "pending" and t["execution_id"] is None]
            if affected:
                if target.mode != SteeringMode.CONSUME:
                    raise ValueError("Reserved execution must consume instructions")
                for receipt in affected:
                    if receipt["reservation"]["engine"] != target.engine:
                        raise ValueError("Reserved Agent engine changed")
                    receipt["execution_id"] = identifier
                changed.append(_save_instruction(store, message, value))
        repository.save(run)
        return (), changed
    target = targets[identifier]
    if target["reason"] is not None:
        raise ValueError("Instruction target is closed")

    def close(reason):
        target.update(accepting=False, reason=reason)
        if identifier == "root":
            channel["accepting"] = False
        repository.save(run)
        return finish_instructions(store, run.id, reason, target_id=identifier)

    if operation == "close":
        return (), close(data["reason"])
    if target["mode"] != SteeringMode.CONSUME or not target["accepting"]:
        raise ValueError("Instruction target does not consume input")
    name, key = data["checkpoint"], data["boundary"]
    if not isinstance(key, str) or not key:
        raise ValueError("Invalid instruction boundary")
    checkpoint = repository.checkpoint(run, name)
    envelope = data.get("envelope")
    if (envelope is None and target["scope"] is not None or envelope is not None
            and envelope["engine_scope"] != target["scope"]):
        raise ValueError("Instruction checkpoint target mismatch")
    if operation == "select":
        if key in checkpoint["records"]:
            raise ValueError("Instruction boundary already selected")
        pending = [m for m in instructions(store, run.id, pending=True)
                   if any(t.get("execution_id", t["id"]) == identifier and t["status"] == "pending"
                          for t in m.metadata["steering"]["targets"])]
        messages = pending if data["allow_continue"] else []
        if data["final"] and not messages:
            return (), close(data.get("reason") or "completed")
        selected = {mid for _, value in instruction_records(checkpoint) if value["target_scope"] == target["scope"]
                    for mid in value["message_ids"]}
        if selected.intersection(m.id for m in messages):
            raise ValueError("Apply the selected instruction boundary before selecting again")
        record = {"kind": "instruction", "status": "input", "target_scope": target["scope"], "message_ids": [m.id for m in messages]}
        if envelope is not None:
            record = {**deepcopy(envelope), "payload": record}
        repository.record_checkpoint(run, EngineEvent(EngineEventType.CHECKPOINT, metadata={
            "name": name, "operation": "record", "key": key, "value": record}))
        return tuple(messages), []
    if operation == "apply":
        record = dict(instruction_records(checkpoint))[key]
        if record["target_scope"] != target["scope"]:
            raise ValueError("Instruction application target mismatch")
        history = checkpoint_messages(store, checkpoint)
        messages = tuple(history[i] for i in record["message_ids"])
        return messages, apply_instructions(store, messages, run.id, key, event.step_id,
                                            target=target, details=data.get("details"))
    raise ValueError("Unknown instruction operation")
