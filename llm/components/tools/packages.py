"""Project Python Tool 패키지 저장·검증·로딩 및 ish 공용 dependency 준비.

소스는 신뢰한 Host/UI 관리 코드다. import 자체도 Python 실행이며 sandbox가 아니다.
"""

from contextlib import ExitStack
import importlib
import importlib.metadata
import os
from pathlib import Path
import stat
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from dataclasses import asdict, dataclass
from typing import ClassVar
from llm.components.base import validate_name
from llm.components.usage import ModelUsageRecord
from llm.services.infrastructure.storage import (
    reject_links,
    make_directory,
    prepare_replace,
    temporary_file,
    sync_directory,
    unlink_file,
    atomic_json,
    read_json,
    remove_named_tree,
)
from .process import inspect_tool
from .package_values import (
    ToolPackageSource,
    ToolPackageDigests,
    ToolPackageManifest,
    ToolRequirements,
)


@dataclass(frozen=True, slots=True)
class ToolPaths:
    COMPONENT: ClassVar[str] = "tools"
    SCHEMA_VERSION = ToolPackageManifest.VERSION
    SOURCE_SUFFIX = ".py"
    REQUIREMENTS_FILE = "requirements.txt"
    MANIFEST = "packages.manifest.json"
    PARENT_SEGMENT = ".."
    WRITE_MODE = "wb"
    ESCAPED_PATH = "Tool package path cannot escape Project"
    UNDECLARED_STORAGE = "Undeclared Tool storage exists"
    MISSING_STORAGE = "Initialized Tool storage is missing"
    RESERVED_IDENTIFIER = "Reserved Tool package identifier"
    MANIFEST_EXISTS = "Tool package manifest already exists"
    UNDECLARED_PACKAGES = "Cannot adopt undeclared Tool packages"
    INVALID_MANIFEST_FILE = "Tool package manifest must be a regular file"
    INVALID_PACKAGE_DIRECTORY = "Tool packages must be directories"
    INVENTORY_MISMATCH = "Tool packages are missing or undeclared"
    INVALID_PACKAGE_FILE = "Tool package contents must be regular files"
    FILE_SET_MISMATCH = "Tool package files are missing or undeclared"
    INTEGRITY_MISMATCH = "Tool package integrity mismatch"
    MISSING_PACKAGE = "Tool package does not exist"
    INVALID_IMPORT = "Tool source package requires source and optional requirements"
    REMOVE_ABSENT_TEMPORARY = True
    root: Path
    project_id: str

    def __post_init__(self):
        validate_name(self.project_id)

    @classmethod
    def checked(cls, path):
        if cls.PARENT_SEGMENT in path.parts:
            raise ValueError(cls.ESCAPED_PATH)
        return reject_links(path)

    @classmethod
    def for_project(cls, project):
        root = _checked(project.paths.root / cls.COMPONENT)
        if cls.COMPONENT not in project.initialized_components and root.exists():
            raise ValueError(ToolPaths.UNDECLARED_STORAGE)
        if cls.COMPONENT in project.initialized_components and not root.is_dir():
            raise ValueError(ToolPaths.MISSING_STORAGE)
        return cls(root, project.id)

    def package(self, name):
        if name == ModelUsageRecord.DIRECTORY:
            raise ValueError(ToolPaths.RESERVED_IDENTIFIER)
        return _checked(self.root / validate_name(name))

    def modified_at(self, name):
        """Display-only timestamp; callers obtain the current package snapshot first."""
        return max(path.stat().st_mtime for path in self.package(name).iterdir())

    def source(self, name):
        return _checked(self.package(name) / (name + self.SOURCE_SUFFIX))

    def requirements(self, name):
        return _checked(self.package(name) / self.REQUIREMENTS_FILE)

    @property
    def manifest(self):
        return _checked(self.root / self.MANIFEST)

    def initialize(self):
        if self.manifest.exists():
            raise FileExistsError(ToolPaths.MANIFEST_EXISTS)
        if {path.name for path in self.root.iterdir()} != {ModelUsageRecord.DIRECTORY}:
            raise ValueError(ToolPaths.UNDECLARED_PACKAGES)
        atomic_json(
            self.manifest,
            ToolPackageManifest.create(self.project_id, self.COMPONENT).to_dict(),
        )

    @staticmethod
    def fingerprints(value):
        return ToolPackageDigests.fingerprint_record(value).to_dict()

    def inventory(self):
        """Owner-local existence and content proof; never import package code."""
        if not self.manifest.is_file():
            raise ValueError(ToolPaths.INVALID_MANIFEST_FILE)
        manifest = ToolPackageManifest.read(
            read_json(self.manifest),
            project_id=self.project_id,
            component=self.COMPONENT,
        )
        for name in manifest.packages:
            self.package(name)
        actual = set()
        for path in self.root.iterdir():
            _checked(path)
            if path.name in (self.MANIFEST, ModelUsageRecord.DIRECTORY):
                continue
            if not path.is_dir():
                raise ValueError(ToolPaths.INVALID_PACKAGE_DIRECTORY)
            actual.add(validate_name(path.name))
        if actual != manifest.packages.keys():
            raise ValueError(ToolPaths.INVENTORY_MISMATCH)
        packages = {}
        for name in sorted(manifest.packages):
            entry = manifest.packages[name]
            expected = {name + self.SOURCE_SUFFIX}
            if entry.has_requirements:
                expected.add(self.REQUIREMENTS_FILE)
            actual = set()
            for path in self.package(name).iterdir():
                if not stat.S_ISREG(_checked(path).stat().st_mode):
                    raise ValueError(ToolPaths.INVALID_PACKAGE_FILE)
                actual.add(path.name)
            if actual != expected:
                raise ValueError(ToolPaths.FILE_SET_MISMATCH)
            source = self.source(name).read_bytes().decode(ToolPackageSource.ENCODING)
            wanted = (
                self.requirements(name).read_bytes().decode(ToolPackageSource.ENCODING)
                if entry.has_requirements
                else ToolPackageSource.ABSENT_REQUIREMENTS
            )
            if ToolPackageDigests.for_text(source, wanted) != entry:
                raise ValueError(ToolPaths.INTEGRITY_MISMATCH)
            packages[name] = ToolPackageSource(source, wanted)
        return manifest, packages


