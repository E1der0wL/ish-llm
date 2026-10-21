"""Run 소유 체크포인트 저장. Engine은 이벤트만 보내고 서비스가 원자적으로 기록한다.

헤더와 상속 스냅샷에 노드별 파일 리비전을 연결하고 Run에 manifest 리비전을 기록한다.
파일 I/O는 호출자의 StorageIO/워크스페이스 소유권 안에서 수행한다.
"""

from copy import deepcopy
import hashlib
import json
import re

from llm.components.base import validate_name
from llm.core.models import ProjectConfig
from llm.core.interactions import InteractionRequest
from llm.core.checkpoints import CheckpointStatus
from llm.services.infrastructure.storage import atomic_json, read_json, make_directory, revision_token


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

    SCHEMA_VERSION = 1

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
        run.metadata.pop("checkpoint_revisions", None)
        # 승인 조회 증거만 남긴다. require_resume이 이 표식을 거부하므로 실행 권한은 없다.
        run.metadata["checkpoint_deletion"] = {"deleted_at": now(), "revision": revision,
            "names": list(names), "interactions": [request.to_dict() for request in interactions]}

    def names(self, run):
        """Run declaration and owner revision distinguish absent from lost state."""
        names = run.metadata.get("checkpoints", [])
        revisions = run.metadata.get("checkpoint_revisions", {})
        if (not isinstance(names, list) or len(set(names)) != len(names)
                or not isinstance(revisions, dict) or set(names) != set(revisions)):
            raise ValueError("Invalid checkpoint declarations")
        for name in names:
            validate_name(name)
            if not isinstance(revisions[name], str) or not re.fullmatch(r"[a-f0-9]{64}", revisions[name]):
                raise ValueError("Invalid checkpoint revision")
        if ("checkpoints" in run.metadata) != ("checkpoint_revisions" in run.metadata):
            raise ValueError("Checkpoint declaration requires its revision witness")
        root = self._checked(run.paths.state / "checkpoints")
        actual = {self._checked(path).name for path in root.iterdir()} if root.exists() else set()
        if actual != set(names):
            raise ValueError("Checkpoint directories are missing or undeclared")
        return list(names)

    def _stored(self, run, name):
        names = self.names(run)
        root = self._root(run, name)
        data = read_json(self._checked(root / "checkpoint.json"))
        if name not in names:
            raise ValueError("Checkpoint is not declared by its Run")
        if (not isinstance(data, dict) or data.keys() != {
                "schema_version", "run_id", "engine", "header", "records", "record_revisions"}):
            raise ValueError("Checkpoint requires all current storage fields")
        if (type(data["schema_version"]) is not int or data["schema_version"] != self.SCHEMA_VERSION
                or data["run_id"] != run.id or data["engine"] != run.engine):
            raise ValueError("Checkpoint version or Run ownership mismatch")
        if revision_token(data) != run.metadata["checkpoint_revisions"][name]:
            raise ValueError("Checkpoint manifest revision mismatch")
        if (not isinstance(data["header"], dict) or not isinstance(data["records"], dict)
                or not isinstance(data["record_revisions"], dict)):
            raise ValueError("Checkpoint requires object header, records and revisions")
        ProjectConfig.validate_json(data)
        for key, value in data["records"].items():
            self._entry(key, value)
        directory = self._checked(root / "records")
        if {self._checked(path).name for path in root.iterdir()} != {"checkpoint.json", "records"}:
            raise ValueError("Invalid checkpoint storage entries")
        expected = set()
        for key, revision in data["record_revisions"].items():
            if not isinstance(key, str) or not key or not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{64}", revision):
                raise ValueError("Invalid checkpoint record revision")
            expected.add(self._filename(key))
        if {self._checked(path).name for path in directory.iterdir()} != expected:
            raise ValueError("Checkpoint records are missing or undeclared")
        records = deepcopy(data["records"])
        for key, revision in data["record_revisions"].items():
            value = read_json(self._checked(directory / self._filename(key)))
            if (not isinstance(value, dict) or value.keys() != {"key", "value"}
                    or value["key"] != key or revision_token(value) != revision):
                raise ValueError("Checkpoint record identity or revision mismatch")
            self._entry(key, value["value"])
            records[key] = value["value"]
        return data, records

    @staticmethod
    def _entry(key, value):
        if not isinstance(key, str) or not key or not isinstance(value, dict):
            raise ValueError("Checkpoint record requires a nonempty key and object value")
        ProjectConfig.validate_json(value)

    @staticmethod
    def _filename(key):
        return hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json"

    def _manifest(self, run, name, data):
        atomic_json(self._root(run, name) / "checkpoint.json", data)
        run.metadata.setdefault("checkpoint_revisions", {})[name] = revision_token(data)

    def record(self, run, event) -> None:
        if self.deletion(run) is not None:
            raise ValueError("Checkpoints were deleted; start a new Run")
        data = deepcopy(event.metadata)
        ProjectConfig.validate_json(data)
        name = data["name"]
        root = self._root(run, name)
        if data["operation"] == "initialize":
            names = self.names(run)
            if name in names or root.exists():
                raise ValueError("Checkpoint already initialized")
            records = data["records"] if "records" in data else {}
            if not isinstance(data["header"], dict) or not isinstance(records, dict):
                raise ValueError("Checkpoint initialization requires object header and records")
            for key, value in records.items():
                self._entry(key, value)
            make_directory(self._checked(root / "records"))
            self._manifest(run, name, {"schema_version": self.SCHEMA_VERSION, "run_id": run.id,
                "engine": run.engine, "header": data["header"], "records": records, "record_revisions": {}})
            run.metadata.setdefault("checkpoints", []).append(name)
        elif data["operation"] == "record":
            stored, _ = self._stored(run, name)
            key, value = data["key"], data["value"]
            self._entry(key, value)
            raw = value.get("interaction")
            if raw is not None:
                request = InteractionRequest.from_dict(raw)
                if (request.binding.get("checkpoint") != name or request.binding.get("key") != key
                        or value.get("status") != CheckpointStatus.WAITING):
                    raise ValueError("Interaction checkpoint binding mismatch")
                if event.interaction is not None and event.interaction.to_dict() != raw:
                    raise ValueError("Interaction event differs from persisted request")
            elif event.interaction is not None:
                raise ValueError("Interaction event requires a persisted request")
            item = {"key": key, "value": value}
            atomic_json(self._checked(root / "records" / self._filename(key)), item)
            stored["record_revisions"][key] = revision_token(item)
            self._manifest(run, name, stored)
        else:
            raise ValueError("Unknown checkpoint operation")

    def load(self, run, name: str) -> dict:
        if self.deletion(run) is not None:
            raise ValueError("Checkpoints were explicitly deleted")
        data, records = self._stored(run, name)
        return {key: records if key == "records" else value
                for key, value in data.items() if key != "record_revisions"}
