"""Skill lineage와 영향 조회. Agent 정의가 참조의 유일한 저장 원본이다."""

from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import async_method, check_revision


class SkillData(ComponentData):
    @workspace_locked
    def create(self, data, *, identifier=None):
        if "lineage" in data:
            lineage = data["lineage"]
            self.registry.get(self.name).validate_record(identifier or "pending", data)
            check_revision(self.load(lineage["parent"]), lineage["parent_revision"])
        return super().create(data, identifier=identifier)

    @workspace_locked
    def save(self, identifier, data, *, expected_version=None):
        if self.load(identifier).get("lineage") != data.get("lineage"):
            raise ValueError("Skill lineage is immutable; create a fork instead")
        return super().save(identifier, data, expected_version=expected_version)

    @workspace_locked
    def update(self, identifier, changes, *, expected_version=None):
        if "lineage" in changes and self.load(identifier).get("lineage") != changes["lineage"]:
            raise ValueError("Skill lineage is immutable; create a fork instead")
        return super().update(identifier, changes, expected_version=expected_version)

    @workspace_locked
    def impact(self, identifier):
        """현재 Agent 정의를 읽어서 참조 목록을 계산한다. 별도 인덱스는 저장하지 않는다."""
        self.load(identifier)
        project, _ = self._current()
        if "agents" not in project.components:
            return []
        return [{"agent_id": key, "revision": self.related("agents").snapshot(key)["revision"]}
                for key, value in self.related("agents").list().items()
                if identifier in value.get("resources", {}).get("skills", [])]

    @workspace_locked
    def dependencies(self, identifier):
        """Agent 참조와 child lineage를 원본 정의에서 조회한다. 중복 인덱스 없음."""
        self.load(identifier)
        return {"agents": self.impact(identifier), "children": [
            {"skill_id": key, "version": self.snapshot(key)["version"]}
            for key, value in self.list().items() if value.get("lineage", {}).get("parent") == identifier]}

    @workspace_locked
    def delete(self, identifier, *, expected_version=None):
        refs = self.dependencies(identifier)
        if refs["agents"] or refs["children"]:
            raise ValueError("Skill is referenced by Agents or child Skills; remove references first")
        return super().delete(identifier, expected_version=expected_version)

    acreate = async_method(create)
    asave = async_method(save)
    aupdate = async_method(update)
    aimpact = async_method(impact)
    adependencies = async_method(dependencies)
    adelete = async_method(delete)
