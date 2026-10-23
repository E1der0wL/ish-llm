"""명시적으로 생성·삭제하는 Project별 테스트 파일 공간과 Linux 샌드박스 실행.

Run/Step·승인·재시도는 ToolExecutor가 소유한다. 이 모듈의 record는 파일 공간의
소유권과 생성 완료 여부만 기록하며 실행 기록이나 재생 큐를 만들지 않는다.
"""

from llm.core.models import RunErrorCode

import asyncio
from copy import deepcopy
from pathlib import Path
import shutil
from typing import Mapping, Sequence

from llm._platform import require_linux
from llm.services.infrastructure.storage import drain_on_cancel
from llm.policies import ExecutionLimitError
from llm.services.runtime.processes import ProcessToolRunner, copy_env
from llm.services.runtime.tools import current_tool_call
from llm.core.tool_contracts import ToolIsolation
from .workspace_records import WorkspaceRecord, WorkspaceIdentity
from .workspace_repository import WorkspaceRepository


class TestWorkspaces:
    """호스트가 주입하는 파일 자원. 모델은 경로·mount·network 권한을 선택하지 않는다.

    source_root에서 명시한 파일만 복사한다. root는 소유권 기록/공간을 보관하는 전용
    디렉터리이며 다른 관리 파일을 넣지 않는다. allow_network는 반드시 호스트가 정한다.
    """

    STORAGE_VERSION = WorkspaceRecord.VERSION

    def __init__(self, root: str | Path, *, source_root: str | Path, allow_network: bool,
                 read_only_paths: Sequence[str | Path] = (), env: Mapping[str, str] | None = None,
                 max_output_bytes: int | None = None, memory_bytes: int | None = None,
                 python_executable: str | Path | None = None):
        require_linux()
        if type(allow_network) is not bool:
            raise ValueError("allow_network must be explicitly boolean")
        self.repository = WorkspaceRepository(root, source_root)
        mounts = tuple(Path(path).resolve(strict=True) for path in read_only_paths)
        for mount in mounts:
            if (mount == Path("/") or mount in (self.root, *self.root.parents)
                    or mount in (self.source.root, *self.source.root.parents)):
                raise ValueError("Read-only mounts cannot expose test storage or the source root")
        self._runtime = {"isolation": ToolIsolation.SANDBOX, "allow_network": allow_network,
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


    @property
    def root(self):
        return self.repository.root

    @property
    def source(self):
        return self.repository.source

    def _project(self):
        if self._closed:
            raise RuntimeError("TestWorkspaces is closed")
        call = current_tool_call()
        if call is None or not call.project_id or not WorkspaceIdentity.valid_project(call.project_id):
            raise ValueError("Test workspace Tools require an owning Project Tool invocation")
        return call


    async def create(self, args: dict) -> dict:
        """빈 공간 또는 명시한 파일의 독립 사본을 만든다. 취소 시 승인된 생성의 회수를 기다린다."""
        return (await drain_on_cancel(asyncio.to_thread(self.repository.create, self._project(), args["source_paths"]))).to_dict()

    async def list(self, args: dict) -> dict:
        """재시작 후에도 해당 Project의 공간을 조회한다. 명령을 재생하지 않는다."""
        return (await asyncio.to_thread(self.repository.list, self._project().project_id)).to_dict()

    async def delete(self, args: dict) -> dict:
        """사용 중이지 않은 공간을 명시적으로 삭제한다. 원본에는 접근하지 않는다."""
        return (await drain_on_cancel(asyncio.to_thread(self.repository.delete, self._project().project_id, args["workspace_id"]))).to_dict()

    async def file_call(self, operation: str, args: dict) -> dict:
        """기존 파일 Tool 계약을 선택 공간에 적용한다. 실행 중인 공간에는 접근하지 않는다."""
        return await drain_on_cancel(asyncio.to_thread(self.repository.files, self._project().project_id,
            args["workspace_id"], operation, args))

    async def __call__(self, args: dict) -> dict:
        """ToolExecutor가 승인한 argv만 실행한다. 완료/취소 후에도 파일 공간은 유지한다."""
        call = self._project()
        task = asyncio.current_task()
        self._active.add(task)
        try:
            # nonblocking flock: 다른 프로세스/Tool의 삭제·파일 변경과 실행을 직렬화한다.
            # 잠금의 작은 메타데이터 I/O만 동기, 복사/조회/삭제는 위의 off-loop 경로다.
            with self.repository.lease(call.project_id, args["workspace_id"]) as (directory, fd):
                self.repository.record(directory, call.project_id, require_ready=WorkspaceRepository.REQUIRE_READY)
                files = self.repository.file_tools(directory)
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
        if name == "test_workspace_execute" and isolation == ToolIsolation.SANDBOX:
            if shutil.which("bwrap") is None:
                raise ExecutionLimitError(RunErrorCode.SANDBOX_UNAVAILABLE, "Linux Bubblewrap is required")
            return
        if self.fallback is None or not hasattr(self.fallback, "validate_tool"):
            raise ExecutionLimitError(RunErrorCode.TOOL_CONTRACT, "Runner does not satisfy Tool isolation requirements")
        self.fallback.validate_tool(name, isolation)

    async def __call__(self, tool, call):
        if tool.name == "test_workspace_execute":
            if not isinstance(tool.handler, TestWorkspaces):
                raise ExecutionLimitError(RunErrorCode.TOOL_CONTRACT, "Sandbox Tool requires a trusted TestWorkspaces handler")
            return await tool.handler(call.arguments)
        if self.fallback is not None:
            return await self.fallback(tool, call)
        if tool.contract.isolation != ToolIsolation.NONE:
            raise ExecutionLimitError(RunErrorCode.TOOL_CONTRACT, "Tool requires another isolation runner")
        return await tool.handler(call.arguments)
