"""재사용할 작업 지침과 참고 리소스 정의를 저장한다. 지침을 자동 실행하지 않는다."""

from llm.components.files import FileComponent, FILE_MANIFEST
from .data import SkillData
from llm.core.schema import object_schema, metadata_schema


class SkillComponent(FileComponent):
    """지침·파일·버전이 고정된 참조를 저장한다. 실행 권한은 부여하지 않는다."""

    name = "skills"
    directory = "skills"
    capabilities = ("skills", "tools")
    data_class = SkillData
    record_name = "skill.json"
    schema = {
        "type": "object", "required": ["instructions"], "additionalProperties": False,
        "properties": {
            "instructions": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
            "title": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            "metadata": metadata_schema(),
            "files": FILE_MANIFEST,
            "resources": {"type": "array", "items": {"oneOf": [object_schema({
                "uri": {"type": "string", "minLength": 1}, "description": {"type": "string"},
                "metadata": metadata_schema()}, required=["uri"]), object_schema({
                "path": {"type": "string", "minLength": 1}, "description": {"type": "string"},
                "metadata": metadata_schema()}, required=["path"]), object_schema({
                "component": {"const": "data"},
                "identifier": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"},
                "revision": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                "path": {"type": "string", "minLength": 1},
                "description": {"type": "string"}, "metadata": metadata_schema(),
                }, required=["component", "identifier", "revision", "path"]), object_schema({
                "component": {"const": "tools"},
                "identifier": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"},
                "revision": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                "description": {"type": "string"}, "metadata": metadata_schema(),
                }, required=["component", "identifier", "revision"])]}},
            "lineage": {"type": "object", "required": ["parent", "parent_revision"],
                        "properties": {"parent": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"},
                                       "parent_revision": {"type": "string", "pattern": "^[a-f0-9]{64}$"}},
                        "additionalProperties": False},
        },
    }

    def delete_file(self, project, identifier, path):
        if any("component" not in ref and ref.get("path") == path
               for ref in self.load(project, identifier).get("resources", [])):
            raise ValueError("File is referenced by Skill resources; remove the reference first")
        return super().delete_file(project, identifier, path)

    def resolve_runtime(self, project, capability, *, data_factory):
        data = data_factory(self.name)
        for identifier in self.list(project):
            data.validate_references(identifier)
        if capability == "tools":
            from .tools import skill_tools
            registry = self.resolve(project, capability)
            registry.register(skill_tools(self.list(project), read_resource=data.aread_resource).get("skill_read_resource"))
            return registry
        return self.resolve(project, capability)

    def references(self, project):
        refs = []
        for identifier, value in self.list(project).items():
            refs.extend({**ref, "source": identifier} for ref in value.get("resources", []) if "component" in ref)
            if "lineage" in value:
                refs.append({"component": self.name, "identifier": value["lineage"]["parent"], "source": identifier})
        return refs

    def resolve(self, project, capability):
        """Agent 주입과 모델의 명시적 지침 조회는 같은 레코드를 사용한다."""
        if capability == "tools":
            from .tools import skill_tools
            # capability 해석의 잠금 경계에서 복사한다. 실행 도중 편집된 지침을
            # 섞지 않으며, 재개 시 정의 지문 변경은 기존 Tool binding 검사로 거부한다.
            self.get_config(project)
            return skill_tools(self.list(project))
        return super().resolve(project, capability)
