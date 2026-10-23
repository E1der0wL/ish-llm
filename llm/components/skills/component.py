"""Standard Skill capability adapter; native storage and record meaning have Owners."""

from llm.components.tools.registry import ToolRegistry

from llm.core.resource_references import ResourceTarget, StoredResourceReference

from copy import deepcopy
import io
from pathlib import Path

from llm.components.definitions import DefinitionComponent
from llm.components.files import FileComponent
from .records import SkillRecord as Record
from .storage import SkillStorage
from .data import SkillData


class SkillComponent(DefinitionComponent):
    name = directory = "skills"
    capabilities = (name, ToolRegistry.CAPABILITY)
    data_class = SkillData
    schema = Record.SCHEMA
    INVALID_FILE_CONTENT = "File content must be bytes or text"

    def storage(self, project):
        return SkillStorage(project, self)

    def _usage_root(self, project):
        return self.storage(project).usage_root()

    def initialize(self, project):
        self.get_config(project)
        self.storage(project).initialize()

    def validate_record(self, identifier, data):
        Record.validate(identifier, data)

    def create(self, project, data, *, identifier=None):
        return self.storage(project).create(data, identifier)

    def load(self, project, identifier):
        return self.storage(project).load(identifier)

    def list(self, project):
        if not self._has_storage(project):
            return {}
        return self.storage(project).inventory()[1]

    def save(self, project, identifier, data):
        self.storage(project).save(identifier, data)

    def update(self, project, identifier, changes):
        value = {**self.load(project, identifier), **deepcopy(changes)}
        self.save(project, identifier, value)
        return self.load(project, identifier)

    def delete(self, project, identifier):
        self.storage(project).delete(identifier)

    def read_file(self, project, identifier, path, *, offset=0, length=None):
        return self.storage(project).read_file(identifier, path, offset=offset, length=length)

    def write_file(self, project, identifier, path, content):
        if not isinstance(content, (str, bytes)):
            raise TypeError(self.INVALID_FILE_CONTENT)
        with io.BytesIO(content.encode(Record.TEXT_ENCODING) if isinstance(content, str) else content) as stream:
            self.storage(project).write_file(identifier, path, stream)

    def import_file(self, project, identifier, path, source):
        source = self._checked(Path(source).absolute())
        with FileComponent._stream(source) as stream:
            self.storage(project).write_file(identifier, path, stream)

    def delete_file(self, project, identifier, path):
        self.storage(project).delete_file(identifier, path)

    def import_directory(self, project, source):
        return self.storage(project).import_directory(source)

    def export_directory(self, project, identifier, destination):
        return self.storage(project).export_directory(identifier, destination)

    def copy_record(self, source, destination, identifier, *, target_id=None, definition=None):
        original = self.load(source, identifier)
        target_id = identifier if target_id is None else target_id
        value = deepcopy(original if definition is None else definition)
        if target_id != Record.document(value).name:
            value = Record.renamed(value, target_id)
        self.storage(destination).import_directory(self.storage(source).package(identifier), value=value)

    def clone(self, source, destination):
        for identifier in self.list(source):
            self.copy_record(source, destination, identifier)

    def resolve_runtime(self, project, capability, *, data_factory):
        data = data_factory(self.name)
        for identifier in self.list(project):
            data.validate_references(identifier)
        if capability == ToolRegistry.CAPABILITY:
            from .tools import skill_tools
            from .views import SkillResourceRequest
            registry = self.resolve(project, capability)
            registry.register(skill_tools(self.list(project), read_resource=data.aread_resource,
                read_file=data.aread_file).get(SkillResourceRequest.NAME))
            return registry
        return self.resolve(project, capability)

    def references(self, project):
        refs = []
        for identifier, value in self.list(project).items():
            record = Record.read(value, identifier=identifier)
            refs.extend(StoredResourceReference.from_value(ref.to_dict(), identifier).to_dict()
                        for ref in record.resources if ref.external)
            if record.lineage is not None:
                refs.append(StoredResourceReference.from_target(
                    ResourceTarget(self.name, record.lineage.parent), identifier).to_dict())
        return refs

    def resolve(self, project, capability):
        if capability == ToolRegistry.CAPABILITY:
            from .tools import skill_tools
            self.get_config(project)
            return skill_tools(self.list(project))
        return super().resolve(project, capability)
