"""재사용할 작업 지침과 참고 리소스 정의를 저장한다. 지침을 자동 실행하지 않는다."""

from llm.components.definitions import DefinitionComponent


class SkillComponent(DefinitionComponent):
    """instructions를 중심으로 한 열린 JSON Skill 문서를 관리한다."""

    name = "skills"
    directory = "skills"
    capabilities = ("skills", "tools")
    schema = {
        "type": "object", "required": ["instructions"],
        "properties": {
            "instructions": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
            "title": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "resources": {"type": "array", "items": {"type": "object"}},
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