_checked = ToolPaths.checked


# Public validators delegate to the one current definition Owner.
requirements = ToolRequirements.parse


def validate_source(data):
    return ToolPackageSource.read(data).to_dict()


def read_package(paths, name):
    paths.package(name)
    _, packages = paths.inventory()
    if name not in packages:
        raise FileNotFoundError(ToolPaths.MISSING_PACKAGE)
    return packages[name].to_dict()


def read_source_package(directory):
    """Read an explicit external import, before it becomes Project-owned storage."""
    directory = _checked(Path(directory))
    name = validate_name(directory.name)
    if name == ModelUsageRecord.DIRECTORY:
        raise ValueError(ToolPaths.RESERVED_IDENTIFIER)
    files = {}
    for path in directory.iterdir():
        if not stat.S_ISREG(_checked(path).stat().st_mode):
            raise ValueError(ToolPaths.INVALID_PACKAGE_FILE)
        files[path.name] = path
    if set(files) not in (
        {name + ToolPaths.SOURCE_SUFFIX},
        {name + ToolPaths.SOURCE_SUFFIX, ToolPaths.REQUIREMENTS_FILE},
    ):
        raise ValueError(ToolPaths.INVALID_IMPORT)
    source = (
        files[name + ToolPaths.SOURCE_SUFFIX]
        .read_bytes()
        .decode(ToolPackageSource.ENCODING)
    )
    wanted = (
        files[ToolPaths.REQUIREMENTS_FILE]
        .read_bytes()
        .decode(ToolPackageSource.ENCODING)
        if ToolPaths.REQUIREMENTS_FILE in files
        else ToolPackageSource.ABSENT_REQUIREMENTS
    )
    return ToolPackageSource(source, wanted).to_dict()


def _write_text(path, text):
    prepare_replace(path)
    fd, temporary = temporary_file(path)
    try:
        with os.fdopen(fd, ToolPaths.WRITE_MODE) as stream:
            stream.write(text.encode(ToolPackageSource.ENCODING))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=ToolPaths.REMOVE_ABSENT_TEMPORARY)


def write_package(paths, name, data):
    data = ToolPackageSource.read(data)
    manifest, _ = paths.inventory()
    make_directory(paths.package(name))
    _write_text(paths.source(name), data.source)
    if data.has_requirements:
        _write_text(paths.requirements(name), data.requirements)
    elif paths.requirements(name).exists():
        unlink_file(paths.requirements(name))
    atomic_json(paths.manifest, manifest.with_package(name, data).to_dict())


def delete_package(paths, name):
    paths.package(name)
    manifest, packages = paths.inventory()
    if name not in packages:
        raise FileNotFoundError(ToolPaths.MISSING_PACKAGE)
    remove_named_tree(paths.root, paths.package(name), name)
    atomic_json(paths.manifest, manifest.without_package(name).to_dict())


@dataclass(frozen=True, slots=True)
class RequirementEnvironment:
    """The packaging marker wire field; environment policy remains with packaging."""

    extra: str

    def to_dict(self):
        return asdict(self)


