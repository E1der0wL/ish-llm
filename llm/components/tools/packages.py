"""Project Python Tool 패키지 저장·검증·로딩 및 ish 공용 dependency 준비.

소스는 신뢰한 Host/UI 관리 코드다. import 자체도 Python 실행이며 sandbox가 아니다.
"""

from contextlib import ExitStack
from copy import deepcopy
import importlib
import hashlib
import re
import importlib.metadata
import os
from pathlib import Path
import stat
import sys

from packaging.requirements import Requirement, InvalidRequirement
from packaging.utils import canonicalize_name
from dataclasses import dataclass
from typing import ClassVar
from llm.components.base import validate_name
from llm.components.usage import ModelUsageRecord
from llm.services.infrastructure.storage import (reject_links, make_directory, prepare_replace,
    temporary_file, sync_directory, unlink_file, atomic_json, read_json, remove_named_tree)
from .process import inspect_tool


def _checked(path):
    if ".." in path.parts:
        raise ValueError("Tool package path cannot escape Project")
    return reject_links(path)


@dataclass(frozen=True, slots=True)
class ToolPaths:
    COMPONENT: ClassVar[str] = "tools"
    SCHEMA_VERSION = 1
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
        return _checked(self.package(name) / (name + ".py"))

    def requirements(self, name):
        return _checked(self.package(name) / "requirements.txt")

    @property
    def manifest(self):
        return _checked(self.root / self.MANIFEST)

    def initialize(self):
        if self.manifest.exists():
            raise FileExistsError("Tool package manifest already exists")
        if {path.name for path in self.root.iterdir()} != {ModelUsageRecord.DIRECTORY}:
            raise ValueError("Cannot adopt undeclared Tool packages")
        atomic_json(self.manifest, {"schema_version": self.SCHEMA_VERSION,
            "project_id": self.project_id, "component": self.COMPONENT, "packages": {}})

    @staticmethod
    def fingerprints(value):
        return {"source": hashlib.sha256(value["source"].encode("utf-8")).hexdigest(),
                "requirements": hashlib.sha256(value["requirements"].encode("utf-8")).hexdigest()
                    if "requirements" in value else None}

    def inventory(self):
        """Owner-local existence and content proof; never import package code."""
        if not self.manifest.is_file():
            raise ValueError("Tool package manifest must be a regular file")
        manifest = read_json(self.manifest)
        if (not isinstance(manifest, dict)
                or manifest.keys() != {"schema_version", "project_id", "component", "packages"}
                or type(manifest["schema_version"]) is not int
                or manifest["schema_version"] != self.SCHEMA_VERSION
                or manifest["project_id"] != self.project_id or manifest["component"] != self.COMPONENT
                or not isinstance(manifest["packages"], dict)):
            raise ValueError("Invalid Tool package manifest")
        for name, entry in manifest["packages"].items():
            self.package(name)
            if (not isinstance(entry, dict) or entry.keys() != {"source", "requirements"}
                    or not isinstance(entry["source"], str) or not re.fullmatch(r"[a-f0-9]{64}", entry["source"])
                    or entry["requirements"] is not None and (not isinstance(entry["requirements"], str)
                        or not re.fullmatch(r"[a-f0-9]{64}", entry["requirements"]))):
                raise ValueError("Invalid Tool package content declaration")
        actual = set()
        for path in self.root.iterdir():
            _checked(path)
            if path.name in (self.MANIFEST, ModelUsageRecord.DIRECTORY):
                continue
            if not path.is_dir():
                raise ValueError("Tool packages must be directories")
            actual.add(validate_name(path.name))
        if actual != manifest["packages"].keys():
            raise ValueError("Tool packages are missing or undeclared")
        packages = {}
        for name in sorted(manifest["packages"]):
            entry = manifest["packages"][name]
            expected = {name + ".py"}
            if entry["requirements"] is not None:
                expected.add("requirements.txt")
            actual = set()
            for path in self.package(name).iterdir():
                if not stat.S_ISREG(_checked(path).stat().st_mode):
                    raise ValueError("Tool package contents must be regular files")
                actual.add(path.name)
            if actual != expected:
                raise ValueError("Tool package files are missing or undeclared")
            value = {"source": self.source(name).read_bytes().decode("utf-8")}
            if entry["requirements"] is not None:
                value["requirements"] = self.requirements(name).read_bytes().decode("utf-8")
            if self.fingerprints(value) != entry:
                raise ValueError("Tool package integrity mismatch")
            packages[name] = validate_source(value)
        return manifest, packages


def requirements(text):
    """단순 PEP 508만 허용한다. pip 옵션/파일 include/URL은 실행하지 않는다."""
    result = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            raise ValueError("Tool requirements only support PEP 508 requirement lines")
        try:
            item = Requirement(line)
        except InvalidRequirement as error:
            raise ValueError("Invalid Tool requirement") from error
        if item.url:
            raise ValueError("Tool requirements do not support URL/path installations")
        result.append(item)
    return result


def validate_source(data):
    if not isinstance(data, dict) or set(data) - {"source", "requirements"} or "source" not in data:
        raise ValueError("Tool package requires source and optional requirements")
    if any(not isinstance(value, str) for value in data.values()) or not data["source"].strip():
        raise ValueError("Tool package contents must be UTF-8 text with nonempty source")
    for value in data.values():
        value.encode("utf-8")
    requirements(data.get("requirements", ""))
    return deepcopy(data)


def read_package(paths, name):
    paths.package(name)
    _, packages = paths.inventory()
    if name not in packages:
        raise FileNotFoundError("Tool package does not exist")
    return packages[name]


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
    if set(files) not in ({name + ".py"}, {name + ".py", "requirements.txt"}):
        raise ValueError("Tool source package requires source and optional requirements")
    value = {"source": files[name + ".py"].read_bytes().decode("utf-8")}
    if "requirements.txt" in files:
        value["requirements"] = files["requirements.txt"].read_bytes().decode("utf-8")
    return validate_source(value)


def _write_text(path, text):
    prepare_replace(path)
    fd, temporary = temporary_file(path)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(text.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_package(paths, name, data):
    data = validate_source(data)
    manifest, _ = paths.inventory()
    make_directory(paths.package(name))
    _write_text(paths.source(name), data["source"])
    if "requirements" in data:
        _write_text(paths.requirements(name), data["requirements"])
    elif paths.requirements(name).exists():
        unlink_file(paths.requirements(name))
    manifest["packages"][name] = paths.fingerprints(data)
    atomic_json(paths.manifest, manifest)


def delete_package(paths, name):
    paths.package(name)
    manifest, packages = paths.inventory()
    if name not in packages:
        raise FileNotFoundError("Tool package does not exist")
    remove_named_tree(paths.root, paths.package(name), name)
    del manifest["packages"][name]
    atomic_json(paths.manifest, manifest)


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
    wanted = requirements(data.get("requirements", ""))
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
