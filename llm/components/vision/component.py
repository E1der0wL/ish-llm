"""Project 소유 이미지 자료와 명시적 Vision 설정. 이미지/변환 자료는 불변이며 메타데이터만 편집한다."""

from copy import deepcopy
import hashlib
import os
import re
from pathlib import Path

from llm.components.base import Component, validate_name
from llm.core.models import new_id, now, ProjectConfig
from llm.core.schema import object_schema, completion_schema, checked_schema
from llm.providers.requests import provider_schema, resolve_provider_options
from llm.services.infrastructure.storage import (
    atomic_json, make_directory, prepare_replace, temporary_file, sync_directory, revision_token,
)
from .backends import TesseractBackend, VisionModel


class VisionComponent(Component):
    name = "vision"
    directory = "vision"
    capabilities = ("vision", "tools")

    def __init__(self, *, ocr_backends=None, completion_fn=None, revision="1"):
        from .data import VisionData
        self.data_class = VisionData
        if not isinstance(revision, str) or not revision:
            raise ValueError("Vision backend revision is required")
        self.revision = revision
        # 제공 가능한 구현의 등록이다. backend 선택 기본값은 만들지 않는다.
        self.ocr_backends = dict({"tesseract": TesseractBackend()} if ocr_backends is None else ocr_backends)
        for name, backend in self.ocr_backends.items():
            validate_name(name)
            if name == "auto" or not callable(getattr(backend, "recognize", None)):
                raise ValueError("Register explicit OCR backends; auto is unsupported")
            if not isinstance(getattr(backend, "revision", None), str) or not backend.revision:
                raise ValueError("OCR backend revision is required")
            checked_schema(backend.configuration_schema())
        self.model = VisionModel(completion_fn=completion_fn)

    def _blob(self, project, digest):
        if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
            raise ValueError("Invalid image SHA-256")
        return self._checked(self.root(project) / "assets" / (digest + ".bin"))

    def _write_blob(self, project, raw, digest):
        path = self._blob(project, digest)
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError("Image asset integrity mismatch")
            return
        make_directory(path.parent)
        prepare_replace(path)
        fd, temporary = temporary_file(path)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            sync_directory(path.parent)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _record(self, project, identifier):
        return super().load(project, identifier)

    # 공개 구현 API. 서비스 핸들이 workspace 트랜잭션과 수명 검사를 제공한다.
    def configuration_schema(self):
        backend = ({"enum": list(self.ocr_backends)} if self.ocr_backends else {"not": {}})
        return object_schema({
            "limits": object_schema({key: {"type": "integer", "minimum": 1}
                for key in ("max_bytes", "max_pixels")}, additionalProperties=False),
            "ocr": object_schema({"backend": {"type": "string", **backend},
                "timeout": {"type": ["number", "null"], "exclusiveMinimum": 0},
                "backends": object_schema({key: value.configuration_schema()
                    for key, value in self.ocr_backends.items()}, additionalProperties=False)}, additionalProperties=False),
            "completion": completion_schema(), "provider": provider_schema(),
        })

    def validate_configuration(self, values):
        super().validate_configuration(values)
        ProjectConfig.validate_settings(values)
        resolve_provider_options(values.get("provider", {}))
        params = values.get("completion", {})
        if any(key in params for key in ("messages", "tools", "tool_choice", "functions", "function_call")):
            raise ValueError("Vision owns completion messages and does not invoke tools")
        if params.get("stream", False) is not False or params.get("n", 1) != 1:
            raise ValueError("Vision requires stream=False and n=1")

    def effective_configuration(self, project):
        view = super().effective_configuration(project)
        view["enforced"] = {"completion": {"stream": False, "n": 1}, "asset_format": 1}
        return view

    def binding(self, project):
        return revision_token({"configuration": self.configuration(project), "revision": self.revision,
                               "backends": {key: value.revision for key, value in self.ocr_backends.items()}})

    def validate_record(self, identifier, data):
        if (data.get("id") != identifier or data.get("format_version") != 1
                or data.get("mime_type") not in ("image/png", "image/jpeg")
                or any(type(data.get(key)) is not int or data[key] < 1 for key in ("width", "height", "bytes"))
                or not isinstance(data.get("sha256"), str) or not re.fullmatch("[0-9a-f]{64}", data["sha256"])
                or not isinstance(data.get("title"), str) or not isinstance(data.get("metadata"), dict)
                or type(data.get("deleted")) is not bool):
            raise ValueError("Invalid Vision image record")

    def import_bytes(self, project, raw, info, *, title, metadata, identifier=None, origin=None):
        if hashlib.sha256(raw).hexdigest() != info["sha256"] or len(raw) != info["bytes"]:
            raise ValueError("Prepared image integrity mismatch")
        identifier = new_id() if identifier is None else validate_name(identifier)
        if self._record_path(project, identifier).exists():
            raise FileExistsError("Image IDs cannot be reused, including deleted images")
        record = {"id": identifier, "format_version": 1, **info, "title": title,
                  "metadata": deepcopy(metadata), "created_at": now(), "deleted": False}
        if origin is not None:
            record["origin"] = deepcopy(origin)
        self.serialize(record)
        self.validate_record(identifier, record)
        self._write_blob(project, raw, info["sha256"])
        super().create(project, record, identifier=identifier)
        return record

    def create(self, project, data, *, identifier=None):
        raise ValueError("Use VisionData.import_image/aimport_image to register image bytes")

    def load(self, project, identifier):
        data = self._record(project, identifier)
        if data["deleted"]:
            raise FileNotFoundError(identifier)
        return data

    def list(self, project):
        root = self._checked(self.root(project) / "records")
        records = {p.stem: self._record(project, p.stem) for p in sorted(root.glob("*.json"))}
        return {key: value for key, value in records.items() if not value["deleted"]}

    def save(self, project, identifier, data):
        old = self.load(project, identifier)
        editable = {"title", "metadata"}
        if {k: v for k, v in old.items() if k not in editable} != {k: v for k, v in data.items() if k not in editable}:
            raise ValueError("Image content/provenance is immutable; edit title/metadata or register a new image")
        super().save(project, identifier, data)

    def delete(self, project, identifier):
        record = self.load(project, identifier)
        record.update(deleted=True, deleted_at=now())
        # 원본은 RAG/Step의 해시 참조를 위해 유지한다. 이미지 ID는 재사용하지 않는다.
        atomic_json(self._record_path(project, identifier), record)

    def read_image(self, project, identifier):
        record = self.load(project, identifier)
        raw = self._blob(project, record["sha256"]).read_bytes()
        if len(raw) != record["bytes"] or hashlib.sha256(raw).hexdigest() != record["sha256"]:
            raise ValueError("Image asset integrity mismatch")
        return record, raw

    def validate_backup(self, project):
        self.configuration(project)
        for path in self._checked(self.root(project) / "records").glob("*.json"):
            record = self._record(project, path.stem)
            raw = self._blob(project, record["sha256"]).read_bytes()
            if len(raw) != record["bytes"] or hashlib.sha256(raw).hexdigest() != record["sha256"]:
                raise ValueError("Image asset integrity mismatch")

    def clone(self, source, destination):
        self.validate_backup(source)
        for path in sorted(self._checked(self.root(source) / "records").glob("*.json")):
            record = self._record(source, path.stem)
            self._write_blob(destination, self._blob(source, record["sha256"]).read_bytes(), record["sha256"])
            # tombstone도 복제해 기존 출처와 ID 불변성을 유지한다.
            super().create(destination, record, identifier=path.stem)

    def resolve_runtime(self, project, capability, *, data_factory):
        data = data_factory(self.name)
        if capability == "vision":
            return data
        if capability == "tools":
            from .tools import vision_tools
            return vision_tools(data, self.binding(project), tuple(self.ocr_backends))
        return super().resolve_runtime(project, capability, data_factory=data_factory)

    def resolve(self, project, capability):
        raise ValueError("Vision runtime capabilities require a lifecycle-checked data_factory")
