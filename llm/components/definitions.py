"""실행 없는 정의 컴포넌트의 공통 검증과 Run별 JSON 스냅샷을 제공한다."""

from jsonschema import Draft202012Validator

from llm.core.models import Project
from llm.core.definition_snapshot import DefinitionSnapshot
from llm.core.schema import object_schema
from .base import Component


class DefinitionComponent(Component):
    """선택 구현체의 record contract를 검증한다. 연결이나 모델 호출은 하지 않는다."""

    schema: dict = object_schema()
    PATH_SEPARATOR = "."
    RECORD_LOCATION = "record"
    INVALID_RECORD = "{component}/{identifier}: {location}: {message}"

    def validate_record(self, identifier: str, data: dict) -> None:
        errors = sorted(Draft202012Validator(self.schema).iter_errors(data),
                        key=lambda error: str(list(error.path)))
        if errors:
            error = errors[0]
            location = self.PATH_SEPARATOR.join(str(part) for part in error.path) or self.RECORD_LOCATION
            raise ValueError(self.INVALID_RECORD.format(component=self.name, identifier=identifier,
                                                        location=location, message=error.message))

    def resolve(self, project: Project, capability: str) -> dict:
        """요청된 정의를 분리된 dict로 전달한다. 실행 핸들은 영속 데이터가 아니다."""
        if capability not in self.capabilities:
            return super().resolve(project, capability)
        return DefinitionSnapshot(self.get_config(project), self.list(project)).to_dict()
