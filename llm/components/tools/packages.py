"""Project Python Tool 패키지 저장·검증·로딩 및 ish 공용 dependency 준비.

소스는 신뢰한 Host/UI 관리 코드다. import 자체도 Python 실행이며 sandbox가 아니다.
"""

from contextlib import ExitStack
from copy import deepcopy
import hashlib
import importlib
import importlib.metadata
import os
from pathlib import Path
import stat
import sys
import types

from packaging.requirements import Requirement, InvalidRequirement
from packaging.utils import canonicalize_name
from llm.compat import dataclass
from llm.components.base import validate_name
from llm.services.infrastructure.storage import (reject_links, make_directory, prepare_replace,
    temporary_file, sync_directory, unlink_file)
from .function import function_tool


def _checked(path):
    if ".." in path.parts:
        raise ValueError("Tool package path cannot escape Project")
    return reject_links(path)


@dataclass(frozen=True, slots=True)
class ToolPaths:
    root: Path

    @classmethod
    def for_project(cls, project):
        return cls(_checked(project.paths.root / "tools"))

    def package(self, name):
        return _checked(self.root / validate_name(name))

    def source(self, name):
        return _checked(self.package(name) / (name + ".py"))

    def requirements(self, name):
        return _checked(self.package(name) / "requirements.txt")


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
    directory = paths.package(name)
    if not directory.is_dir():
        raise FileNotFoundError("Tool package does not exist")
    allowed = {name + ".py", "requirements.txt"}
    for path in directory.iterdir():
        if path.name not in allowed or not stat.S_ISREG(_checked(path).stat().st_mode):
            raise ValueError("Tool package supports only its source and requirements.txt regular files")
    value = {"source": paths.source(name).read_text(encoding="utf-8")}
    if paths.requirements(name).exists():
        value["requirements"] = paths.requirements(name).read_text(encoding="utf-8")
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
    make_directory(paths.package(name))
    _write_text(paths.source(name), data["source"])
    if "requirements" in data:
        _write_text(paths.requirements(name), data["requirements"])
    elif paths.requirements(name).exists():
        unlink_file(paths.requirements(name))


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


def _import_tool(project, name, data):
    paths = ToolPaths.for_project(project)
    digest = hashlib.sha256((str(paths.root.absolute()) + "\0" + data["source"] + "\0" +
                             data.get("requirements", "")).encode()).hexdigest()
    identity = f"_ish_llm_tool_{project.id}_{name}_{digest}"
    module = types.ModuleType(identity)
    module.__file__ = str(paths.source(name))
    # compile/exec는 timestamp pyc 재사용/생성을 피한다. 캐시 없이 Run마다 새 함수를 만든다.
    previous = sys.modules.get(identity)
    sys.modules[identity] = module
    try:
        exec(compile(data["source"], module.__file__, "exec"), module.__dict__)
        return function_tool(name, getattr(module, "main", None))
    finally:
        if previous is None:
            sys.modules.pop(identity, None)
        else:
            sys.modules[identity] = previous


def load_tool(project, name, *, prepare=False):
    """prepare만 ish installer를 호출한다. resolve는 availability만 확인하고 실패한다."""
    data = read_package(ToolPaths.for_project(project), name)
    wanted = requirements(data.get("requirements", ""))
    if not wanted:
        return _import_tool(project, name, data)
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
        return _import_tool(project, name, data)
