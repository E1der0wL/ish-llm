"""Component의 담당 디렉토리와 열린 JSON 레코드/설정 수명 주기를 정의한다. 실행 함수 등록과 데이터 CRUD는 서로 독립적이다.

Project-owned JSON definitions and directories, independent of execution."""

from copy import deepcopy
import hashlib
import json
import re
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Optional, Protocol

from llm.core.models import Project, ProjectConfig, new_id
from llm.core.paths import ProjectPaths
from llm.services.infrastructure.storage import atomic_json, remove_named_tree


def validate_name(value: str) -> str:
    """컴포넌트 경로와 레코드 ID로 사용할 단일 식별자를 검증한다."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("Invalid component directory or record ID")
    return value


class ProjectComponent(Protocol):
    name: str
    directory: str

    def initialize(self, project: Project) -> None: ...
    def get_config(self, project: Project) -> dict: ...
    def create(self, project: Project, data: dict, *, identifier: Optional[str] = None) -> str: ...
    def load(self, project: Project, identifier: str) -> dict: ...
    def list(self, project: Project) -> dict[str, dict]: ...
    def save(self, project: Project, identifier: str, data: dict) -> None: ...
    def update(self, project: Project, identifier: str, changes: dict) -> dict: ...
    def delete(self, project: Project, identifier: str) -> None: ...
    def delete_directory(self, project: Project) -> None: ...
    capabilities: tuple[str, ...]
    required_components: tuple[str, ...]
    def resolve(self, project: Project, capability: str) -> Any: ...
    def clone(self, source: Project, destination: Project) -> None: ...
    def backup_scope(self, project: Project): ...
    def validate_backup(self, project: Project) -> None: ...


# 담당 디렉토리/설정/JSON 레코드의 기본 수명 주기를 제공한다.
class Component:
    """Subclass with explicit name/directory; JSON keys belong to the component.

    ProjectConfig holds configuration; records/<id>.json holds named definitions.
    Direct use requires workspace ownership; ProjectManager.component provides a
    lifecycle-checked, locked CRUD handle. Override validation/resolve for domain
    semantics, and clone for artifacts outside this common JSON layout.
    """

    name: str
    directory: str
    SCHEMA_VERSION = 1
    RECORDS_MANIFEST = "records.manifest.json"
    capabilities: tuple[str, ...] = ()
    # 영속 도메인의 정확한 identity 의존성이다. runtime capability와 별개다.
    required_components: tuple[str, ...] = ()
    # Optional ComponentData subclass; None keeps the generic service handle.
    data_class: Optional[type] = None

    def backup_scope(self, project: Project):
        """외부 저장소가 있으면 checkpoint/flush와 동시 쓰기 차단 문맥을 재정의한다."""
        return nullcontext()

    def validate_backup(self, project: Project) -> None:
        """자신이 소유한 데이터만 검증한다. ProjectManager는 내부 레이아웃을 모른다."""
        self.get_config(project)
        self.list(project)
        self.model_usage(project)

    def root(self, project: Project) -> Path:
        directory = validate_name(self.directory)
        if directory.lower() in ProjectPaths.CORE_DIRECTORIES:
            raise ValueError("Component directory conflicts with core storage")
        root = self._checked(project.paths.root / directory)
        if self.name in project.initialized_components and not root.is_dir():
            raise ValueError("Initialized Component storage is missing")
        return root

    @staticmethod
    def _checked(path: Path) -> Path:
        for entry in (path, *path.parents):
            if entry.is_symlink():
                raise ValueError("Component storage cannot follow linked paths")
        return path

    def _record_path(self, project: Project, identifier: str) -> Path:
        return self._checked(self.root(project) / "records" / f"{validate_name(identifier)}.json")

    @staticmethod
    def serialize(data: dict) -> str:
        """Encode an open JSON object without dropping unknown keys."""
        ProjectConfig.validate_json(data)
        return json.dumps(data, ensure_ascii=False, allow_nan=False)

    @staticmethod
    def deserialize(value: str) -> dict:
        data = json.loads(value)
        ProjectConfig.validate_json(data)
        return data

    def describe_config(self) -> dict:
        """미선언 설정은 받지 않는다. 확장 구현체가 자신의 설정 계약을 선언한다."""
        from llm.core.schema import implementation_schema
        return implementation_schema()

    def resolve_config(self, project) -> dict:
        """명시된 설정값과 출처를 UI에 제공한다. 실행 객체는 직렬화하지 않는다."""
        from llm.core.configuration import resolve_config
        return resolve_config(self.get_config_layers(project), schema=self.describe_config())

    def get_config_layers(self, project):
        """설정 원본은 ProjectConfig뿐이다. 컴포넌트 파일을 대체 설정으로 읽지 않는다."""
        self.get_config(project)
        return [("project", project.config.parameters.get("components", {}).get(self.name, {}))]

    def validate_project_config(self, project):
        """검증을 컴포넌트에 위임하되 Project 저장은 서비스가 담당한다."""
        self.get_config(project)

    def history_references(self, project) -> dict:
        """보관 정리에서 보호해야 할 외부 도메인 ID. 컴포넌트가 자신의 자료 형식을 해석한다."""
        return {"run_ids": [], "message_ids": []}

    def references(self, project) -> list[dict]:
        """저장된 Component 참조. 소비자가 자기 정의를 해석하며 별도 원장은 만들지 않는다."""
        return []

    def _has_storage(self, project):
        """A disabled Component retains its storage declaration."""
        root = self.root(project)
        if self.name not in project.initialized_components:
            if root.exists():
                raise ValueError("Undeclared Component storage exists")
            return False
        if not root.is_dir():
            raise ValueError("Initialized Component storage is missing")
        return True

    def _model_usage_records(self, project):
        from llm.components.usage import ModelUsageRecord
        from llm.services.infrastructure.storage import read_json, revision_token
        if not self._has_storage(project):
            raise ValueError("Component model usage storage is not initialized")
        root = self._usage_root(project)
        manifest = read_json(self._checked(root / ModelUsageRecord.MANIFEST))
        ModelUsageRecord.validate_manifest(manifest, project_id=project.id, component=self.name)
        actual = {self._checked(path).name for path in root.iterdir()}
        if actual != {ModelUsageRecord.MANIFEST, *(identifier + ".json" for identifier in manifest["records"])}:
            raise ValueError("Component model usage files are missing or undeclared")
        records = {}
        for identifier in sorted(manifest["records"]):
            value = read_json(self._checked(root / (identifier + ".json")))
            record = ModelUsageRecord.from_dict(value)
            if (record.id != identifier or record.component != self.name or record.project_id != project.id
                    or revision_token(value) != manifest["records"][identifier]):
                raise ValueError("Component model usage owner or revision mismatch")
            records[identifier] = record
        return manifest, records

    def model_usage(self, project) -> list:
        """All retained receipts, including disabled Component storage."""
        if not self._has_storage(project):
            return []
        return [record.to_dict() for record in self._model_usage_records(project)[1].values()]

    def _usage_root(self, project):
        from llm.components.usage import ModelUsageRecord
        return self._checked(self.root(project) / ModelUsageRecord.DIRECTORY)

    def save_model_usage(self, project, entry):
        from llm.components.usage import ModelUsageRecord, ModelUsageStatus
        from llm.services.infrastructure.storage import revision_token
        record = ModelUsageRecord.from_dict(entry)
        if record.component != self.name or record.project_id != project.id:
            raise ValueError("Component model usage owner mismatch")
        manifest, records = self._model_usage_records(project)
        if record.id in records:
            record.validate_update(records[record.id])
        elif record.status != ModelUsageStatus.STARTED:
            raise ValueError("New Component model usage requires a reservation")
        root = self._usage_root(project)
        value = record.to_dict()
        manifest["records"][record.id] = revision_token(value)
        atomic_json(root / (record.id + ".json"), value)
        atomic_json(root / ModelUsageRecord.MANIFEST, manifest)

    def _record_files(self, project):
        root = self._checked(self.root(project) / "records")
        if not root.is_dir():
            raise ValueError("Component records directory is missing")
        paths = {}
        for path in root.iterdir():
            self._checked(path)
            if path.suffix != ".json" or not path.is_file():
                raise ValueError("Invalid Component record entry")
            paths[validate_name(path.stem)] = path
        return paths

    def _record_inventory(self, project):
        """Validate existence and bytes before an owner decodes or caches records."""
        from llm.services.infrastructure.storage import read_json
        if not self._has_storage(project):
            raise ValueError("Component record storage is not initialized")
        manifest = read_json(self._checked(self.root(project) / self.RECORDS_MANIFEST))
        if (not isinstance(manifest, dict)
                or manifest.keys() != {"schema_version", "project_id", "component", "records"}
                or type(manifest["schema_version"]) is not int
                or manifest["schema_version"] != self.SCHEMA_VERSION
                or manifest["project_id"] != project.id or manifest["component"] != self.name
                or not isinstance(manifest["records"], dict)):
            raise ValueError("Invalid Component record manifest")
        for identifier, digest in manifest["records"].items():
            validate_name(identifier)
            if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError("Invalid Component record digest")
        paths = self._record_files(project)
        if paths.keys() != manifest["records"].keys():
            raise ValueError("Component records are missing or undeclared")
        records = {}
        for identifier in sorted(paths):
            raw = paths[identifier].read_bytes()
            if hashlib.sha256(raw).hexdigest() != manifest["records"][identifier]:
                raise ValueError("Component record integrity mismatch")
            records[identifier] = raw
        return manifest, records

    def _decode_record(self, identifier, raw):
        value = self.deserialize(raw.decode("utf-8"))
        self.validate_record(identifier, value)
        return value

    def _initialize_storage(self, project, *, records=True, record_catalog=True):
        from llm.components.usage import ModelUsageRecord
        from llm.services.infrastructure.storage import make_directory
        self.get_config(project)
        root = self.root(project)
        if self.name in project.initialized_components:
            self._model_usage_records(project)
            if record_catalog:
                self._record_inventory(project)
            return
        if root.exists():
            raise ValueError("Cannot initialize undeclared Component storage")
        make_directory(root / "records" if records else root)
        atomic_json(self._usage_root(project) / ModelUsageRecord.MANIFEST, {
            "schema_version": ModelUsageRecord.SCHEMA_VERSION, "project_id": project.id,
            "component": self.name, "records": {}})
        if record_catalog:
            atomic_json(root / self.RECORDS_MANIFEST, {
                "schema_version": self.SCHEMA_VERSION, "project_id": project.id,
                "component": self.name, "records": {}})

    def resolve_runtime(self, project: Project, capability: str, *, data_factory):
        """필요한 경우 현재 프로젝트의 수명 검사 핸들로 실행 기능을 구성한다.

        data_factory(name)는 호출 서비스가 소유하는 ComponentData를 반환한다.
        기본 구현은 기존 resolve 계약을 그대로 유지한다.
        """
        return self.resolve(project, capability)

    def _options(self, project):
        """구현 내부 인자 사본. 공개 get_config()는 config/policy 형태를 보존한다."""
        return self.parameter_layout.unpack(self.get_config(project))

    def validate_config(self, data: dict) -> None:
        from llm.core.schema import validate_parameters
        validate_parameters(data, scope=self.name)
        from jsonschema import Draft202012Validator
        error = next(Draft202012Validator(self.describe_config()).iter_errors(data), None)
        if error:
            raise ValueError(f"Invalid {self.name} configuration: {error.message}")

    def validate_record(self, identifier: str, data: dict) -> None:
        pass

    def initialize(self, project: Project) -> None:
        self._initialize_storage(project)

    def get_config(self, project: Project) -> dict:
        # 기존 파일은 무시해서 설정을 잃지 않도록 명시적으로 거부한다. 변환/삭제는 하지 않는다.
        if self._checked(self.root(project) / "component.json").exists():
            raise ValueError("Legacy component.json is unsupported; configure ProjectConfig.parameters.components")
        data = deepcopy(project.config.parameters.get("components", {}).get(self.name, {}))
        self.serialize(data)
        self.validate_config(data)
        return data

    def create(self, project: Project, data: dict, *, identifier: Optional[str] = None) -> str:
        identifier = new_id() if identifier is None else identifier
        path = self._record_path(project, identifier)
        if path.exists():
            raise FileExistsError("Component record already exists")
        self._write(project, identifier, data)
        return identifier

    def _write(self, project: Project, identifier: str, data: dict) -> None:
        value = self.deserialize(self.serialize(data))
        self.validate_record(identifier, value)
        manifest, _ = self._record_inventory(project)
        path = self._record_path(project, identifier)
        atomic_json(path, value)
        manifest["records"][identifier] = hashlib.sha256(path.read_bytes()).hexdigest()
        atomic_json(self.root(project) / self.RECORDS_MANIFEST, manifest)

    def load(self, project: Project, identifier: str) -> dict:
        self._record_path(project, identifier)
        _, records = self._record_inventory(project)
        if identifier not in records:
            raise FileNotFoundError(identifier)
        return self._decode_record(identifier, records[identifier])

    def list(self, project: Project) -> dict[str, dict]:
        if not self._has_storage(project):
            return {}
        _, records = self._record_inventory(project)
        return {identifier: self._decode_record(identifier, raw) for identifier, raw in records.items()}

    def save(self, project: Project, identifier: str, data: dict) -> None:
        """Replace an existing definition; create is explicit."""
        if not self._record_path(project, identifier).is_file():
            raise FileNotFoundError("Component record does not exist")
        self._write(project, identifier, data)

    def update(self, project: Project, identifier: str, changes: dict) -> dict:
        """Shallow key update; nested values are replaced as complete values."""
        changes = self.deserialize(self.serialize(changes))
        data = self.load(project, identifier)
        data.update(changes)
        self.save(project, identifier, data)
        return data

    def delete(self, project: Project, identifier: str) -> None:
        self._record_path(project, identifier)
        manifest, records = self._record_inventory(project)
        if identifier not in records:
            raise FileNotFoundError(identifier)
        self._decode_record(identifier, records[identifier])
        self._remove_record(project, identifier)
        del manifest["records"][identifier]
        atomic_json(self.root(project) / self.RECORDS_MANIFEST, manifest)

    def _remove_record(self, project, identifier):
        from llm.services.infrastructure.storage import unlink_file
        unlink_file(self._record_path(project, identifier))

    def delete_directory(self, project: Project) -> None:
        """Explicit permanent removal, including component-owned artifacts."""
        root = self.root(project)
        if root.exists():
            self.validate_backup(project)
            remove_named_tree(project.paths.root, root, self.directory)

    def resolve(self, project: Project, capability: str) -> Any:
        """Create only the requested, declared runtime capability."""
        raise ValueError("Unsupported component capability")

    def clone(self, source: Project, destination: Project) -> None:
        for identifier, data in self.list(source).items():
            self.create(destination, data, identifier=identifier)
