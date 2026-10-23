"""추가 지시의 메시지/체크포인트 연결. 호출자는 저장 잠금과 트랜잭션을 소유한다."""

from llm.core.steering_channel import SteeringChannel
from llm.core.instruction_records import InstructionMessageRecord, InstructionReceipt
from llm.core.steering_events import SteeringEventData, SteeringOperation, SteeringReason
from llm.core.checkpoint_records import CheckpointWrite, CheckpointRecords

from llm.core.checkpoints import CheckpointConversation, CheckpointEnvelope

from llm.core.tool_usage_records import ToolUsageCheckpoint

from llm.core.models import Message, MessageStatus, now, new_id
from llm.core.steering import (InstructionStatus, is_instruction, RunInstruction, SteeringTarget,
                               InstructionDataError, InstructionCheckpoint, SteeringRoute)
from llm.services.history.conversation import Conversation
from llm.services.query import Query


class InstructionQuery:
    ALL = False
    PENDING = True


class InstructionBoundaryError:
    DISTINCT_ROUTES = "Choose distinct reservation routes"
    INVALID_ROUTE = "Reservation route is not a consuming Agent in this Run snapshot"
    SCOPE_MISMATCH = "checkpoint scope mismatch"
    REPEATED_MESSAGE = "repeated checkpoint message"
    MISSING_MESSAGE = "checkpoint message is unavailable"
    MESSAGE_OWNER = "checkpoint message ownership mismatch"
    INVALID_BOUNDARY = "Invalid instruction boundary"
    CHECKPOINT_TARGET = "Instruction checkpoint target mismatch"
    SELECTED = "Instruction boundary already selected"
    NOT_APPLIED = "Apply the selected instruction boundary before selecting again"
    APPLICATION_TARGET = "Instruction application target mismatch"
    UNKNOWN_OPERATION = "Unknown instruction operation"


def instruction_routes(run) -> list[SteeringRoute]:
    """Read reservation routes through the Run channel Owner."""
    channel = SteeringChannel.read(run)
    return [] if channel is None else list(channel.routes.values())


def reservation_receipts(run, routes):
    """접수 전체를 검증한 뒤 반환한다. 실행 중 대상 ID와 예약 ID는 별개다."""
    available = {route.key: route for route in instruction_routes(run)}
    if not routes or len({r.key for r in routes}) != len(routes):
        raise ValueError(InstructionBoundaryError.DISTINCT_ROUTES)
    if any(available.get(route.key) != route for route in routes):
        raise ValueError(InstructionBoundaryError.INVALID_ROUTE)
    return [InstructionReceipt.reserve(new_id(), route) for route in routes]


def instructions(store: Conversation, run_id: str, *, pending: bool = InstructionQuery.ALL) -> list[Message]:
    # 접수/종료 경계에서는 대기 메시지만 복사한다. 긴 대화 본문을 매 반복 복사하지 않는다.
    query = Query(status=MessageStatus.QUEUED) if pending else None
    result = []
    for message in store.list(query=query):
        if is_instruction(message):
            value = InstructionMessageRecord.from_message(message)
            if value.run_id == run_id and (not pending or value.status == InstructionStatus.PENDING):
                result.append(message)
    return result


def instruction_targets(run) -> list[SteeringTarget]:
    """Query and recovery share the validated Run channel."""
    channel = SteeringChannel.read(run)
    return [] if channel is None else list(channel.targets.values())


def _save_instruction(store, message, value: InstructionMessageRecord):
    store.update_metadata(message.id, value.metadata())
    store.set_status(message.id, value.message_status)
    return RunInstruction.from_message(store.get(message.id))


def finish_instructions(store: Conversation, run_id: str, reason: str, *, target_id=None) -> list[RunInstruction]:
    """Close pending recipients without replaying them as ordinary requests."""
    changed = []
    for message in instructions(store, run_id, pending=InstructionQuery.PENDING):
        value = InstructionMessageRecord.from_message(message)
        targets = value.targets
        if any(target.pending_for(target_id) for target in targets):
            value = value.with_targets(target.finish(reason) if target.pending_for(target_id) else target
                                       for target in targets)
            changed.append(_save_instruction(store, message, value))
    return changed


