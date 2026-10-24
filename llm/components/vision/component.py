"""Project-owned image storage; meaning lives in image/configuration Owners."""

from llm.components.tools.registry import ToolRegistry
from llm.components.base import Component, validate_name
from llm.core.models import new_id
from .backends import TesseractBackend, VisionModel
from .assets import VisionAssets
from .configuration import OCRBackendContract, VisionSettings, VisionConfigurationView, VisionBinding
from .records import VisionImageIdentity, PreparedImage, VisionImageRecord


class VisionComponent(Component):
    FORMAT_VERSION = VisionImageRecord.FORMAT_VERSION
    name = VisionImageIdentity.COMPONENT
    directory = VisionImageIdentity.COMPONENT
    capabilities = (VisionImageIdentity.COMPONENT, ToolRegistry.CAPABILITY)
    parameter_layout = VisionSettings.LAYOUT
    ABSENT = None
    REUSED_ID = "Image IDs cannot be reused, including deleted images"
    IMPORT_REQUIRED = "Use VisionData.import_image/aimport_image to register image bytes"
    RUNTIME_FACTORY_REQUIRED = "Vision runtime capabilities require a lifecycle-checked data_factory"

    def __init__(self, *, ocr_backends=ABSENT, adapter=ABSENT, revision=OCRBackendContract.DEFAULT_REVISION):
        from .data import VisionData

        self.data_class = VisionData
        OCRBackendContract.validate_revision(revision)
        self.revision = revision
        self.ocr_backends = dict(
            {TesseractBackend.NAME: TesseractBackend()} if ocr_backends is self.ABSENT else ocr_backends
        )
        for name, backend in self.ocr_backends.items():
            OCRBackendContract.validate(name, backend)
        self.model = VisionModel(adapter=adapter)

    def _blob(self, project, digest):
        return VisionAssets.path(self, project, digest)

    def _write_blob(self, project, raw, digest):
        return VisionAssets.write(self, project, raw, digest)

    def _record(self, project, identifier):
        return super().load(project, identifier)

    def describe_config(self):
        return VisionSettings.schema(self)

    def validate_config(self, values):
        super().validate_config(values)
        VisionSettings.validate(self, values)

    def resolve_config(self, project):
        view = super().resolve_config(project)
        VisionConfigurationView.enforce(view, self)
        return view

    def binding(self, project):
        return VisionBinding.create(self, project)

    def validate_record(self, identifier, data):
        VisionImageRecord.validate(identifier, data, self.FORMAT_VERSION)

    def import_bytes(self, project, raw, info, *, title, metadata, identifier=ABSENT, origin=ABSENT):
        prepared = PreparedImage(info)
        prepared.verify(raw)
        identifier = new_id() if identifier is self.ABSENT else validate_name(identifier)
        if self._record_path(project, identifier).exists():
            raise FileExistsError(self.REUSED_ID)
        record = VisionImageRecord.create(identifier, self.FORMAT_VERSION, info, title, metadata, origin)
        self.serialize(record)
        self.validate_record(identifier, record)
        self._write_blob(project, raw, prepared.sha256)
        super().create(project, record, identifier=identifier)
        return record

    def create(self, project, data, *, identifier=ABSENT):
        raise ValueError(self.IMPORT_REQUIRED)

    def load(self, project, identifier):
        data = self._record(project, identifier)
        if VisionImageRecord(data).deleted:
            raise FileNotFoundError(identifier)
        return data

    def list(self, project):
        records = super().list(project)
        return {key: value for key, value in records.items() if not VisionImageRecord(value).deleted}

    def save(self, project, identifier, data):
        old = self.load(project, identifier)
        VisionImageRecord(old).check_edit(data)
        super().save(project, identifier, data)

    def delete(self, project, identifier):
        record = self.load(project, identifier)
        VisionImageRecord(record).mark_deleted()
        # Keep original bytes and the tombstone for RAG/Step references and identity non-reuse.
        self._write(project, identifier, record)

    def read_image(self, project, identifier):
        record = self.load(project, identifier)
        image = VisionImageRecord(record)
        raw = self._blob(project, image.sha256).read_bytes()
        image.verify_asset(raw)
        return record, raw

    def validate_backup(self, project):
        self.model_usage(project)
        self.get_config(project)
        for record in super().list(project).values():
            image = VisionImageRecord(record)
            raw = self._blob(project, image.sha256).read_bytes()
            image.verify_asset(raw)

    def clone(self, source, destination):
        self.validate_backup(source)
        for identifier, record in super().list(source).items():
            image = VisionImageRecord(record)
            self._write_blob(destination, self._blob(source, image.sha256).read_bytes(), image.sha256)
            super().create(destination, record, identifier=identifier)

    def resolve_runtime(self, project, capability, *, data_factory):
        data = data_factory(self.name)
        if capability == VisionImageIdentity.COMPONENT:
            return data
        if capability == ToolRegistry.CAPABILITY:
            from .tools import vision_tools

            return vision_tools(data, self.binding(project), tuple(self.ocr_backends))
        return super().resolve_runtime(project, capability, data_factory=data_factory)

    def resolve(self, project, capability):
        raise ValueError(self.RUNTIME_FACTORY_REQUIRED)

    def read_parameters(self, project):
        return VisionSettings(self._options(project))
