"""재사용할 작업 지침과 참고 리소스 정의를 저장한다. 지침을 자동 실행하지 않는다."""

from llm.components.definitions import DefinitionComponent


class SkillComponent(DefinitionComponent):
    """instructions를 중심으로 한 열린 JSON Skill 문서를 관리한다."""

    name = "skills"
    directory = "skills"
    capabilities = ("skills",)
    schema = {
        "type": "object", "required": ["instructions"],
        "properties": {
            "instructions": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
            "resources": {"type": "array", "items": {"type": "object"}},
        },
    }
