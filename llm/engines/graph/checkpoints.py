"""자식 Engine의 로컬 체크포인트를 부모 Workflow 실행 위치에 연결한다. 파일은 쓰지 않는다."""

import json
from copy import deepcopy
from llm.core.interactions import InteractionRequest
from dataclasses import replace

from llm.engines.base import EngineEvent, EngineEventType


class EngineCheckpointScope:
    """Step ID와 독립적인 호출 경로. 내부 payload 형식은 자식 Engine이 소유한다."""

    def __init__(self, owner, name, records):
        self.owner, self.name, self.records = owner, name, records

    def key(self, local):
        return json.dumps([*json.loads(self.owner), "engine", self.name, local], ensure_ascii=False, separators=(",", ":"))

    def snapshot(self):
        header = self.records.get(self.key("@header"))
        if header is None:
            return None
        return {"header": deepcopy(header["payload"]), "records": {
            v["local_key"]: deepcopy(v["payload"]) for v in self.records.values()
            if v.get("engine_scope") == self.owner and v.get("checkpoint_name") == self.name and v["local_key"] != "@header"}}

    def context(self, context):
        snapshot = self.snapshot()
        run = deepcopy(context.run)
        descriptor = run.metadata.get("resume", {})
        decisions = {}
        for key, record in self.records.items():
            if (record.get("engine_scope") != self.owner or record.get("checkpoint_name") != self.name
                    or key not in descriptor.get("decisions", {})):
                continue
            local = record["local_key"]
            parent = InteractionRequest.from_dict(record["interaction"])
            child = InteractionRequest.from_dict(record["payload"]["interaction"])
            expected = child.bind("graph", self.key(local), decision_key="approved")
            if (key != self.key(local) or child.binding.get("checkpoint") != self.name
                    or child.binding.get("key") != local or parent.fingerprint != expected.fingerprint):
                raise ValueError("Child interaction does not match its checkpoint scope")
            # 부모의 승인 값 모양을 가정하지 않고 같은 선택지 ID를 자식의 원래 값으로 복원한다.
            # 수명/갱신/응답 영수증은 서비스가 검증한다. 여기서 원본 요청을 다시 승인하지 않는다.
            option, supplied = parent.select_decision(descriptor["decisions"][key])
            decisions[local] = child.decision_for(option.id, value=supplied)
        retry = [v["local_key"] for key, v in self.records.items()
            if v.get("engine_scope") == self.owner and v.get("checkpoint_name") == self.name
            and key in descriptor.get("retry_nodes", [])]
        if "resume" in run.metadata:
            run.metadata["resume"] = {**descriptor, "decisions": decisions, "retry_nodes": retry}
        return replace(context, run=run, checkpoint=snapshot, checkpoint_scope=self.owner)

    def wrap(self, event):
        data = event.metadata
        if data.get("name") != self.name:
            raise ValueError("Child Engine checkpoint name mismatch")
        if data["operation"] == "initialize":
            if self.snapshot() is not None or data.get("records"):
                raise ValueError("Child checkpoint initialization must be empty and unique")
            local, payload = "@header", data["header"]
            status, container = "completed", True
        elif data["operation"] == "record":
            if self.snapshot() is None or data["key"] == "@header":
                raise ValueError("Initialize child checkpoint before records")
            local, payload = data["key"], data["value"]
            state = payload["status"]
            if state not in ("started", "completed", "waiting", "responded", "not_applied"):
                raise ValueError("Unsupported child checkpoint status")
            # 자식의 responded/not_applied는 부모에서 외부 효과 재시도를 요구하지 않는다.
            status = state if state in ("started", "completed", "waiting") else "completed"
            container = state != "started"
        else:
            raise ValueError("Unsupported child checkpoint operation")
        value = {"node_type": "engine_record", "engine_scope": self.owner, "checkpoint_name": self.name,
                 "local_key": local, "payload": deepcopy(payload), "status": status, "container": container,
                 "requires_retry": status == "started" and not container}
        if status == "waiting":
            value.update(approval_required=True, name=payload.get("name"), arguments=payload.get("arguments"), prompt=payload.get("prompt"))
        request = None
        if "interaction" in payload:
            request = InteractionRequest.from_dict(payload["interaction"]).bind("graph", self.key(local), decision_key="approved")
            value["interaction"] = request.to_dict()
        return EngineEvent(EngineEventType.CHECKPOINT, interaction=request, metadata={"name": "graph", "operation": "record",
                          "key": self.key(local), "value": value})

    def accepted(self, event):
        self.records[event.metadata["key"]] = deepcopy(event.metadata["value"])
