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
        decisions = {v["local_key"]: descriptor.get("decisions", {})[key]["approved"]
            for key, v in self.records.items() if v.get("engine_scope") == self.owner
            and v.get("checkpoint_name") == self.name and key in descriptor.get("decisions", {})}
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
