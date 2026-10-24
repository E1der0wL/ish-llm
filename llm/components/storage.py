"""Shared Component file effects; definition and usage semantics stay with their Owners.

The selected Component supplies layout and validation callbacks. All writes
participate in the existing workspace transaction; this store grants no execution
authority and does not interpret per-Component business or quota policy.
"""

import hashlib
from pathlib import Path
from typing import Optional
from llm.core.models import Project, new_id
from llm.core.paths import ProjectPaths
from llm.services.infrastructure.storage import atomic_json, remove_named_tree
from .naming import ComponentName
from .record_manifest import ComponentRecordManifest


class ComponentStorage:
    MISSING_RECORD = "Component record does not exist"
    RETIRED_CONFIGURATION = "component.json"
    RETIRED_CONFIGURATION_MESSAGE = (
        "Legacy component.json is unsupported; configure ProjectConfig.parameters.components"
    )
    RESERVED_DIRECTORY = "Component directory conflicts with core storage"
    MISSING_INITIALIZED_STORAGE = "Initialized Component storage is missing"
    LINKED_PATH = "Component storage cannot follow linked paths"
    UNDECLARED_STORAGE = "Undeclared Component storage exists"
    UNINITIALIZED_USAGE = "Component model usage storage is not initialized"
    USAGE_INVENTORY_CHANGED = "Component model usage files are missing or undeclared"
    USAGE_EVIDENCE_CHANGED = "Component model usage owner or revision mismatch"
    WRONG_USAGE_OWNER = "Component model usage owner mismatch"
    MISSING_RECORD_DIRECTORY = "Component records directory is missing"
    INVALID_RECORD_ENTRY = "Invalid Component record entry"
    UNINITIALIZED_RECORDS = "Component record storage is not initialized"
    RECORD_INVENTORY_CHANGED = "Component records are missing or undeclared"
    RECORD_CHANGED = "Component record integrity mismatch"
    ADOPT_STORAGE = "Cannot initialize undeclared Component storage"
    RECORD_EXISTS = "Component record already exists"
    RECORDS_DIRECTORY = "records"
    RECORDS_MANIFEST = "records.manifest.json"
    RECORD_SUFFIX = ".json"
    AUTO_IDENTIFIER = None
    CREATE_RECORDS = True
    CREATE_RECORD_CATALOG = True
    INITIALIZED = True
    UNINITIALIZED = False
    INVENTORY_RECORDS = 1

    @staticmethod
    def reject_retired_configuration(component, project):
        if component._checked(component.root(project) / ComponentStorage.RETIRED_CONFIGURATION).exists():
            raise ValueError(ComponentStorage.RETIRED_CONFIGURATION_MESSAGE)

    @staticmethod
    def save(component, project, identifier, data):
        if not component._record_path(project, identifier).is_file():
            raise FileNotFoundError(ComponentStorage.MISSING_RECORD)
        component._write(project, identifier, data)

    @staticmethod
    def root(component, project: Project) -> Path:
        directory = ComponentName.validate(component.directory)
        if directory.lower() in ProjectPaths.CORE_DIRECTORIES:
            raise ValueError(ComponentStorage.RESERVED_DIRECTORY)
        root = component._checked(project.paths.root / directory)
        if component.name in project.initialized_components and not root.is_dir():
            raise ValueError(ComponentStorage.MISSING_INITIALIZED_STORAGE)
        return root

    @staticmethod
    def _checked(path: Path) -> Path:
        for entry in (path, *path.parents):
            if entry.is_symlink():
                raise ValueError(ComponentStorage.LINKED_PATH)
        return path

    @staticmethod
    def _record_path(component, project: Project, identifier: str) -> Path:
        return component._checked(
            component.root(project)
            / component.RECORDS_DIRECTORY
            / f"{ComponentName.validate(identifier)}{ComponentStorage.RECORD_SUFFIX}"
        )

    @staticmethod
    def _has_storage(component, project):
        """A disabled Component retains its storage declaration."""
        root = component.root(project)
        if component.name not in project.initialized_components:
            if root.exists():
                raise ValueError(ComponentStorage.UNDECLARED_STORAGE)
            return ComponentStorage.UNINITIALIZED
        if not root.is_dir():
            raise ValueError(ComponentStorage.MISSING_INITIALIZED_STORAGE)
        return ComponentStorage.INITIALIZED

    @staticmethod
    def _model_usage_records(component, project):
        from llm.components.usage import ModelUsageRecord, ModelUsageManifest
        from llm.services.infrastructure.storage import read_json, revision_token

        if not component._has_storage(project):
            raise ValueError(ComponentStorage.UNINITIALIZED_USAGE)
        root = component._usage_root(project)
        manifest = ModelUsageManifest.from_dict(
            read_json(component._checked(root / ModelUsageRecord.MANIFEST)),
            project_id=project.id,
            component=component.name,
        )
        actual = {component._checked(path).name for path in root.iterdir()}
        if actual != {
            ModelUsageRecord.MANIFEST,
            *(ModelUsageRecord.filename(identifier) for identifier in manifest.records),
        }:
            raise ValueError(ComponentStorage.USAGE_INVENTORY_CHANGED)
        records = {}
        for identifier in sorted(manifest.records):
            value = read_json(component._checked(root / ModelUsageRecord.filename(identifier)))
            record = ModelUsageRecord.from_dict(value)
            if (
                record.id != identifier
                or record.component != component.name
                or record.project_id != project.id
                or revision_token(value) != manifest.records[identifier]
            ):
                raise ValueError(ComponentStorage.USAGE_EVIDENCE_CHANGED)
            records[identifier] = record
        return manifest, records

    @staticmethod
    def model_usage(component, project) -> list:
        """All retained receipts, including disabled Component storage."""
        if not component._has_storage(project):
            return []
        return [
            record.to_dict()
            for record in component._model_usage_records(project)[ComponentStorage.INVENTORY_RECORDS].values()
        ]

    @staticmethod
    def _usage_root(component, project):
        from llm.components.usage import ModelUsageRecord

        return component._checked(component.root(project) / ModelUsageRecord.DIRECTORY)

    @staticmethod
    def save_model_usage(component, project, entry):
        from llm.components.usage import ModelUsageRecord
        from llm.services.infrastructure.storage import revision_token

        record = ModelUsageRecord.from_dict(entry)
        if record.component != component.name or record.project_id != project.id:
            raise ValueError(ComponentStorage.WRONG_USAGE_OWNER)
        manifest, records = component._model_usage_records(project)
        if record.id in records:
            record.validate_update(records[record.id])
        else:
            record.require_reservation()
        root = component._usage_root(project)
        value = record.to_dict()
        manifest = manifest.with_record(record.id, revision_token(value))
        atomic_json(root / ModelUsageRecord.filename(record.id), value)
        atomic_json(root / ModelUsageRecord.MANIFEST, manifest.to_dict())

    @staticmethod
    def _record_files(component, project):
        root = component._checked(component.root(project) / component.RECORDS_DIRECTORY)
        if not root.is_dir():
            raise ValueError(ComponentStorage.MISSING_RECORD_DIRECTORY)
        paths = {}
        for path in root.iterdir():
            component._checked(path)
            if path.suffix != ComponentStorage.RECORD_SUFFIX or not path.is_file():
                raise ValueError(ComponentStorage.INVALID_RECORD_ENTRY)
            paths[ComponentName.validate(path.stem)] = path
        return paths

    @staticmethod
    def _record_inventory(component, project):
        """Validate existence and bytes before an owner decodes or caches records."""
        from llm.services.infrastructure.storage import read_json

        if not component._has_storage(project):
            raise ValueError(ComponentStorage.UNINITIALIZED_RECORDS)
        manifest = read_json(component._checked(component.root(project) / component.RECORDS_MANIFEST))
        ComponentRecordManifest.validate(manifest, component.SCHEMA_VERSION, project.id, component.name)
        paths = component._record_files(project)
        if paths.keys() != ComponentRecordManifest(manifest).records.keys():
            raise ValueError(ComponentStorage.RECORD_INVENTORY_CHANGED)
        records = {}
        for identifier in sorted(paths):
            raw = paths[identifier].read_bytes()
            if hashlib.sha256(raw).hexdigest() != ComponentRecordManifest(manifest).records[identifier]:
                raise ValueError(ComponentStorage.RECORD_CHANGED)
            records[identifier] = raw
        return manifest, records

    @staticmethod
    def _initialize_storage(
        component, project, *, records=CREATE_RECORDS, record_catalog=CREATE_RECORD_CATALOG
    ):
        from llm.components.usage import ModelUsageRecord, ModelUsageManifest
        from llm.services.infrastructure.storage import make_directory

        component.get_config(project)
        root = component.root(project)
        if component.name in project.initialized_components:
            component._model_usage_records(project)
            if record_catalog:
                component._record_inventory(project)
            return
        if root.exists():
            raise ValueError(ComponentStorage.ADOPT_STORAGE)
        make_directory(root / component.RECORDS_DIRECTORY if records else root)
        atomic_json(
            component._usage_root(project) / ModelUsageRecord.MANIFEST,
            ModelUsageManifest.empty(project_id=project.id, component=component.name).to_dict(),
        )
        if record_catalog:
            atomic_json(
                root / component.RECORDS_MANIFEST,
                ComponentRecordManifest.empty(component.SCHEMA_VERSION, project.id, component.name),
            )

    @staticmethod
    def create(
        component, project: Project, data: dict, *, identifier: Optional[str] = AUTO_IDENTIFIER
    ) -> str:
        identifier = new_id() if identifier is ComponentStorage.AUTO_IDENTIFIER else identifier
        path = component._record_path(project, identifier)
        if path.exists():
            raise FileExistsError(ComponentStorage.RECORD_EXISTS)
        component._write(project, identifier, data)
        return identifier

    @staticmethod
    def _write(component, project: Project, identifier: str, data: dict) -> None:
        value = component.deserialize(component.serialize(data))
        component.validate_record(identifier, value)
        manifest, _ = component._record_inventory(project)
        path = component._record_path(project, identifier)
        atomic_json(path, value)
        ComponentRecordManifest(manifest).record(identifier, hashlib.sha256(path.read_bytes()).hexdigest())
        atomic_json(component.root(project) / component.RECORDS_MANIFEST, manifest)

    @staticmethod
    def load(component, project: Project, identifier: str) -> dict:
        component._record_path(project, identifier)
        _, records = component._record_inventory(project)
        if identifier not in records:
            raise FileNotFoundError(identifier)
        return component._decode_record(identifier, records[identifier])

    @staticmethod
    def list(component, project: Project) -> dict[str, dict]:
        if not component._has_storage(project):
            return {}
        _, records = component._record_inventory(project)
        return {identifier: component._decode_record(identifier, raw) for identifier, raw in records.items()}

    @staticmethod
    def delete(component, project: Project, identifier: str) -> None:
        component._record_path(project, identifier)
        manifest, records = component._record_inventory(project)
        if identifier not in records:
            raise FileNotFoundError(identifier)
        component._decode_record(identifier, records[identifier])
        component._remove_record(project, identifier)
        ComponentRecordManifest(manifest).remove(identifier)
        atomic_json(component.root(project) / component.RECORDS_MANIFEST, manifest)

    @staticmethod
    def _remove_record(component, project, identifier):
        from llm.services.infrastructure.storage import unlink_file

        unlink_file(component._record_path(project, identifier))

    @staticmethod
    def delete_directory(component, project: Project) -> None:
        """Explicit permanent removal, including component-owned artifacts."""
        root = component.root(project)
        if root.exists():
            component.validate_backup(project)
            remove_named_tree(project.paths.root, root, component.directory)
