"""자식 Engine의 로컬 체크포인트를 부모 Workflow 실행 위치에 연결한다. 파일은 쓰지 않는다."""

from llm.core.steering_events import SteeringEventData, SteeringOperation
from llm.core.checkpoint_records import CheckpointWrite

from llm.core.checkpoints import CheckpointOperation

from llm.core.continuations import ResumeRecord

import json
from copy import deepcopy
from llm.core.steering import InstructionCheckpoint
from llm.core.interactions import InteractionRequest
from llm.core.checkpoints import CheckpointEnvelope, CheckpointStatus, build_envelope
from dataclasses import replace

from llm.engines.base import EngineEvent, EngineEventType


GRAPH_CHECKPOINT_NAME = "graph"
GRAPH_CHECKPOINT_FORMAT = "workflow-nodes-v1"


class EngineCheckpointScope:
    """Step ID와 독립적인 호출 경로. 내부 payload 형식은 자식 Engine이 소유한다."""

    HEADER_KEY = "@header"

    def __init__(self, owner, name, records, *, parent_name=GRAPH_CHECKPOINT_NAME):
        if any(not isinstance(v, str) or not v.strip() for v in (owner, name, parent_name)):
            raise ValueError("Checkpoint scope requires nonempty owner and Engine checkpoint names")
        self.owner, self.name, self.records = owner, name, records
        self.parent_name = parent_name

    def _bind(self, request, local):
        return request.bind(self.parent_name, self.key(local), selection=True)

    def key(self, local):
        return json.dumps([self.owner, "engine", self.name, local], ensure_ascii=False, separators=(",", ":"))

    def snapshot(self):
        header = self.records.get(self.key(self.HEADER_KEY))
        if header is None:
            return None
        _, header_payload = CheckpointEnvelope.read(header)
        records = {}
        for value in self.records.values():
            if CheckpointEnvelope.belongs_to(value, self.owner, self.name):
                envelope, payload = CheckpointEnvelope.read(value)
                if envelope.local_key != self.HEADER_KEY:
                    records[envelope.local_key] = deepcopy(payload)
        return {"header": deepcopy(header_payload), "records": records}

    def context(self, context):
        snapshot = self.snapshot()
        run = deepcopy(context.run)
        descriptor = ResumeRecord.from_metadata(run.metadata)
        decisions = {}
        for key, record in self.records.items():
            if (not CheckpointEnvelope.belongs_to(record, self.owner, self.name)
                    or descriptor is None or key not in descriptor.decisions):
                continue
            envelope, payload = CheckpointEnvelope.read(record)
            local = envelope.local_key
            parent = InteractionRequest.from_dict(record["interaction"])
            child = InteractionRequest.from_dict(payload["interaction"])
            expected = self._bind(child, local)
            if (key != self.key(local) or child.checkpoint_binding.checkpoint != self.name
                    or child.checkpoint_binding.key != local or parent.fingerprint != expected.fingerprint):
                raise ValueError("Child interaction does not match its checkpoint scope")
            # 부모의 승인 값 모양을 가정하지 않고 같은 선택지 ID를 자식의 원래 값으로 복원한다.
            # 수명/갱신/응답 영수증은 서비스가 검증한다. 여기서 원본 요청을 다시 승인하지 않는다.
            option, supplied = parent.select_decision(descriptor.decisions[key])
            decisions[local] = child.decision_for(option.id, value=supplied)
        retry = [CheckpointEnvelope.from_dict(v).local_key for key, v in self.records.items()
            if CheckpointEnvelope.belongs_to(v, self.owner, self.name)
            and descriptor is not None and key in descriptor.retry_nodes]
        if descriptor is not None:
            descriptor.for_child(decisions=decisions, retry_nodes=retry).store(run.metadata)
        return replace(context, run=run, checkpoint=snapshot, checkpoint_scope=self.owner)

    def wrap(self, event):
        if event.checkpoint is not None:
            return replace(event, checkpoint=self.wrap(event.checkpoint))
        write = CheckpointWrite(event.metadata)
        if write.name_or_none != self.name:
            raise ValueError("Child Engine checkpoint name mismatch")
        if write.operation == CheckpointOperation.INITIALIZE:
            if self.snapshot() is not None or write.initial_records:
                raise ValueError("Child checkpoint initialization must be empty and unique")
            local, payload = self.HEADER_KEY, write.initial_header
            status, container = CheckpointStatus.COMPLETED, True
        elif write.operation == CheckpointOperation.RECORD:
            entry = write.record
            if self.snapshot() is None or entry.key == self.HEADER_KEY:
                raise ValueError("Initialize child checkpoint before records")
            local, payload = entry.key, entry.value
            from llm.core.checkpoints import checkpoint_lifecycle
            status, container = checkpoint_lifecycle(payload, write.lifecycle)
        else:
            raise ValueError("Unsupported child checkpoint operation")
        value = CheckpointEnvelope.attach(build_envelope(engine_scope=self.owner, checkpoint_name=self.name, local_key=local,
                    status=status, container=container, requires_retry=status == CheckpointStatus.STARTED and not container),
                 payload)
        if status == CheckpointStatus.WAITING:
            if "interaction" not in payload:
                raise ValueError("Nested waiting checkpoints require an InteractionRequest")
            value.update(approval_required=True, name=payload.get("name"), arguments=payload.get("arguments"), prompt=payload.get("prompt"))
        request = None
        if "interaction" in payload:
            request = self._bind(InteractionRequest.from_dict(payload["interaction"]), local)
            value["interaction"] = request.to_dict()
        return EngineEvent(EngineEventType.CHECKPOINT, interaction=request, metadata=CheckpointWrite.record_event(self.parent_name, self.key(local), value).to_dict())

    def accepted(self, event):
        event = event.checkpoint or event
        entry = CheckpointWrite(event.metadata).record
        self.records[entry.key] = entry.value

    def wrap_instruction(self, event):
        """입력 선택/반영도 같은 부모 저장 ACK를 사용한다. 본문은 여기에 저장하지 않는다."""
        data = SteeringEventData(event.metadata)
        if data.operation not in (SteeringOperation.SELECT, SteeringOperation.APPLY):
            return event
        if data.checkpoint != self.name:
            raise ValueError("Child instruction checkpoint name mismatch")
        local = data.boundary
        envelope = build_envelope(engine_scope=self.owner, checkpoint_name=self.name, local_key=local,
                                  status=CheckpointStatus.COMPLETED, container=True, requires_retry=False)
        return replace(event, metadata=data.scoped(self.parent_name, self.key(local), envelope).to_dict())

    def accepted_instruction(self, event, inbox):
        if SteeringEventData(event.metadata).operation == SteeringOperation.SELECT and not inbox.closed:
            data = SteeringEventData(event.metadata)
            self.records[data.boundary] = CheckpointEnvelope.attach(data.envelope,
                InstructionCheckpoint.create(self.owner, (m.id for m in inbox.messages)).to_dict())
