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
from dataclasses import dataclass
from typing import ClassVar
from llm.components.base import validate_name
from llm.components.usage import ModelUsageRecord
from llm.services.infrastructure.storage import (reject_links, make_directory, prepare_replace,
    temporary_file, sync_directory, unlink_file, atomic_json, read_json, remove_named_tree)
from .process import inspect_tool
from .package_values import ToolPackageSource, ToolPackageDigests, ToolPackageManifest, ToolRequirements


def _checked(path):
    if ".." in path.parts:
        raise ValueError("Tool package path cannot escape Project")
    return reject_links(path)


@dataclass(frozen=True, slots=True)
class ToolPaths:
    COMPONENT: ClassVar[str] = "tools"
    SCHEMA_VERSION = ToolPackageManifest.VERSION
    SOURCE_SUFFIX = ".py"
    REQUIREMENTS_FILE = "requirements.txt"
    MANIFEST = "packages.manifest.json"
    root: Path
    project_id: str

    def __post_init__(self):
        validate_name(self.project_id)

    @classmethod
    def for_project(cls, project):
        root = _checked(project.paths.root / cls.COMPONENT)
        if cls.COMPONENT not in project.initialized_components and root.exists():
            raise ValueError("Undeclared Tool storage exists")
        if cls.COMPONENT in project.initialized_components and not root.is_dir():
            raise ValueError("Initialized Tool storage is missing")
        return cls(root, project.id)

    def package(self, name):
        if name == ModelUsageRecord.DIRECTORY:
            raise ValueError("Reserved Tool package identifier")
        return _checked(self.root / validate_name(name))

    def source(self, name):
        return _checked(self.package(name) / (name + self.SOURCE_SUFFIX))

    def requirements(self, name):
        return _checked(self.package(name) / self.REQUIREMENTS_FILE)

    @property
    def manifest(self):
        return _checked(self.root / self.MANIFEST)

    def initialize(self):
        if self.manifest.exists():
            raise FileExistsError("Tool package manifest already exists")
        if {path.name for path in self.root.iterdir()} != {ModelUsageRecord.DIRECTORY}:
            raise ValueError("Cannot adopt undeclared Tool packages")
        atomic_json(self.manifest, ToolPackageManifest.create(self.project_id, self.COMPONENT).to_dict())

    @staticmethod
    def fingerprints(value):
        return ToolPackageDigests.fingerprint_record(value).to_dict()

    def inventory(self):
        """Owner-local existence and content proof; never import package code."""
        if not self.manifest.is_file():
            raise ValueError("Tool package manifest must be a regular file")
        manifest = ToolPackageManifest.read(read_json(self.manifest), project_id=self.project_id, component=self.COMPONENT)
        for name in manifest.packages:
            self.package(name)
        actual = set()
        for path in self.root.iterdir():
            _checked(path)
            if path.name in (self.MANIFEST, ModelUsageRecord.DIRECTORY):
                continue
            if not path.is_dir():
                raise ValueError("Tool packages must be directories")
            actual.add(validate_name(path.name))
        if actual != manifest.packages.keys():
            raise ValueError("Tool packages are missing or undeclared")
        packages = {}
        for name in sorted(manifest.packages):
            entry = manifest.packages[name]
            expected = {name + self.SOURCE_SUFFIX}
            if entry.has_requirements:
                expected.add(self.REQUIREMENTS_FILE)
            actual = set()
            for path in self.package(name).iterdir():
                if not stat.S_ISREG(_checked(path).stat().st_mode):
                    raise ValueError("Tool package contents must be regular files")
                actual.add(path.name)
            if actual != expected:
                raise ValueError("Tool package files are missing or undeclared")
            source = self.source(name).read_bytes().decode(ToolPackageSource.ENCODING)
            wanted = (self.requirements(name).read_bytes().decode(ToolPackageSource.ENCODING)
                      if entry.has_requirements else ToolPackageSource.ABSENT_REQUIREMENTS)
            if ToolPackageDigests.for_text(source, wanted) != entry:
                raise ValueError("Tool package integrity mismatch")
            packages[name] = ToolPackageSource(source, wanted)
        return manifest, packages


# Public validators delegate to the one current definition Owner.
requirements = ToolRequirements.parse


def validate_source(data):
    return ToolPackageSource.read(data).to_dict()


def read_package(paths, name):
    paths.package(name)
    _, packages = paths.inventory()
    if name not in packages:
        raise FileNotFoundError("Tool package does not exist")
    return packages[name].to_dict()


