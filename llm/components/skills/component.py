"""재사용할 작업 지침과 참고 리소스 정의를 저장한다. 지침을 자동 실행하지 않는다."""

from llm.components.definitions import DefinitionComponent
from .data import SkillData
from llm.core.schema import object_schema, metadata_schema


class SkillComponent(DefinitionComponent):
    """지침과 참고 URI를 저장한다. metadata는 실행 권한을 부여하지 않는다."""

    name = "skills"
    directory = "skills"
    capabilities = ("skills", "tools")
    data_class = SkillData
    schema = {
        "type": "object", "required": ["instructions"], "additionalProperties": False,
        "properties": {
            "instructions": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
            "title": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "metadata": metadata_schema(),
            "resources": {"type": "array", "items": object_schema({
                "uri": {"type": "string", "minLength": 1}, "description": {"type": "string"},
                "metadata": metadata_schema()}, required=["uri"])},
            "lineage": {"type": "object", "required": ["parent", "parent_revision"],
                        "properties": {"parent": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"},
                                       "parent_revision": {"type": "string", "pattern": "^[a-f0-9]{64}$"}},
                        "additionalProperties": False},
        },
    }

    def resolve(self, project, capability):
        """Agent 주입과 모델의 명시적 지침 조회는 같은 레코드를 사용한다."""
        if capability == "tools":
            from .tools import skill_tools
            # capability 해석의 잠금 경계에서 복사한다. 실행 도중 편집된 지침을
            # 섞지 않으며, 재개 시 정의 지문 변경은 기존 Tool binding 검사로 거부한다.
            self.configuration(project)
            return skill_tools(self.list(project))
        return super().resolve(project, capability)
