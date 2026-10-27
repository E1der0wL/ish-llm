"""명시적으로 생성·삭제하는 Project별 테스트 파일 공간과 Linux 샌드박스 실행.

Run/Step·승인·재시도는 ToolExecutor가 소유한다. 이 모듈의 record는 파일 공간의
소유권과 생성 완료 여부만 기록하며 실행 기록이나 재생 큐를 만들지 않는다.
"""

from llm.core.models import RunErrorCode

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Mapping, Sequence

from llm._platform import require_linux
from llm.services.infrastructure.linux_sandbox import LinuxSandbox
from llm.services.infrastructure.storage import drain_on_cancel
from llm.policies import ExecutionLimitError
from llm.services.runtime.tools import current_tool_call
from llm.core.tool_contracts import ToolIsolation
from .workspace_records import WorkspaceRecord, WorkspaceIdentity
from .workspace_repository import WorkspaceRepository
from .workspace_values import (WorkspaceSettings, WorkspaceRuntime, WorkspaceCreateArguments, WorkspaceReference,
    WorkspaceExecuteArguments, WorkspaceExecutionResult)


class TestWorkspaces:
    """호스트가 주입하는 파일 자원. 모델은 경로·mount·network 권한을 선택하지 않는다.

    source_root에서 명시한 파일만 복사한다. root는 소유권 기록/공간을 보관하는 전용
    디렉터리이며 다른 관리 파일을 넣지 않는다. allow_network는 반드시 호스트가 정한다.
    """

    STORAGE_VERSION = WorkspaceRecord.VERSION
    NETWORK_REQUIRED = "allow_network must be explicitly boolean"
    HOST_ROOT = Path("/")
    STRICT_PATHS = True
    EXPOSED_MOUNT = "Read-only mounts cannot expose test storage or the source root"
    CLOSED = "TestWorkspaces is closed"
    OWNER_REQUIRED = "Test workspace Tools require an owning Project Tool invocation"
    OPEN_STATE = False
    CLOSED_STATE = True
    COLLECT_CLOSE_ERRORS = True

    def __init__(self, root: str | Path, *, source_root: str | Path, allow_network: bool,
                 read_only_paths: Sequence[str | Path] = WorkspaceSettings.NO_MOUNTS,
                 env: Mapping[str, str] | None = WorkspaceSettings.UNCONFIGURED,
                 max_output_bytes: int | None = WorkspaceSettings.UNCONFIGURED,
                 memory_bytes: int | None = WorkspaceSettings.UNCONFIGURED,
                 python_executable: str | Path | None = WorkspaceSettings.UNCONFIGURED):
        require_linux()
        if type(allow_network) is not bool:
            raise ValueError(self.NETWORK_REQUIRED)
        self.repository = WorkspaceRepository(root, source_root)
        mounts = tuple(Path(path).resolve(strict=self.STRICT_PATHS) for path in read_only_paths)
        for mount in mounts:
            if (mount == self.HOST_ROOT or mount in (self.root, *self.root.parents)
                    or mount in (self.source.root, *self.source.root.parents)):
                raise ValueError(self.EXPOSED_MOUNT)
        self._runtime = WorkspaceRuntime(allow_network=allow_network, read_only_paths=mounts,
            env=deepcopy(env), max_output_bytes=max_output_bytes, memory_bytes=memory_bytes,
            python_executable=python_executable)
        # 생성 시 호스트 옵션을 검증하되 bwrap/명령 프로세스는 시작하지 않는다.
        validated = self._runtime.runner(self.source.root)
        self._runtime.python_executable = validated.python
        self._binding = self._runtime.binding(self.root, self.source.root)
        self._active = set()
        self._closed = self.OPEN_STATE

    @property
    def execution_binding(self):
        return self._binding.to_dict()

    def binding(self):
        return deepcopy(self._binding)

    @property
    def root(self):
        return self.repository.root

    @property
    def source(self):
        return self.repository.source

    def _project(self):
        if self._closed or getattr(self, "_output_owner", self)._closed:
            raise RuntimeError(self.CLOSED)
        call = current_tool_call()
        if call is None or not call.project_id or not WorkspaceIdentity.valid_project(call.project_id):
            raise ValueError(self.OWNER_REQUIRED)
        return call


    async def create(self, args: dict) -> dict:
        """빈 공간 또는 명시한 파일의 독립 사본을 만든다. 취소 시 승인된 생성의 회수를 기다린다."""
        call = self._project()
        request = WorkspaceCreateArguments.read(args)
        return (await drain_on_cancel(asyncio.to_thread(self.repository.create, call, request.source_paths))).to_dict()

    async def list(self, args: dict) -> dict:
        """재시작 후에도 해당 Project의 공간을 조회한다. 명령을 재생하지 않는다."""
        return (await asyncio.to_thread(self.repository.list, self._project().project_id)).to_dict()

    async def delete(self, args: dict) -> dict:
        """사용 중이지 않은 공간을 명시적으로 삭제한다. 원본에는 접근하지 않는다."""
        project_id = self._project().project_id
        request = WorkspaceReference.read(args)
        return (await drain_on_cancel(asyncio.to_thread(self.repository.delete, project_id, request.workspace_id))).to_dict()

    async def file_call(self, operation: str, args: dict) -> dict:
        """기존 파일 Tool 계약을 선택 공간에 적용한다. 실행 중인 공간에는 접근하지 않는다."""
        project_id = self._project().project_id
        request = WorkspaceReference.read(args)
        return await drain_on_cancel(asyncio.to_thread(self.repository.files, project_id,
            request.workspace_id, operation, args))

    async def __call__(self, args: dict) -> dict:
        """ToolExecutor가 승인한 argv만 실행한다. 완료/취소 후에도 파일 공간은 유지한다."""
        call = self._project()
        request = WorkspaceExecuteArguments.read(args)
        task = asyncio.current_task()
        self._active.add(task)
        try:
            # nonblocking flock: 다른 프로세스/Tool의 삭제·파일 변경과 실행을 직렬화한다.
            # 잠금의 작은 메타데이터 I/O만 동기, 복사/조회/삭제는 위의 off-loop 경로다.
            with self.repository.lease(call.project_id, request.workspace_id) as (directory, fd):
                self.repository.record(directory, call.project_id, require_ready=WorkspaceRepository.REQUIRE_READY)
                files = self.repository.file_tools(directory)
                # Tool 인자의 env는 승인된 전체 환경이다. 호스트 환경과 암묵적으로 병합하지 않는다.
                runtime = self._runtime.with_environment(request.env)
                runner = runtime.runner(files.root)
                store = getattr(self, "_output_store", None)
                if store is not None:
                    from datetime import datetime, timezone
                    started_at = datetime.now(timezone.utc).isoformat()
                    async def save(code, stdout, stderr):
                        return await drain_on_cancel(store.command_result(request.argv, files.root, code, stdout, stderr,
                                                                          started_at=started_at))
                    result = await runner.execute(request.argv, pass_fds=(fd,), output_sink=save)
                    return {**result, "workspace_id": request.workspace_id}
                result = await runner.execute(request.argv, pass_fds=(fd,))
                return WorkspaceExecutionResult.from_result(request.workspace_id, result).to_dict()
        finally:
            self._active.discard(task)

    def with_output(self, store):
        from copy import copy
        bound = copy(self)
        bound._output_store = store
        bound._output_owner = self
        return bound

    async def close(self) -> None:
        """실행만 회수한다. 영속 파일은 명시적 delete까지 보관한다."""
        self._closed = self.CLOSED_STATE
        pending = tuple(self._active)
        for task in pending:
            task.cancel()
        await drain_on_cancel(asyncio.gather(*pending, return_exceptions=self.COLLECT_CLOSE_ERRORS))