class ToolDependencies:
    """Observe or explicitly prepare the host's shared dependency installation."""

    DISTRIBUTION_NAME = "Name"
    NO_EXTRAS = ("",)
    NOT_INSTALLED = None
    NO_MARKER = None
    HOST_MODULE = "ish.config"
    HOST_CONFIGURATION = "config"
    HOST_PLUGIN_DIRECTORY = "PLUGIN_LIB_DIR"
    HOST_NOT_LOADED = None
    PREPARE_BY_DEFAULT = False
    PATH_PRIORITY = 0
    PIN = "{name}=={version}"
    AMBIGUOUS = "Ambiguous shared Tool dependency installation: {name}"
    CONFLICT = (
        "Shared Tool dependency conflict: {name} {version} does not satisfy {specifier}"
    )
    MISSING_HOST = "Tool dependencies require the ish host and its PLUGIN_LIB_DIR"
    UNAVAILABLE = "Tool dependencies are unavailable; call tools.prepare() explicitly"
    UNSATISFIED = "Tool dependency installation did not satisfy requirements"

    @staticmethod
    def installed(target):
        installed = {}
        for dist in importlib.metadata.distributions(path=[str(target)]):
            name = dist.metadata[ToolDependencies.DISTRIBUTION_NAME]
            if not name:
                continue
            key = canonicalize_name(name)
            if key in installed:
                raise ValueError(ToolDependencies.AMBIGUOUS.format(name=name))
            installed[key] = dist
        return installed

    @staticmethod
    def missing(wanted, installed):
        """기존 설치는 전부 고정한다. extras의 전이 dependency도 확인한다."""
        missing, seen = [], set()

        def visit(req, extras=ToolDependencies.NO_EXTRAS):
            if req.marker and not any(
                req.marker.evaluate(RequirementEnvironment(extra).to_dict())
                for extra in extras
            ):
                return
            key = str(req)
            if key in seen:
                return
            seen.add(key)
            dist = installed.get(canonicalize_name(req.name))
            if dist is ToolDependencies.NOT_INSTALLED:
                # extra marker는 부모 dependency 문맥에서 이미 평가했다. 독립 pip 요청에서
                # 다시 extra=''로 평가되어 설치를 건너뛰지 않도록 활성 requirement만 전달한다.
                active = Requirement(str(req))
                active.marker = ToolDependencies.NO_MARKER
                missing.append(active)
                return
            if not req.specifier.contains(dist.version):
                raise ValueError(
                    ToolDependencies.CONFLICT.format(
                        name=req.name, version=dist.version, specifier=req.specifier
                    )
                )
            for child in dist.requires or ():
                visit(
                    Requirement(child), tuple(req.extras) or ToolDependencies.NO_EXTRAS
                )

        for req in wanted:
            visit(req)
        return missing

    @staticmethod
    def host_target():
        try:
            from ish.config import config
        except ImportError as error:
            raise RuntimeError(ToolDependencies.MISSING_HOST) from error
        return _checked(Path(config.PLUGIN_LIB_DIR))

    @staticmethod
    def loaded_target():
        config = sys.modules.get(ToolDependencies.HOST_MODULE)
        return getattr(
            getattr(
                config,
                ToolDependencies.HOST_CONFIGURATION,
                ToolDependencies.HOST_NOT_LOADED,
            ),
            ToolDependencies.HOST_PLUGIN_DIRECTORY,
            ToolDependencies.HOST_NOT_LOADED,
        )


# Existing injection points alias the single implementations, including crash tests.
_installed = ToolDependencies.installed
_missing = ToolDependencies.missing
_host_target = ToolDependencies.host_target


def load_tool(project, name, *, prepare=ToolDependencies.PREPARE_BY_DEFAULT):
    """prepare만 ish installer를 호출한다. resolve는 availability만 확인하고 실패한다."""
    data = read_package(ToolPaths.for_project(project), name)
    wanted = requirements(ToolPackageSource.read(data).requirements_text)
    if not wanted:
        # Host 공용 경로는 dependency가 없는 Tool에서도 제공할 수 있다.
        target = ToolDependencies.loaded_target()
        return inspect_tool(project, name, data, target)
    target = _host_target()
    installed = _installed(target)
    missing = _missing(wanted, installed)
    if missing and not prepare:
        raise ValueError(ToolDependencies.UNAVAILABLE)
    # host가 이미 sys.path를 설정한다. 명시적인 prepare에서도 같은 공용 경로만 사용한다.
    if str(target) not in sys.path:
        sys.path.insert(ToolDependencies.PATH_PRIORITY, str(target))
    with ExitStack() as stack:
        if missing:
            from ish.plugin.dependencies import install_dependency

            pins = [
                ToolDependencies.PIN.format(name=name, version=dist.version)
                for name, dist in installed.items()
            ]
            constraints = pins + [
                req.name + str(req.specifier)
                for req in wanted
                if not req.marker or req.marker.evaluate()
            ]
            for req in missing:
                if not _missing([req], _installed(target)):
                    continue
                stack.enter_context(
                    install_dependency(
                        sys.executable, str(req), target, constraints=constraints
                    )
                )
            if _missing(wanted, _installed(target)):
                raise ValueError(ToolDependencies.UNSATISFIED)
        importlib.invalidate_caches()
        return inspect_tool(project, name, data, target)
