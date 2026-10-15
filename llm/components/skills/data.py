"""Skill 파일과 참조·lineage를 검증한다. 각 소유자의 정의가 참조의 원본이다."""

from llm.components.files import FileData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import async_method, check_revision


class SkillData(FileData):
    def _validate_resources(self, value):
        for ref in value.get("resources", []):
            if "component" in ref:
                self._target(ref)
            elif "path" in ref and ref["path"] not in value.get("files", {}):
                raise ValueError("Skill resource path must name a registered file")

    def _target(self, ref):
        """참조의 public snapshot/file 계약만 사용한다. 대상 저장 경로는 해석하지 않는다."""
        target = self.related(ref["component"])
        snapshot = target.snapshot(ref["identifier"])
        if snapshot["version"] != ref["revision"]:
            raise ValueError("Skill reference revision changed; explicitly update the reference")
        if ref["component"] == "data" and ref["path"] not in target.list_files(ref["identifier"]):
            raise ValueError("Skill Data reference must name a registered file")
        return target

    @workspace_locked
    def create(self, data, *, identifier=None):
        self.registry.get(self.name).validate_record(identifier or "pending", data)
        self._validate_resources(data)
        if "lineage" in data:
            lineage = data["lineage"]
            check_revision(self.load(lineage["parent"]), lineage["parent_revision"])
        return super().create(data, identifier=identifier)

    @workspace_locked
    def save(self, identifier, data, *, expected_version=None):
        self.registry.get(self.name).validate_record(identifier, data)
        self._validate_resources(data)
        if self.load(identifier).get("lineage") != data.get("lineage"):
            raise ValueError("Skill lineage is immutable; create a fork instead")
        return super().save(identifier, data, expected_version=expected_version)

    @workspace_locked
    def update(self, identifier, changes, *, expected_version=None):
        original = self.load(identifier)
        candidate = {**original, **changes}
        self.registry.get(self.name).validate_record(identifier, candidate)
        self._validate_resources(candidate)
        if "lineage" in changes and original.get("lineage") != changes["lineage"]:
            raise ValueError("Skill lineage is immutable; create a fork instead")
        return super().update(identifier, changes, expected_version=expected_version)

    @workspace_locked
    def fork(self, parent, data, *, identifier, expected_revision):
        """명시한 부모 버전의 참고 파일을 복사하고 새 정의를 저장한다."""
        project, component = self._edit(parent, expected_revision)
        original = self.load(parent)
        value = {**data, "lineage": {"parent": parent, "parent_revision": expected_revision}}
        if value.get("files") != original.get("files"):
            raise ValueError("Fork must preserve parent files")
        self._validate_resources(value)
        component.copy_record(project, project, parent, target_id=identifier, definition=value)
        return identifier

    @workspace_locked
    def validate_references(self, identifier):
        self._validate_resources(self.load(identifier))

    @workspace_locked
    def read_resource(self, identifier, index, *, expected_revision):
        if not isinstance(expected_revision, str) or not expected_revision:
            raise ValueError("Resource reads require expected_revision")
        value = self.load(identifier)
        check_revision(value, expected_revision)
        if type(index) is not int or index < 0 or index >= len(value.get("resources", [])):
            raise ValueError("Invalid Skill resource index")
        ref = value["resources"][index]
        if "component" in ref:
            target = self._target(ref)
            if ref["component"] == "tools":
                # 내용 조회/참조 검증은 Tool 준비·활성화·실행·승인을 하지 않는다.
                return {"reference": ref}
            content = target.read_file(ref["identifier"], ref["path"], expected_revision=ref["revision"])
            return {"reference": ref, "text": content.decode("utf-8"),
                    **target.list_files(ref["identifier"])[ref["path"]]}
        if "path" not in ref:
            return {"reference": ref}
        content = self.read_file(identifier, ref["path"], expected_revision=expected_revision)
        return {"path": ref["path"], "text": content.decode("utf-8"), **value["files"][ref["path"]]}

    @workspace_locked
    def impact(self, identifier):
        """삭제 보호와 같은 저장 참조를 조회한다. 선택 해제된 Agent도 포함한다."""
        self.load(identifier)
        project, _ = self._current()
        refs = [ref for ref in self.registry.dependents(project, self.name, identifier)
                if ref["component"] == "agents"]
        if not refs:
            return []
        # workspace 잠금 안에서 소유 Component의 공개 읽기 계약만 사용한다.
        # 조회를 위해 Component를 선택하거나 runtime capability를 활성화하지 않는다.
        agents = self.registry.get("agents")
        return [{"agent_id": ref["identifier"],
                 "revision": agents.revision(agents.load(project, ref["identifier"]))}
                for ref in refs]

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
    afork = async_method(fork)
    aread_resource = async_method(read_resource)
    avalidate_references = async_method(validate_references)
    asave = async_method(save)
    aupdate = async_method(update)
    aimpact = async_method(impact)
    adependencies = async_method(dependencies)
    adelete = async_method(delete)
