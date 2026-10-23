"""Skill 파일과 참조·lineage를 검증한다. 각 소유자의 정의가 참조의 원본이다."""

from llm.core.resource_references import ResourceTarget
from llm.core.agents import AgentDefinition
from llm.core.agent_views import AgentDependency
from .dependencies import SkillDependencies, SkillChild

from llm.core.editable_snapshot import EditableSnapshot
from llm.core.agent_views import AgentSnapshot

from llm.components.files import FileData
from .records import SkillRecord as Record, SkillFileEvidence
from .views import SkillFileView, SkillReferenceView
from llm.components.file_evidence import FileEvidence
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import async_method, check_revision


class SkillData(FileData):
    def _validate_resources(self, value):
        for ref in value.get(Record.RESOURCES, []):
            if Record.COMPONENT in ref:
                self._target(ref)
            elif Record.PATH in ref and ref[Record.PATH] not in value.get(Record.FILES, {}):
                raise ValueError("Skill resource path must name a registered file")

    def _target(self, ref):
        """참조의 public snapshot/file 계약만 사용한다. 대상 저장 경로는 해석하지 않는다."""
        target = self.related(ref[Record.COMPONENT])
        snapshot = EditableSnapshot.read(target.snapshot(ref[Record.IDENTIFIER]))
        if snapshot.version != ref[Record.REVISION]:
            raise ValueError("Skill reference revision changed; explicitly update the reference")
        if ref[Record.COMPONENT] == Record.DATA and ref[Record.PATH] not in target.list_files(ref[Record.IDENTIFIER]):
            raise ValueError("Skill Data reference must name a registered file")
        return target

    @workspace_locked
    def create(self, data, *, identifier=None):
        self.registry.get(self.name).validate_record(identifier, data)
        self._validate_resources(data)
        if Record.LINEAGE in data:
            lineage = data[Record.LINEAGE]
            check_revision(self.load(lineage[Record.PARENT]), lineage[Record.PARENT_REVISION])
        return super().create(data, identifier=identifier)

    @workspace_locked
    def save(self, identifier, data, *, expected_version=None):
        self.registry.get(self.name).validate_record(identifier, data)
        self._validate_resources(data)
        if self.load(identifier).get(Record.LINEAGE) != data.get(Record.LINEAGE):
            raise ValueError("Skill lineage is immutable; create a fork instead")
        return super().save(identifier, data, expected_version=expected_version)

    @workspace_locked
    def update(self, identifier, changes, *, expected_version=None):
        original = self.load(identifier)
        candidate = {**original, **changes}
        self.registry.get(self.name).validate_record(identifier, candidate)
        self._validate_resources(candidate)
        if Record.LINEAGE in changes and original.get(Record.LINEAGE) != changes[Record.LINEAGE]:
            raise ValueError("Skill lineage is immutable; create a fork instead")
        return super().update(identifier, changes, expected_version=expected_version)

    @workspace_locked
    def fork(self, parent, data, *, identifier, expected_revision):
        """명시한 부모 버전의 참고 파일을 복사하고 새 정의를 저장한다."""
        project, component = self._edit(parent, expected_revision)
        original = self.load(parent)
        value = {**data, Record.LINEAGE: {Record.PARENT: parent, Record.PARENT_REVISION: expected_revision}}
        if value.get(Record.FILES) != original.get(Record.FILES):
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
        if type(index) is not int or index < 0 or index >= len(value.get(Record.RESOURCES, [])):
            raise ValueError("Invalid Skill resource index")
        ref = value[Record.RESOURCES][index]
        if Record.COMPONENT in ref:
            target = self._target(ref)
            if ref[Record.COMPONENT] == Record.TOOLS:
                # 내용 조회/참조 검증은 Tool 준비·활성화·실행·승인을 하지 않는다.
                return SkillReferenceView(ref).to_dict()
            content = target.read_file(ref[Record.IDENTIFIER], ref[Record.PATH], expected_revision=ref[Record.REVISION])
            return SkillReferenceView(ref).with_content(content, FileEvidence.from_dict(
                target.list_files(ref[Record.IDENTIFIER])[ref[Record.PATH]]))
        if Record.PATH not in ref:
            return SkillReferenceView(ref).to_dict()
        content = self.read_file(identifier, ref[Record.PATH], expected_revision=expected_revision)
        return SkillFileView.describe(ref[Record.PATH], content,
                                      SkillFileEvidence.from_dict(value[Record.FILES][ref[Record.PATH]]))

    @workspace_locked
    def impact(self, identifier):
        """삭제 보호와 같은 저장 참조를 조회한다. 저장된 모든 Agent를 포함한다."""
        self.load(identifier)
        project, _ = self._current()
        refs = [reference for value in self.references.dependents(project, self.name, identifier)
                if (reference := ResourceTarget.from_dict(value)).component == AgentDefinition.REFERENCE_COMPONENT]
        if not refs:
            return []
        # workspace 잠금 안에서 Agent 소유자의 공개 읽기 계약만 사용한다.
        # 조회를 위해 Component를 선택하거나 runtime capability를 활성화하지 않는다.
        agents = self.agents()
        return [AgentDependency.from_snapshot(AgentSnapshot.read(
                    agents.snapshot(ref.identifier))).to_dict() for ref in refs]

    @workspace_locked
    def dependencies(self, identifier):
        """Agent 참조와 child lineage를 원본 정의에서 조회한다. 중복 인덱스 없음."""
        self.load(identifier)
        return SkillDependencies(
            agents=[AgentDependency.from_dict(value) for value in self.impact(identifier)],
            children=[SkillChild(key, EditableSnapshot.read(self.snapshot(key)).version)
                for key, value in self.list().items()
                if value.get(Record.LINEAGE, {}).get(Record.PARENT) == identifier]).to_dict()

    @workspace_locked
    def delete(self, identifier, *, expected_version=None):
        refs = SkillDependencies.from_dict(self.dependencies(identifier))
        if refs.has_dependents:
            raise ValueError("Skill is referenced by Agents or child Skills; remove references first")
        return super().delete(identifier, expected_version=expected_version)

    @workspace_locked
    def import_directory(self, source):
        """Register one standard directory. No Project-local relations are imported."""
        project, component = self._current()
        return component.import_directory(project, source)

    @workspace_locked
    def export_directory(self, identifier, destination, *, expected_revision):
        project, component = self._edit(identifier, expected_revision)
        return component.export_directory(project, identifier, destination)

    @workspace_locked
    def read_markdown(self, identifier, *, expected_revision=None):
        value = self.load(identifier)
        check_revision(value, expected_revision)
        return value[Record.MARKDOWN]

    @workspace_locked
    def write_markdown(self, identifier, markdown, *, expected_revision):
        self._edit(identifier, expected_revision)
        self.update(identifier, {Record.MARKDOWN: markdown}, expected_version=expected_revision)
        return self.snapshot(identifier)

    aimport_directory = async_method(import_directory)
    aexport_directory = async_method(export_directory)
    aread_markdown = async_method(read_markdown)
    awrite_markdown = async_method(write_markdown)

    acreate = async_method(create)
    afork = async_method(fork)
    aread_resource = async_method(read_resource)
    avalidate_references = async_method(validate_references)
    asave = async_method(save)
    aupdate = async_method(update)
    aimpact = async_method(impact)
    adependencies = async_method(dependencies)
    adelete = async_method(delete)
