"""Skill 파일과 참조·lineage를 검증한다. 각 소유자의 정의가 참조의 원본이다."""

from llm.core.resource_references import ResourceTarget
from llm.core.agents import AgentDefinition
from llm.core.agent_views import AgentDependency
from .dependencies import SkillDependencies, SkillChild

from llm.core.editable_snapshot import EditableSnapshot
from llm.core.agent_views import AgentSnapshot

from llm.components.files import FileData
from .records import SkillRecord as Record
from .views import SkillFileView, SkillReferenceView
from llm.components.file_evidence import FileEvidence
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import async_method, check_revision


class SkillData(FileData):
    INVALID_LOCAL_RESOURCE = "Skill resource path must name a registered file"
    STALE_REFERENCE = "Skill reference revision changed; explicitly update the reference"
    INVALID_DATA_RESOURCE = "Skill Data reference must name a registered file"
    IMMUTABLE_LINEAGE = "Skill lineage is immutable; create a fork instead"
    FORK_FILES = "Fork must preserve parent files"
    REQUIRED_REVISION = "Resource reads require expected_revision"
    HAS_DEPENDENTS = "Skill is referenced by Agents or child Skills; remove references first"

    def _validate_resources(self, value):
        for ref in value.resources:
            if ref.external:
                self._target(ref)
            elif ref.path is not None and ref.path not in value.files:
                raise ValueError(self.INVALID_LOCAL_RESOURCE)

    def _target(self, ref):
        """참조의 public snapshot/file 계약만 사용한다. 대상 저장 경로는 해석하지 않는다."""
        target = self.related(ref.component)
        snapshot = EditableSnapshot.read(target.snapshot(ref.identifier))
        if snapshot.version != ref.revision:
            raise ValueError(self.STALE_REFERENCE)
        if ref.data_reference and ref.path not in target.list_files(ref.identifier):
            raise ValueError(self.INVALID_DATA_RESOURCE)
        return target

    @workspace_locked
    def create(self, data, *, identifier=None):
        self.registry.get(self.name).validate_record(identifier, data)
        value = Record.read(data, identifier=identifier)
        self._validate_resources(value)
        if value.lineage is not None:
            check_revision(self.load(value.lineage.parent), value.lineage.parent_revision)
        return super().create(data, identifier=identifier)

    @workspace_locked
    def save(self, identifier, data, *, expected_version=None):
        self.registry.get(self.name).validate_record(identifier, data)
        value = Record.read(data, identifier=identifier)
        self._validate_resources(value)
        if Record.read(self.load(identifier)).lineage != value.lineage:
            raise ValueError(self.IMMUTABLE_LINEAGE)
        return super().save(identifier, data, expected_version=expected_version)

    @workspace_locked
    def update(self, identifier, changes, *, expected_version=None):
        original = self.load(identifier)
        candidate = {**original, **changes}
        self.registry.get(self.name).validate_record(identifier, candidate)
        value = Record.read(candidate, identifier=identifier)
        self._validate_resources(value)
        if Record.read(original).lineage != value.lineage:
            raise ValueError(self.IMMUTABLE_LINEAGE)
        return super().update(identifier, changes, expected_version=expected_version)

    @workspace_locked
    def fork(self, parent, data, *, identifier, expected_revision):
        """명시한 부모 버전의 참고 파일을 복사하고 새 정의를 저장한다."""
        project, component = self._edit(parent, expected_revision)
        original = Record.read(self.load(parent))
        candidate = Record.read(data)
        value = candidate.with_lineage(parent, expected_revision)
        if not candidate.same_files(original):
            raise ValueError(self.FORK_FILES)
        self._validate_resources(candidate)
        component.copy_record(project, project, parent, target_id=identifier, definition=value)
        return identifier

    @workspace_locked
    def validate_references(self, identifier):
        self._validate_resources(Record.read(self.load(identifier)))

    @workspace_locked
    def read_resource(self, identifier, index, *, expected_revision):
        if not isinstance(expected_revision, str) or not expected_revision:
            raise ValueError(self.REQUIRED_REVISION)
        value = self.load(identifier)
        check_revision(value, expected_revision)
        record = Record.read(value)
        ref = record.resource(index)
        if ref.external:
            target = self._target(ref)
            if ref.tool_reference:
                # 내용 조회/참조 검증은 Tool 준비·활성화·실행·승인을 하지 않는다.
                return SkillReferenceView(ref.to_dict()).to_dict()
            content = target.read_file(ref.identifier, ref.path, expected_revision=ref.revision)
            return SkillReferenceView(ref.to_dict()).with_content(content, FileEvidence.from_dict(
                target.list_files(ref.identifier)[ref.path]))
        if ref.path is None:
            return SkillReferenceView(ref.to_dict()).to_dict()
        content = self.read_file(identifier, ref.path, expected_revision=expected_revision)
        return SkillFileView.describe(ref.path, content, record.files[ref.path])

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
                if (lineage := Record.read(value).lineage) is not None and lineage.parent == identifier]).to_dict()

    @workspace_locked
    def delete(self, identifier, *, expected_version=None):
        refs = SkillDependencies.from_dict(self.dependencies(identifier))
        if refs.has_dependents:
            raise ValueError(self.HAS_DEPENDENTS)
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
        return Record.read(value).markdown

    @workspace_locked
    def write_markdown(self, identifier, markdown, *, expected_revision):
        self._edit(identifier, expected_revision)
        self.update(identifier, Record.markdown_change(markdown), expected_version=expected_revision)
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
