"""Component의 담당 디렉토리와 열린 JSON 레코드/설정 수명 주기를 정의한다. 실행 함수 등록과 데이터 CRUD는 서로 독립적이다.

Project-owned JSON definitions and directories, independent of execution."""

from llm.core.history_references import HistoryReferenceRecord

from llm.core.configuration import ConfigurationSource

from copy import deepcopy
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Optional, Protocol

from llm.core.models import Project
from .naming import ComponentName
from .record_manifest import ComponentRecordManifest, ComponentRecordEncoding
from .storage import ComponentStorage


def validate_name(value: str) -> str:
    """컴포넌트 경로와 레코드 ID로 사용할 단일 식별자를 검증한다."""
    return ComponentName.validate(value)


class ProjectComponent(Protocol):
    name: str
    directory: str

    def initialize(self, project: Project) -> None: ...
    def get_config(self, project: Project) -> dict: ...
    def create(
        self, project: Project, data: dict, *, identifier: Optional[str] = ComponentStorage.AUTO_IDENTIFIER
    ) -> str: ...
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
    SCHEMA_VERSION = ComponentRecordManifest.SCHEMA_VERSION
    RECORDS_DIRECTORY = ComponentStorage.RECORDS_DIRECTORY
    RECORDS_MANIFEST = ComponentStorage.RECORDS_MANIFEST
    NO_CONFIG_ERROR = None
    NO_DATA_CLASS = None
    MISSING_DIRECTORY = None
    NO_REQUIRED_COMPONENTS = ()
    INVALID_CONFIGURATION = "Invalid {name} configuration: {message}"
    UNSUPPORTED_CAPABILITY = "Unsupported component capability"

    class Field:
        SCHEMA = "schema"
        DATA_CLASS = "data_class"
        DIRECTORY = "directory"
        REQUIRED_COMPONENTS = "required_components"
    capabilities: tuple[str, ...] = ()
    # 영속 도메인의 정확한 identity 의존성이다. runtime capability와 별개다.
    required_components: tuple[str, ...] = NO_REQUIRED_COMPONENTS
    # Optional ComponentData subclass; None keeps the generic service handle.
    data_class: Optional[type] = NO_DATA_CLASS

    @classmethod
    def declared_schema(cls, component, default):
        return getattr(component, cls.Field.SCHEMA, default)

    @classmethod
    def data_type(cls, component):
        return getattr(component, cls.Field.DATA_CLASS, cls.NO_DATA_CLASS)

    @classmethod
    def declared_directory(cls, component):
        return getattr(component, cls.Field.DIRECTORY, cls.MISSING_DIRECTORY)

    @classmethod
    def declared_dependencies(cls, component):
        return getattr(component, cls.Field.REQUIRED_COMPONENTS, cls.NO_REQUIRED_COMPONENTS)

    def backup_scope(self, project: Project):
        """외부 저장소가 있으면 checkpoint/flush와 동시 쓰기 차단 문맥을 재정의한다."""
        return nullcontext()

    def validate_backup(self, project: Project) -> None:
        """자신이 소유한 데이터만 검증한다. ProjectManager는 내부 레이아웃을 모른다."""
        self.get_config(project)
        self.list(project)
        self.model_usage(project)

    def root(self, project: Project) -> Path:
        return ComponentStorage.root(self, project)

    @staticmethod
    def _checked(path: Path) -> Path:
        return ComponentStorage._checked(path)

    def _record_path(self, project: Project, identifier: str) -> Path:
        return ComponentStorage._record_path(self, project, identifier)

    @staticmethod
    def serialize(data: dict) -> str:
        """Encode an open JSON object without dropping unknown keys."""
        return ComponentRecordEncoding.encode(data)

    @staticmethod
    def deserialize(value: str) -> dict:
        return ComponentRecordEncoding.decode(value)

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
        return [
            (ConfigurationSource.PROJECT, project.config.parameter_targets.component(self.name).to_dict())
        ]

    def validate_project_config(self, project):
        """검증을 컴포넌트에 위임하되 Project 저장은 서비스가 담당한다."""
        self.get_config(project)

    def history_references(self, project) -> dict:
        """보관 정리에서 보호해야 할 외부 도메인 ID. 컴포넌트가 자신의 자료 형식을 해석한다."""
        return HistoryReferenceRecord.project(run_ids=[], message_ids=[])

    def references(self, project) -> list[dict]:
        """저장된 Component 참조. 소비자가 자기 정의를 해석하며 별도 원장은 만들지 않는다."""
        return []

    def _has_storage(self, project):
        return ComponentStorage._has_storage(self, project)

    def _model_usage_records(self, project):
        return ComponentStorage._model_usage_records(self, project)

    def model_usage(self, project) -> list:
        return ComponentStorage.model_usage(self, project)

    def _usage_root(self, project):
        return ComponentStorage._usage_root(self, project)

    def save_model_usage(self, project, entry):
        return ComponentStorage.save_model_usage(self, project, entry)

    def _record_files(self, project):
        return ComponentStorage._record_files(self, project)

    def _record_inventory(self, project):
        return ComponentStorage._record_inventory(self, project)

    def _decode_record(self, identifier, raw):
        value = self.deserialize(raw.decode(ComponentRecordEncoding.TEXT_ENCODING))
        self.validate_record(identifier, value)
        return value

    def _initialize_storage(
        self,
        project,
        *,
        records=ComponentStorage.CREATE_RECORDS,
        record_catalog=ComponentStorage.CREATE_RECORD_CATALOG,
    ):
        return ComponentStorage._initialize_storage(
            self, project, records=records, record_catalog=record_catalog
        )

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

        error = next(Draft202012Validator(self.describe_config()).iter_errors(data), self.NO_CONFIG_ERROR)
        if error:
            raise ValueError(self.INVALID_CONFIGURATION.format(name=self.name, message=error.message))

    def validate_record(self, identifier: str, data: dict) -> None:
        pass

    def initialize(self, project: Project) -> None:
        self._initialize_storage(project)

    def get_config(self, project: Project) -> dict:
        # 기존 파일은 무시해서 설정을 잃지 않도록 명시적으로 거부한다. 변환/삭제는 하지 않는다.
        ComponentStorage.reject_retired_configuration(self, project)
        data = deepcopy(project.config.parameter_targets.component(self.name).to_dict())
        self.serialize(data)
        self.validate_config(data)
        return data

    def create(
        self, project: Project, data: dict, *, identifier: Optional[str] = ComponentStorage.AUTO_IDENTIFIER
    ) -> str:
        return ComponentStorage.create(self, project, data, identifier=identifier)

    def _write(self, project: Project, identifier: str, data: dict) -> None:
        return ComponentStorage._write(self, project, identifier, data)

    def load(self, project: Project, identifier: str) -> dict:
        return ComponentStorage.load(self, project, identifier)

    def list(self, project: Project) -> dict[str, dict]:
        return ComponentStorage.list(self, project)

    def save(self, project: Project, identifier: str, data: dict) -> None:
        """Replace an existing definition; create is explicit."""
        return ComponentStorage.save(self, project, identifier, data)

    def update(self, project: Project, identifier: str, changes: dict) -> dict:
        """Shallow key update; nested values are replaced as complete values."""
        changes = self.deserialize(self.serialize(changes))
        data = self.load(project, identifier)
        data.update(changes)
        self.save(project, identifier, data)
        return data

    def delete(self, project: Project, identifier: str) -> None:
        return ComponentStorage.delete(self, project, identifier)

    def _remove_record(self, project, identifier):
        return ComponentStorage._remove_record(self, project, identifier)

    def delete_directory(self, project: Project) -> None:
        return ComponentStorage.delete_directory(self, project)

    def resolve(self, project: Project, capability: str) -> Any:
        """Create only the requested, declared runtime capability."""
        raise ValueError(self.UNSUPPORTED_CAPABILITY)

    def clone(self, source: Project, destination: Project) -> None:
        for identifier, data in self.list(source).items():
            self.create(destination, data, identifier=identifier)


class ComponentHook:
    """Stable optional hook names derived from their actual Component declarations."""

    BACKUP_SCOPE = Component.backup_scope.__name__
    VALIDATE_BACKUP = Component.validate_backup.__name__
    MODEL_USAGE = Component.model_usage.__name__
    DESCRIBE_CONFIG = Component.describe_config.__name__
    REFERENCES = Component.references.__name__
    VALIDATE_PROJECT_CONFIG = Component.validate_project_config.__name__
    RESOLVE_RUNTIME = Component.resolve_runtime.__name__