def instruction_records(checkpoint):
    """직접/중첩 체크포인트의 공통 input 기록. 메시지 본문은 복제하지 않는다."""
    from llm.core.checkpoints import unwrap_record
    for key, record in CheckpointRecords.from_checkpoint(checkpoint).records.items():
        payload, scope = unwrap_record(record)
        if InstructionCheckpoint.is_record(payload):
            payload = InstructionCheckpoint.read(payload)
            if scope is not None and payload.target_scope != scope:
                raise InstructionDataError(InstructionBoundaryError.SCOPE_MISMATCH)
            yield key, payload


def checkpoint_messages(store: Conversation, checkpoint: dict) -> dict[str, Message]:
    """체크포인트에는 본문 대신 ID만 저장한다. 원본 소실은 추측하지 않고 거부한다."""
    result = {}
    seen = set()
    for key, record in instruction_records(checkpoint):
        ids = record.message_ids
        for identifier in ids:
            identity = (record.target_scope, identifier)
            if identity in seen:
                raise InstructionDataError(InstructionBoundaryError.REPEATED_MESSAGE)
            seen.add(identity)
            try:
                message = store.get(identifier)
            except KeyError as error:
                raise InstructionDataError(InstructionBoundaryError.MISSING_MESSAGE) from error
            value = InstructionMessageRecord.from_message(message)
            if (value.input_message_id != CheckpointConversation.from_checkpoint(checkpoint).input_message_id
                    or not any(target.scope == record.target_scope for target in value.targets)):
                raise InstructionDataError(InstructionBoundaryError.MESSAGE_OWNER)
            result[identifier] = message
    return result


def resume_instructions(store: Conversation, checkpoint: dict) -> dict:
    """새 Run 사본에서만 미소비 예약을 제외한다. 원본 checkpoint/메시지는 변경하지 않는다.

    선택 저장과 적용 저장 사이에서 중단될 수 있다. 선택 ID의 존재만으로 소비를 추정하지 않는다.
    일반 활성 실행 지시의 기존 재개 계약은 유지한다.
    """
    history = checkpoint_messages(store, checkpoint)
    records = CheckpointRecords.from_checkpoint(checkpoint).records
    for key, record in instruction_records(checkpoint):
        kept = []
        for identifier in record.message_ids:
            receipt = next(target for target in InstructionMessageRecord.from_message(history[identifier]).targets
                           if target.scope == record.target_scope)
            if receipt.restores_context:
                kept.append(identifier)
        records[key] = InstructionCheckpoint.replace_message_ids(records[key], kept)
    return CheckpointRecords.replace_records(checkpoint, records)


def apply_instructions(store: Conversation, messages: tuple[Message, ...], run_id: str,
                       boundary: str, step_id: str, *, target: SteeringTarget, details=None) -> list[RunInstruction]:
    """Persist model input use; resume never consumes an applied reservation twice."""
    changed = []
    for selected in messages:
        message = store.get(selected.id)
        value = InstructionMessageRecord.from_message(message)
        targets = value.targets
        receipt = next(item for item in targets if item.scope == target.scope)
        if receipt.reserved and receipt.applications:
            continue
        applied = receipt.apply(run_id, boundary, target.id, step_id, now(), details)
        value = value.with_targets(applied if item.id == receipt.id else item for item in targets)
        changed.append(_save_instruction(store, message, value))
    return changed


def close_targets(run, reason):
    channel = SteeringChannel.require(run)
    channel.close(reason)
    channel.store(run)


