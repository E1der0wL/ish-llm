"""Run 소유 체크포인트 저장. Engine은 이벤트만 보내고 서비스가 원자적으로 기록한다.

헤더와 상속 스냅샷은 한 번 저장하고, 이후에는 변경된 노드만 교체한다.
파일 I/O는 호출자의 StorageIO/워크스페이스 소유권 안에서 수행한다.
"""

from copy import deepcopy
import hashlib
import json

from llm.components.base import validate_name
from llm.core.models import ProjectConfig
from llm.core.interactions import InteractionRequest
from llm.services.infrastructure.storage import atomic_json, read_json


def checkpoint_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def checkpoint_message_ids(checkpoint: dict) -> set[str]:
    """재개에 필요한 문맥·직접/중첩 추가 지시의 원본 ID. 삭제 검사와 재개가 공유한다."""
    from llm.services.runtime.steering import instruction_records
    header = checkpoint["header"]
    identifiers = header["message_ids"]
    if (not isinstance(identifiers, list) or any(not isinstance(v, str) or not v for v in identifiers)
            or not isinstance(header["input_message_id"], str) or not header["input_message_id"]):
        raise ValueError("Invalid checkpoint conversation references")
    result = {*identifiers, header["input_message_id"]}
    for _, record in instruction_records(checkpoint):
        result.update(record["message_ids"])
    return result


class CheckpointRepository:
    """체크포인트의 내용은 Engine 소유 JSON이고 저장 경로/Run 연결은 서비스 책임이다."""

    def _checked(self, path):
        for item in (path, *path.parents):
            if item.is_symlink():
                raise ValueError("Checkpoint paths cannot follow links")
        return path

    def _root(self, run, name):
        return self._checked(run.paths.state / "checkpoints" / validate_name(name))

    # 공개 API
    def deletion(self, run):
        """명시적으로 삭제된 자료를 손상/미초기화와 구분한다. 재개 원본으로 쓰지 않는다."""
        value = run.metadata.get("checkpoint_deletion")
        if value is None:
            return None
        from llm.core.models import RunStatus
        if (not isinstance(value, dict) or set(value) != {"deleted_at", "revision", "names", "interactions"}
                or not isinstance(value["deleted_at"], str) or not value["deleted_at"]
                or not isinstance(value["revision"], str) or not value["revision"]
                or not isinstance(value["names"], list) or not value["names"]
                or not isinstance(value["interactions"], list)
                or run.metadata.get("checkpoints")
                or run.status in (RunStatus.PENDING, RunStatus.RUNNING)):
            raise ValueError("Invalid checkpoint deletion record")
        for name in value["names"]:
            validate_name(name)
        if len(set(value["names"])) != len(value["names"]):
            raise ValueError("Duplicate deleted checkpoint names")
        if run.status not in (RunStatus.COMPLETED, RunStatus.CANCELLED):
            from llm.core.checkpoints import ResumeAbandonment
            ResumeAbandonment.from_dict(run.metadata["resume_abandonment"])
        requests = [InteractionRequest.from_dict(raw) for raw in value["interactions"]]
        if len({request.id for request in requests}) != len(requests):
            raise ValueError("Duplicate archived interaction identity")
        if any(request.binding.get("checkpoint") not in value["names"] for request in requests):
            raise ValueError("Archived interaction checkpoint mismatch")
        if self._checked(run.paths.state / "checkpoints").exists():
            raise ValueError("Deleted checkpoint files unexpectedly exist")
        return deepcopy(value)

    def delete(self, run, *, names, interactions, revision):
        """호출자의 트랜잭션에서 파일 삭제와 영구 삭제 표식을 함께 확정한다."""
        from llm.core.models import now
        from llm.services.infrastructure.storage import remove_named_tree
        root = self._checked(run.paths.state / "checkpoints")
        if root.exists():
            remove_named_tree(root.parent, root, "checkpoints")
        run.metadata.pop("checkpoints", None)
        # 승인 조회 증거만 남긴다. require_resume이 이 표식을 거부하므로 실행 권한은 없다.
        run.metadata["checkpoint_deletion"] = {"deleted_at": now(), "revision": revision,
            "names": list(names), "interactions": [request.to_dict() for request in interactions]}

    def record(self, run, event) -> None:
        if self.deletion(run) is not None:
            raise ValueError("Checkpoints were deleted; start a new Run")
        data = deepcopy(event.metadata)
        ProjectConfig.validate_json(data)
        root = self._root(run, data["name"])
        manifest = self._checked(root / "checkpoint.json")
        if data["operation"] == "initialize":
            if manifest.exists():
                raise ValueError("Checkpoint already initialized")
            atomic_json(manifest, {"schema_version": 1, "run_id": run.id, "engine": run.engine,
                                  "header": data["header"], "records": data.get("records", {})})
        elif data["operation"] == "record":
            if not manifest.is_file():
                raise ValueError("Initialize checkpoint before recording nodes")
            key = data["key"]
            if not isinstance(key, str) or not key:
                raise ValueError("Checkpoint record key must be nonempty text")
            raw = data["value"].get("interaction")
            if raw is not None:
                request = InteractionRequest.from_dict(raw)
                if (request.binding.get("checkpoint") != data["name"] or request.binding.get("key") != key
                        or data["value"].get("status") != "waiting"):
                    raise ValueError("Interaction checkpoint binding mismatch")
                if event.interaction is not None and event.interaction.to_dict() != raw:
                    raise ValueError("Interaction event differs from persisted request")
            elif event.interaction is not None:
                raise ValueError("Interaction event requires a persisted request")
            filename = hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json"
            atomic_json(self._checked(root / "records" / filename), {"key": key, "value": data["value"]})
        else:
            raise ValueError("Unknown checkpoint operation")

    def load(self, run, name: str) -> dict:
        if self.deletion(run) is not None:
            raise ValueError("Checkpoints were explicitly deleted")
        root = self._root(run, name)
        data = read_json(self._checked(root / "checkpoint.json"))
        if (data["schema_version"] != 1 or data["run_id"] != run.id or data["engine"] != run.engine):
            raise ValueError("Checkpoint version or Run ownership mismatch")
        records = dict(data["records"])
        for path in self._checked(root / "records").glob("*.json"):
            item = read_json(self._checked(path))
            expected = hashlib.sha256(item["key"].encode("utf-8")).hexdigest() + ".json"
            if path.name != expected:
                raise ValueError("Checkpoint record identity mismatch")
            records[item["key"]] = item["value"]
        return {**data, "records": records}
