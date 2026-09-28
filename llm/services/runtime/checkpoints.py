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
    def record(self, run, event) -> None:
        data = deepcopy(event.metadata)
        ProjectConfig.validate_settings(data)
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

    def load(self, run, name: str = "graph") -> dict:
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