def steering_boundary(repository, run, store, event, *, tool_usage):
    """엔진이 제공한 경계/대상으로 처리한다. 저장 잠금·트랜잭션은 RunManager가 소유한다."""
    from llm.engines.base import EngineEvent, EngineEventType
    data = SteeringEventData(event.metadata)
    channel = SteeringChannel.require(run)
    identifier, operation = data.target_id, data.operation
    if operation == SteeringOperation.ROUTES:
        channel.declare_routes(data.routes)
        channel.store(run)
        repository.save(run)
        return (), []
    if operation == SteeringOperation.BIND:
        route, scope = data.route, data.scope
        channel.bind_route(route, scope)
        changed = []
        for message in instructions(store, run.id, pending=InstructionQuery.PENDING):
            value = InstructionMessageRecord.from_message(message)
            targets = value.targets
            if any(receipt.waiting_for_route(route) for receipt in targets):
                value = value.with_targets(receipt.bind(route, scope) if receipt.waiting_for_route(route) else receipt
                                           for receipt in targets)
                changed.append(_save_instruction(store, message, value))
        channel.store(run)
        repository.save(run)
        return (), changed
    if operation == SteeringOperation.OPEN:
        target = data.target
        channel.register(identifier, target)
        changed = []
        for message in instructions(store, run.id, pending=InstructionQuery.PENDING):
            value = InstructionMessageRecord.from_message(message)
            targets = value.targets
            if any(receipt.waiting_for_target(target) for receipt in targets):
                value = value.with_targets(receipt.activate(target) if receipt.waiting_for_target(target) else receipt
                                           for receipt in targets)
                changed.append(_save_instruction(store, message, value))
        channel.store(run)
        repository.save(run)
        return (), changed
    target = channel.target(identifier)

    def close(reason):
        channel.close_target(identifier, reason)
        channel.store(run)
        repository.save(run)
        return finish_instructions(store, run.id, reason, target_id=identifier)

    if operation == SteeringOperation.CLOSE:
        return (), close(data.required_reason)
    channel.target(identifier, consuming=SteeringChannel.CONSUMING_ONLY)
    name, key = data.checkpoint, data.boundary
    if not isinstance(key, str) or not key:
        raise ValueError(InstructionBoundaryError.INVALID_BOUNDARY)
    checkpoint = repository.checkpoint(run, name)
    envelope = data.envelope
    if (envelope is None and target.scope is not None or envelope is not None
            and CheckpointEnvelope.from_dict(envelope).engine_scope != target.scope):
        raise ValueError(InstructionBoundaryError.CHECKPOINT_TARGET)
    if operation == SteeringOperation.SELECT:
        if key in CheckpointRecords.from_checkpoint(checkpoint).records:
            raise ValueError(InstructionBoundaryError.SELECTED)
        pending = [m for m in instructions(store, run.id, pending=InstructionQuery.PENDING)
                   if any(target.pending_for(identifier) for target in InstructionMessageRecord.from_message(m).targets)]
        messages = pending if data.allow_continue else []
        if data.final and not messages:
            return (), close(data.reason or SteeringReason.COMPLETED)
        selected = {mid for _, value in instruction_records(checkpoint) if value.target_scope == target.scope
                    for mid in value.message_ids}
        if selected.intersection(m.id for m in messages):
            raise ValueError(InstructionBoundaryError.NOT_APPLIED)
        record = InstructionCheckpoint.create(target.scope, (m.id for m in messages)).to_dict()
        if envelope is not None:
            record = CheckpointEnvelope.attach(envelope, record)
        record = ToolUsageCheckpoint.attach(record, tool_usage)
        repository.record_checkpoint(run, EngineEvent(EngineEventType.CHECKPOINT, metadata=CheckpointWrite.record_event(name, key, record).to_dict()))
        return tuple(messages), []
    if operation == SteeringOperation.APPLY:
        record = dict(instruction_records(checkpoint))[key]
        if record.target_scope != target.scope:
            raise ValueError(InstructionBoundaryError.APPLICATION_TARGET)
        history = checkpoint_messages(store, checkpoint)
        messages = tuple(history[i] for i in record.message_ids)
        return messages, apply_instructions(store, messages, run.id, key, event.step_id,
                                            target=target, details=data.details)
    raise ValueError(InstructionBoundaryError.UNKNOWN_OPERATION)
