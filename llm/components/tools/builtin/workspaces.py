"""명시적으로 생성·삭제하는 Project별 테스트 파일 공간과 Linux 샌드박스 실행.

Run/Step·승인·재시도는 ToolExecutor가 소유한다. 이 모듈의 record는 파일 공간의
소유권과 생성 완료 여부만 기록하며 실행 기록이나 재생 큐를 만들지 않는다.
"""

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
from uuid import uuid4
from typing import Mapping, Sequence

from llm._platform import require_linux
from llm.policies import ExecutionLimitError
from llm.services.infrastructure.storage import atomic_json, drain_on_cancel, sync_directory
from llm.services.runtime.processes import ProcessToolRunner, copy_env
from llm.services.runtime.tools import current_tool_call
from .files import FileTools


class TestWorkspaces:
    """호스트가 주입하는 파일 자원. 모델은 경로·mount·network 권한을 선택하지 않는다.

    source_root에서 명시한 파일만 복사한다. root는 소유권 기록/공간을 보관하는 전용
    디렉터리이며 다른 관리 파일을 넣지 않는다. allow_network는 반드시 호스트가 정한다.
    """

    STORAGE_VERSION = 1

    def __init__(self, root: str | Path, *, source_root: str | Path, allow_network: bool,
                 read_only_paths: Sequence[str | Path] = (), env: Mapping[str, str] | None = None,
                 max_output_bytes: int | None = None, memory_bytes: int | None = None,
                 python_executable: str | Path | None = None):
        require_linux()
        if type(allow_network) is not bool:
            raise ValueError("allow_network must be explicitly boolean")
        self.root = Path(root).absolute()
        self.source = FileTools(Path(source_root))
        self._check_path(self.root)
        if self.root == self.source.root or self.root in self.source.root.parents:
            raise ValueError("Test storage cannot contain the source root")
        mounts = tuple(Path(path).resolve(strict=True) for path in read_only_paths)
        for mount in mounts:
            if (mount == Path("/") or mount in (self.root, *self.root.parents)
                    or mount in (self.source.root, *self.source.root.parents)):
                raise ValueError("Read-only mounts cannot expose test storage or the source root")
        self._runtime = {"isolation": "sandbox", "allow_network": allow_network,
            "read_only_paths": mounts, "env": deepcopy(env), "max_output_bytes": max_output_bytes,
            "memory_bytes": memory_bytes, "python_executable": python_executable}
        # 생성 시 호스트 옵션을 검증하되 bwrap/명령 프로세스는 시작하지 않는다.
        validated = ProcessToolRunner({}, cwd=self.source.root, **self._runtime)
        self._runtime["python_executable"] = validated.python
        self.execution_binding = {"root": str(self.root), "source_root": str(self.source.root),
            **{key: [str(p) for p in value] if key == "read_only_paths" else
               str(value) if isinstance(value, Path) else deepcopy(value)
               for key, value in self._runtime.items()}}
        self._active = set()
        self._closed = False

    @staticmethod
    def _check_path(path):
        for part in (path, *path.parents):
            if part.is_symlink():
                raise ValueError("Test workspace paths cannot follow symlinks")

    def _project(self):
        if self._closed:
            raise RuntimeError("TestWorkspaces is closed")
        call = current_tool_call()
        if call is None or not call.project_id or not re.fullmatch(r"[A-Za-z0-9_-]+", call.project_id):
            raise ValueError("Test workspace Tools require an owning Project Tool invocation")
        return call

    def _paths(self, project_id, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ValueError("Invalid test workspace_id")
        project = self.root / project_id
        self._check_path(project)
        directory = project / identifier
        self._check_path(directory)
        return project, directory

    @contextmanager
    def _lease(self, project_id, identifier):
        project, directory = self._paths(project_id, identifier)
        locks = project / ".locks"
        self._check_path(locks)
        locks.mkdir(parents=True, exist_ok=True)
        fd = os.open(locks / (identifier + ".lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Test workspace is busy") from None
            yield directory, fd
        finally:
            # close만 사용: 시작 게이트가 상속한 동일 잠금의 수명도 보존한다.
            os.close(fd)

    def _record(self, directory, project_id, *, ready=False):
        path = directory / "record.json"
        self._check_path(path)
        if not directory.is_dir():
            raise FileNotFoundError("Test workspace does not exist")
        if not path.exists():
            if ready:
                raise ValueError("Test workspace creation is incomplete; explicitly delete it")
            # mkdir→atomic record, 혹은 delete의 마지막 구간에서 중단된 자원이다.
            # 신뢰된 Project 저장 경계 안의 ID만 관측/삭제하며 실행 권한은 만들지 않는다.
            return {"workspace_id": directory.name, "project_id": project_id, "ready": False,
                    "reason": "missing_record"}
        value = json.loads(path.read_text(encoding="utf-8"))
        fields = {"storage_version", "workspace_id", "project_id", "created_at", "created_by", "source_paths", "ready"}
        if (not isinstance(value, dict) or set(value) != fields
                or type(value.get("storage_version")) is not int or value["storage_version"] != self.STORAGE_VERSION
                or value["project_id"] != project_id or value["workspace_id"] != directory.name
                or type(value["ready"]) is not bool):
            raise ValueError("Invalid test workspace ownership record")
        if ready and not value["ready"]:
            raise ValueError("Test workspace creation is incomplete; explicitly delete it")
        return value

    def _copy(self, parent_fd, name, target):
        """정규 파일과 디렉터리만 복사한다. 링크·FIFO·device는 열지 않는다."""
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not (stat.S_ISREG(before.st_mode) or stat.S_ISDIR(before.st_mode)):
            raise ValueError("Only regular files and directories can be copied")
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        try:
            opened = os.fstat(fd)
            if (before.st_dev, before.st_ino, before.st_mode) != (opened.st_dev, opened.st_ino, opened.st_mode):
                raise ValueError("Source changed while preparing test workspace")
            if stat.S_ISDIR(opened.st_mode):
                target.mkdir()
                for child in sorted(os.listdir(fd)):
                    if child.startswith(".llm-"):
                        raise ValueError("Selected directory includes reserved Tool storage")
                    self._copy(fd, child, target / child)
                sync_directory(target)
            else:
                with os.fdopen(os.dup(fd), "rb") as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                    output.flush()
                    os.fchmod(output.fileno(), 0o600 | (opened.st_mode & 0o111))
                    os.fsync(output.fileno())
            after = os.fstat(fd)
            if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("Source changed while copying test workspace")
        finally:
            os.close(fd)

    def _create(self, call, paths):
        selected = []
        if not isinstance(paths, list) or any(not isinstance(path, str) for path in paths):
            raise ValueError("source_paths must be a list of relative paths")
        for value in paths:
            source = self.source.path(value)
            if source == self.source.root:
                raise ValueError("Select files/directories explicitly instead of the whole source root")
            if source == self.root or self.root in source.parents or source in self.root.parents:
                raise ValueError("Cannot copy test workspace storage")
            if any(source == other or source in other.parents or other in source.parents for other in selected):
                raise ValueError("Selected source paths overlap")
            selected.append(source)
        identifier = uuid4().hex
        with self._lease(call.project_id, identifier) as (directory, _):
            directory.mkdir()
            try:
                record = {"storage_version": self.STORAGE_VERSION, "workspace_id": identifier, "project_id": call.project_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "created_by": {"session_id": call.session_id, "run_id": call.run_id, "step_id": call.step_id},
                    "source_paths": [path.relative_to(self.source.root).as_posix() for path in selected], "ready": False}
                atomic_json(directory / "record.json", record)
                files = directory / "files"
                files.mkdir()
                for source in selected:
                    relative = source.relative_to(self.source.root)
                    target = files / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fd = os.open(self.source.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        for part in relative.parts[:-1]:
                            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                            os.close(fd)
                            fd = child
                        self._copy(fd, relative.name, target)
                    finally:
                        os.close(fd)
                # 파일과 중간 디렉터리가 durable해진 뒤에만 실행 가능한 공간으로 게시한다.
                for current, _, _ in os.walk(files, topdown=False):
                    sync_directory(Path(current))
                record["ready"] = True
                atomic_json(directory / "record.json", record)
                sync_directory(directory.parent)
                return record
            except BaseException:
                shutil.rmtree(directory)
                sync_directory(directory.parent)
                raise

    def _list(self, project_id):
        project = self.root / project_id
        self._check_path(project)
        if not project.exists():
            return {"workspaces": []}
        values = []
        for directory in sorted(project.iterdir()):
            if not re.fullmatch(r"[0-9a-f]{32}", directory.name):
                continue
            try:
                values.append(self._record(directory, project_id))
            except FileNotFoundError:
                # 생성/삭제 중일 수 있다. 목록은 실행 상태의 source of truth가 아니다.
                continue
        return {"workspaces": values}

    def _delete(self, project_id, identifier):
        with self._lease(project_id, identifier) as (directory, _):
            self._record(directory, project_id)
            # Linux의 fd 기반 rmtree는 산출물 symlink를 따라가지 않는다.
            if not shutil.rmtree.avoids_symlink_attacks:
                raise RuntimeError("Safe recursive deletion is unavailable")
            shutil.rmtree(directory)
            sync_directory(directory.parent)
        return {"workspace_id": identifier, "deleted": True}

    def _files(self, project_id, identifier, operation, args):
        with self._lease(project_id, identifier) as (directory, _):
            self._record(directory, project_id, ready=True)
            files = FileTools(directory / "files").for_arguments(args)
            # 임의 명령이 만든 FIFO/device를 file_read가 열어 대기하지 않게 한다.
            if "path" in args:
                target = files.path(args["path"])
                if target.exists() and not (target.is_file() or target.is_dir()):
                    raise ValueError("Test output must be a regular file or directory")
            return getattr(files, operation)(args)

    async def create(self, args: dict) -> dict:
        """빈 공간 또는 명시한 파일의 독립 사본을 만든다. 취소 시 승인된 생성의 회수를 기다린다."""
        return await drain_on_cancel(asyncio.to_thread(self._create, self._project(), args["source_paths"]))

    async def list(self, args: dict) -> dict:
        """재시작 후에도 해당 Project의 공간을 조회한다. 명령을 재생하지 않는다."""
        return await asyncio.to_thread(self._list, self._project().project_id)

    async def delete(self, args: dict) -> dict:
        """사용 중이지 않은 공간을 명시적으로 삭제한다. 원본에는 접근하지 않는다."""
        return await drain_on_cancel(asyncio.to_thread(self._delete, self._project().project_id, args["workspace_id"]))

    async def file_call(self, operation: str, args: dict) -> dict:
        """기존 파일 Tool 계약을 선택 공간에 적용한다. 실행 중인 공간에는 접근하지 않는다."""
        return await drain_on_cancel(asyncio.to_thread(self._files, self._project().project_id,
            args["workspace_id"], operation, args))

    async def __call__(self, args: dict) -> dict:
        """ToolExecutor가 승인한 argv만 실행한다. 완료/취소 후에도 파일 공간은 유지한다."""
        call = self._project()
        task = asyncio.current_task()
        self._active.add(task)
        try:
            # nonblocking flock: 다른 프로세스/Tool의 삭제·파일 변경과 실행을 직렬화한다.
            # 잠금의 작은 메타데이터 I/O만 동기, 복사/조회/삭제는 위의 off-loop 경로다.
            with self._lease(call.project_id, args["workspace_id"]) as (directory, fd):
                self._record(directory, call.project_id, ready=True)
                files = FileTools(directory / "files")
                # Tool 인자의 env는 승인된 전체 환경이다. 호스트 환경과 암묵적으로 병합하지 않는다.
                runtime = dict(self._runtime)
                if "env" in args:
                    runtime["env"] = copy_env(args["env"])
                runner = ProcessToolRunner({}, cwd=files.root, **runtime)
                result = await runner.execute(args["argv"], pass_fds=(fd,))
                return {"workspace_id": args["workspace_id"], **result}
        finally:
            self._active.discard(task)

    async def close(self) -> None:
        """실행만 회수한다. 영속 파일은 명시적 delete까지 보관한다."""
        self._closed = True
        pending = tuple(self._active)
        for task in pending:
            task.cancel()
        await drain_on_cancel(asyncio.gather(*pending, return_exceptions=True))


class BuiltinToolRunner:
    """테스트 공간 실행만 sandbox handler로 전달하고 다른 Tool은 기존 경로로 실행한다."""

    def __init__(self, fallback=None):
        self.fallback = fallback

    def validate_tool(self, name, isolation):
        if name == "test_workspace_execute" and isolation == "sandbox":
            if shutil.which("bwrap") is None:
                raise ExecutionLimitError("sandbox_unavailable", "Linux Bubblewrap is required")
            return
        if self.fallback is None or not hasattr(self.fallback, "validate_tool"):
            raise ExecutionLimitError("tool_contract", "Runner does not satisfy Tool isolation requirements")
        self.fallback.validate_tool(name, isolation)

    async def __call__(self, tool, call):
        if tool.name == "test_workspace_execute":
            if not isinstance(tool.handler, TestWorkspaces):
                raise ExecutionLimitError("tool_contract", "Sandbox Tool requires a trusted TestWorkspaces handler")
            return await tool.handler(call.arguments)
        if self.fallback is not None:
            return await self.fallback(tool, call)
        if tool.contract is not None and tool.contract.isolation != "none":
            raise ExecutionLimitError("tool_contract", "Tool requires another isolation runner")
        return await tool.handler(call.arguments)