def read_source_package(directory):
    """Read an explicit external import, before it becomes Project-owned storage."""
    directory = _checked(Path(directory))
    name = validate_name(directory.name)
    if name == ModelUsageRecord.DIRECTORY:
        raise ValueError("Reserved Tool package identifier")
    files = {}
    for path in directory.iterdir():
        if not stat.S_ISREG(_checked(path).stat().st_mode):
            raise ValueError("Tool package contents must be regular files")
        files[path.name] = path
    if set(files) not in ({name + ToolPaths.SOURCE_SUFFIX}, {name + ToolPaths.SOURCE_SUFFIX, ToolPaths.REQUIREMENTS_FILE}):
        raise ValueError("Tool source package requires source and optional requirements")
    source = files[name + ToolPaths.SOURCE_SUFFIX].read_bytes().decode(ToolPackageSource.ENCODING)
    wanted = (files[ToolPaths.REQUIREMENTS_FILE].read_bytes().decode(ToolPackageSource.ENCODING)
              if ToolPaths.REQUIREMENTS_FILE in files else ToolPackageSource.ABSENT_REQUIREMENTS)
    return ToolPackageSource(source, wanted).to_dict()


def _write_text(path, text):
    prepare_replace(path)
    fd, temporary = temporary_file(path)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(text.encode(ToolPackageSource.ENCODING))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


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
        raise FileNotFoundError("Tool package does not exist")
    remove_named_tree(paths.root, paths.package(name), name)
    atomic_json(paths.manifest, manifest.without_package(name).to_dict())


def _installed(target):
    installed = {}
    for dist in importlib.metadata.distributions(path=[str(target)]):
        name = dist.metadata["Name"]
        if not name:
            continue
        key = canonicalize_name(name)
        if key in installed:
            raise ValueError(f"Ambiguous shared Tool dependency installation: {name}")
        installed[key] = dist
    return installed


def _missing(wanted, installed):
    """기존 설치는 전부 고정한다. extras의 전이 dependency도 확인한다."""
    missing, seen = [], set()
    def visit(req, extras=("",)):
        if req.marker and not any(req.marker.evaluate({"extra": extra}) for extra in extras):
            return
        key = str(req)
        if key in seen:
            return
        seen.add(key)
        dist = installed.get(canonicalize_name(req.name))
        if dist is None:
            # extra marker는 부모 dependency 문맥에서 이미 평가했다. 독립 pip 요청에서
            # 다시 extra=''로 평가되어 설치를 건너뛰지 않도록 활성 requirement만 전달한다.
            active = Requirement(str(req))
            active.marker = None
            missing.append(active)
            return
        if not req.specifier.contains(dist.version):
            raise ValueError(f"Shared Tool dependency conflict: {req.name} {dist.version} does not satisfy {req.specifier}")
        for child in dist.requires or ():
            visit(Requirement(child), tuple(req.extras) or ("",))
    for req in wanted:
        visit(req)
    return missing


def _host_target():
    try:
        from ish.config import config
    except ImportError as error:
        raise RuntimeError("Tool dependencies require the ish host and its PLUGIN_LIB_DIR") from error
    return _checked(Path(config.PLUGIN_LIB_DIR))


def load_tool(project, name, *, prepare=False):
    """prepare만 ish installer를 호출한다. resolve는 availability만 확인하고 실패한다."""
    data = read_package(ToolPaths.for_project(project), name)
    wanted = requirements(ToolPackageSource.read(data).requirements_text)
    if not wanted:
        # Host 공용 경로는 dependency가 없는 Tool에서도 제공할 수 있다.
        config = sys.modules.get("ish.config")
        target = getattr(getattr(config, "config", None), "PLUGIN_LIB_DIR", None)
        return inspect_tool(project, name, data, target)
    target = _host_target()
    installed = _installed(target)
    missing = _missing(wanted, installed)
    if missing and not prepare:
        raise ValueError("Tool dependencies are unavailable; call tools.prepare() explicitly")
    # host가 이미 sys.path를 설정한다. 명시적인 prepare에서도 같은 공용 경로만 사용한다.
    if str(target) not in sys.path:
        sys.path.insert(0, str(target))
    with ExitStack() as stack:
        if missing:
            from ish.plugin.dependencies import install_dependency
            pins = [f"{name}=={dist.version}" for name, dist in installed.items()]
            constraints = pins + [req.name + str(req.specifier) for req in wanted
                                 if not req.marker or req.marker.evaluate()]
            for req in missing:
                if not _missing([req], _installed(target)):
                    continue
                stack.enter_context(install_dependency(sys.executable, str(req), target, constraints=constraints))
            if _missing(wanted, _installed(target)):
                raise ValueError("Tool dependency installation did not satisfy requirements")
        importlib.invalidate_caches()
        return inspect_tool(project, name, data, target)