class BuiltinToolRunner:
    """테스트 공간 실행만 sandbox handler로 전달하고 다른 Tool은 기존 경로로 실행한다."""

    SANDBOX_REQUIRED = "Linux Bubblewrap is required"
    ISOLATION_REQUIRED = "Runner does not satisfy Tool isolation requirements"
    TRUSTED_HANDLER_REQUIRED = "Sandbox Tool requires a trusted TestWorkspaces handler"
    OTHER_RUNNER_REQUIRED = "Tool requires another isolation runner"

    def __init__(self, fallback=None):
        self.fallback = fallback

    def validate_tool(self, name, isolation):
        if name == WorkspaceExecuteArguments.NAME and isolation == ToolIsolation.SANDBOX:
            if LinuxSandbox.locate() is None:
                raise ExecutionLimitError(RunErrorCode.SANDBOX_UNAVAILABLE, self.SANDBOX_REQUIRED)
            return
        if self.fallback is None or not hasattr(self.fallback, BuiltinToolRunner.validate_tool.__name__):
            raise ExecutionLimitError(RunErrorCode.TOOL_CONTRACT, self.ISOLATION_REQUIRED)
        self.fallback.validate_tool(name, isolation)

    async def __call__(self, tool, call):
        if tool.name == WorkspaceExecuteArguments.NAME:
            if not isinstance(tool.handler, TestWorkspaces):
                raise ExecutionLimitError(RunErrorCode.TOOL_CONTRACT, self.TRUSTED_HANDLER_REQUIRED)
            return await tool.handler(call.arguments)
        if self.fallback is not None:
            return await self.fallback(tool, call)
        if tool.contract.isolation != ToolIsolation.NONE:
            raise ExecutionLimitError(RunErrorCode.TOOL_CONTRACT, self.OTHER_RUNNER_REQUIRED)
        return await tool.handler(call.arguments)
