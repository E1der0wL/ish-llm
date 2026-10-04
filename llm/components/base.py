"""Component의 담당 디렉토리와 열린 JSON 레코드/설정 수명 주기를 정의한다. 실행 함수 등록과 데이터 CRUD는 서로 독립적이다.

Project-owned JSON definitions and directories, independent of execution."""

from copy import deepcopy
import json
import re
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Optional, Protocol

from llm.core.models import Project, ProjectConfig, new_id
from llm.services.infrastructure.storage import atomic_json, remove_named_tree, sync_directory


def validate_name(value: str) -> str:
    """컴포넌트 경로와 레코드 ID로 사용할 단일 식별자를 검증한다."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("Invalid component directory or record ID")
    return value


class ProjectComponent(Protocol):
    name: str
    directory: str

    def initialize(self, project: Project) -> None: ...
    def configuration(self, project: Project) -> dict: ...
    def create(self, project: Project, data: dict, *, identifier: Optional[str] = None) -> str: ...
    def load(self, project: Project, identifier: str) -> dict: ...
    def list(self, project: Project) -> dict[str, dict]: ...
    def save(self, project: Project, identifier: str, data: dict) -> None: ...
    def update(self, project: Project, identifier: str, changes: dict) -> dict: ...
    def delete(self, project: Project, identifier: str) -> None: ...
    def delete_directory(self, project: Project) -> None: ...
    capabilities: tuple[str, ...]
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
    capabilities: tuple[str, ...] = ()
    # Optional ComponentData subclass; None keeps the generic service handle.
    data_class: Optional[type] = None

    def backup_scope(self, project: Project):
        """외부 저장소가 있으면 checkpoint/flush와 동시 쓰기 차단 문맥을 재정의한다."""
        return nullcontext()

    def validate_backup(self, project: Project) -> None:
        """자신이 소유한 데이터만 검증한다. ProjectManager는 내부 레이아웃을 모른다."""
        self.configuration(project)
        self.list(project)

    def root(self, project: Project) -> Path:
        directory = validate_name(self.directory)
        if directory.lower() in {"sessions", "logs", "state", "cache"}:
            raise ValueError("Component directory conflicts with core storage")
        return self._checked(project.paths.root / directory)

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
        ProjectConfig.validate_settings(data)
        return json.dumps(data, ensure_ascii=False, allow_nan=False)

    @staticmethod
    def deserialize(value: str) -> dict:
        data = json.loads(value)
        ProjectConfig.validate_settings(data)
        return data

    def configuration_schema(self) -> dict:
        """선언한 필드만 UI에 열거한다. 열린 사용자 설정은 계속 허용한다."""
        from llm.core.schema import object_schema
        return object_schema()

    def effective_configuration(self, project) -> dict:
        """명시된 설정값과 출처를 UI에 제공한다. 실행 객체는 직렬화하지 않는다."""
        from llm.core.configuration import resolve_configuration
        return resolve_configuration(self.configuration_layers(project))

    def configuration_layers(self, project):
        """설정 원본은 ProjectConfig뿐이다. 컴포넌트 파일을 대체 설정으로 읽지 않는다."""
        self.configuration(project)
        return [("project", project.config.parameters.get("components", {}).get(self.name, {}))]

    def validate_project_configuration(self, project):
        """검증을 컴포넌트에 위임하되 Project 저장은 서비스가 담당한다."""
        self.configuration(project)

    def maintenance(self, project, *, apply=False, expected_version=None) -> dict:
        """컴포넌트 소유 정리 후보. 기본은 무삭제이며 내부 경로를 서비스에 노출하지 않는다."""
        return {"candidates": [], "protected": [], "version": None}

    def history_references(self, project) -> dict:
        """보관 정리에서 보호해야 할 외부 도메인 ID. 컴포넌트가 자신의 자료 형식을 해석한다."""
        return {"run_ids": [], "message_ids": []}

    def model_usage(self, project) -> list:
        """독립 모델 호출 영수증의 원본은 호출을 소유한 컴포넌트 아래에 둔다."""
        entries = []
        for path in sorted(self._checked(self.root(project) / "usage").glob("*.json")):
            value = self.deserialize(self._checked(path).read_text(encoding="utf-8"))
            self._validate_model_usage(value)
            if value["id"] != path.stem:
                raise ValueError("Component model usage identity mismatch")
            entries.append(value)
        return entries

    def _validate_model_usage(self, value):
        from datetime import datetime
        validate_name(value.get("id"))
        if value.get("component") != self.name or value.get("status") not in ("started", "completed", "failed"):
            raise ValueError("Invalid component model usage owner/status")
        if not isinstance(value.get("operation"), str) or not value["operation"]:
            raise ValueError("Invalid component model operation")
        if type(value.get("usage_complete")) is not bool or not isinstance(value.get("usage"), dict):
            raise ValueError("Invalid component model usage")
        for key in ("started_at", "ended_at"):
            if key in value and datetime.fromisoformat(value[key]).utcoffset() is None:
                raise ValueError("Model usage timestamps require a timezone")
        if "started_at" not in value:
            raise ValueError("Model usage requires started_at")
        reserved, tokens = value.get("reserved_tokens"), value["usage"].get("total_tokens")
        if reserved is not None and (type(reserved) is not int or reserved < 0):
            raise ValueError("Invalid model token reservation")
        if value["usage_complete"] and (type(tokens) is not int or tokens < 0):
            raise ValueError("Invalid complete model usage")

    def save_model_usage(self, project, entry):
        value = self.deserialize(self.serialize(entry))
        self._validate_model_usage(value)
        atomic_json(self._checked(self.root(project) / "usage" / (validate_name(value["id"]) + ".json")), value)

    def resolve_runtime(self, project: Project, capability: str, *, data_factory):
        """필요한 경우 현재 프로젝트의 수명 검사 핸들로 실행 기능을 구성한다.

        data_factory(name)는 호출 서비스가 소유하는 ComponentData를 반환한다.
        기본 구현은 기존 resolve 계약을 그대로 유지한다.
        """
        return self.resolve(project, capability)

    def validate_configuration(self, data: dict) -> None:
        from jsonschema import Draft202012Validator
        error = next(Draft202012Validator(self.configuration_schema()).iter_errors(data), None)
        if error:
            raise ValueError(f"Invalid {self.name} configuration: {error.message}")

    def validate_record(self, identifier: str, data: dict) -> None:
        pass

    def initialize(self, project: Project) -> None:
        from llm.services.infrastructure.storage import make_directory
        self.configuration(project)
        make_directory(self._checked(self.root(project) / "records"))

    def configuration(self, project: Project) -> dict:
        # 기존 파일은 무시해서 설정을 잃지 않도록 명시적으로 거부한다. 변환/삭제는 하지 않는다.
        if self._checked(self.root(project) / "component.json").exists():
            raise ValueError("Legacy component.json is unsupported; configure ProjectConfig.parameters.components")
        data = deepcopy(project.config.parameters.get("components", {}).get(self.name, {}))
        self.serialize(data)
        self.validate_configuration(data)
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
        atomic_json(self._record_path(project, identifier), value)

    def load(self, project: Project, identifier: str) -> dict:
        data = self.deserialize(self._record_path(project, identifier).read_text(encoding="utf-8"))
        self.validate_record(identifier, data)
        return data

    def list(self, project: Project) -> dict[str, dict]:
        root = self._checked(self.root(project) / "records")
        return {path.stem: self.load(project, path.stem) for path in sorted(root.glob("*.json"))}

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
        from llm.services.infrastructure.storage import unlink_file
        path = self._record_path(project, identifier)
        unlink_file(path)

    def delete_directory(self, project: Project) -> None:
        """Explicit permanent removal, including component-owned artifacts."""
        root = self.root(project)
        if root.exists():
            remove_named_tree(project.paths.root, root, self.directory)

    def resolve(self, project: Project, capability: str) -> Any:
        """Create only the requested, declared runtime capability."""
        raise ValueError("Unsupported component capability")

    def clone(self, source: Project, destination: Project) -> None:
        for identifier, data in self.list(source).items():
            self.create(destination, data, identifier=identifier)
