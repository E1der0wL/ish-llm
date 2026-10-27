"""기본 Tool Schema와 구현을 조립한다. 외부 서비스 Tool은 주입된 어댑터만 노출한다."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from llm.core.tool_contracts import ToolContract
from llm.components.tools.registry import Tool, ToolRegistry
from llm.services.infrastructure.storage import drain_on_cancel
from .files import FileTools
from .file_values import (FileReadArguments, FileCreateArguments, FilePatchArguments,
    FileMoveArguments, FileDeleteArguments, FileRestoreArguments)
from .processes import ProcessTools
from .process_values import (ProcessStartArguments, ProcessWriteArguments, ProcessReferenceArguments,
    ProcessOutputArguments, ShellArguments, CheckArguments)
from .diagnostics import DiagnosticTools
from .workspaces import TestWorkspaces
from .workspace_values import (WorkspaceSelection, WorkspaceCreateArguments, WorkspaceListArguments,
    WorkspaceDeleteArguments, WorkspaceExecuteArguments)
from .catalog_values import (BuiltinExecutionBinding, ConfiguredChecks, GitCommands, GitStatusArguments,
    GitDiffArguments, PromptReadArguments, HostAdapters)
from llm.core.tool_contracts import ToolEffect, ToolIsolation


class BuiltinTools:
    """파일 Tool과 선택적 실행/서비스 어댑터를 소유하는 async 수명 관리 객체.

    registry는 호스트가 정의한 Component의 tools capability로 제공한다.
    ToolComponent는 Project Python 소스만 담당하며 이 런타임 객체를 받지 않는다.
    allow_commands는 임의의 호스트 코드 실행을 허용한다. 경로 제한형 샌드박스가 아니다.
    """

    READ_CONTRACT = ToolContract(
        revision="1", effect=ToolEffect.READ_ONLY, isolation=ToolIsolation.NONE,
        approval_required=False, operation_key_required=False, retry_fields=())
    MUTATION_CONTRACT = ToolContract(
        revision="1", effect=ToolEffect.EXTERNAL, isolation=ToolIsolation.NONE,
        approval_required=True, operation_key_required=False, retry_fields=())
    EXECUTION_CONTRACT = ToolContract(
        revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE,
        approval_required=True, operation_key_required=False, retry_fields=())
    SANDBOX_CONTRACT = ToolContract(
        revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.SANDBOX,
        approval_required=True, operation_key_required=False, retry_fields=())
    # External query adapters are explicitly supplied by the trusted Host.
    # Their opaque effects are not a proof of no effect or permission to retry.
    QUERY_CONTRACT = ToolContract(
        revision="1", effect=ToolEffect.UNKNOWN, isolation=ToolIsolation.NONE,
        approval_required=False, operation_key_required=False, retry_fields=())

    FILE_MUTATIONS = frozenset((FileCreateArguments.NAME, FilePatchArguments.NAME, FileMoveArguments.NAME,
                               FileDeleteArguments.NAME, FileRestoreArguments.NAME))
    FILE_READ_CONTRACT = replace(READ_CONTRACT, retry_fields=(FileReadArguments.path.__name__,))

    INVALID_SWITCHES = "Tool feature switches must be booleans"
    SOURCE_MISMATCH = "Test workspace source must match the builtin file root"
    INVALID_SHELL = "shell must be a nonempty argv prefix"
    CLOSED = "BuiltinTools is closed"
    OPEN_STATE = False
    CLOSED_STATE = True
    FEATURE_DISABLED = False

    def __init__(self, root, *, allow_commands: bool = FEATURE_DISABLED, git: bool = FEATURE_DISABLED,
                 diagnostics: bool = FEATURE_DISABLED,
                 checks: Optional[Mapping[str, Sequence[str]]] = None,
                 adapters: Optional[Mapping[str, Callable]] = None,
                 shell: Optional[Sequence[str]] = None,
                 test_workspaces: Optional[TestWorkspaces] = None,
                 read_access=None, write_access=None, cwd_access=None):
        if any(type(value) is not bool for value in (allow_commands, git, diagnostics)):
            raise TypeError(self.INVALID_SWITCHES)
        self.files = FileTools(Path(root), read_access=read_access, write_access=write_access, cwd_access=cwd_access)
        self.processes = ProcessTools(self.files)
        self.registry = ToolRegistry()
        self.closed = self.OPEN_STATE
        self.test_workspaces = test_workspaces
        if test_workspaces is not None and test_workspaces.source.root != self.files.root:
            raise ValueError(self.SOURCE_MISMATCH)
        # 재개 binding에 필요한 호스트 실행 환경. client/함수나 숨은 사용자 설정을
        # 저장하지 않는다. 외부 어댑터의 내부 상태는 여전히 해당 호스트가 관리한다.
        self._execution_binding = BuiltinExecutionBinding(str(self.files.root),
            list(shell) if shell is not None else None, deepcopy(dict(checks or {})),
            test_workspaces.binding() if test_workspaces is not None else None,
            self.files.read_access.to_dict(), self.files.write_access.to_dict(), self.files.cwd_access.to_dict())
        # 효과를 낼 수 있는 Tool은 항상 결정이 필요하다. 자동 응답의 위험 기준은
        # Project가 소유하며, 이 계약은 사람의 승인만을 강제하는 정책이 아니다.
        mutation = self.MUTATION_CONTRACT
        execution = self.EXECUTION_CONTRACT
        for declaration in FileTools.ARGUMENTS:
            name, parameters = declaration.NAME, declaration.schema()
            if test_workspaces is not None:
                parameters = WorkspaceSelection.add_to_file_schema(parameters)
            file_call = self.file_handler(name, self.files)
            self._register(name, declaration.DESCRIPTION, parameters, file_call,
                           contract=mutation if name in self.FILE_MUTATIONS else
                           self.FILE_READ_CONTRACT if name == FileReadArguments.NAME else self.READ_CONTRACT)

        if test_workspaces is not None:
            for declaration, handler, contract in (
                    (WorkspaceCreateArguments, test_workspaces.create, mutation),
                    (WorkspaceListArguments, test_workspaces.list, self.READ_CONTRACT),
                    (WorkspaceDeleteArguments, test_workspaces.delete, mutation)):
                self._register(declaration.NAME, declaration.DESCRIPTION, declaration.schema(), handler,
                               contract=contract)
            # The sandbox runner verifies the trusted handler and its Host binding.
            self.registry.register(Tool(WorkspaceExecuteArguments.NAME, WorkspaceExecuteArguments.DESCRIPTION,
                WorkspaceExecuteArguments.schema(), test_workspaces, contract=self.SANDBOX_CONTRACT))

        commands = ConfiguredChecks.read(checks).commands
        if commands:
            async def check(args):
                return await self.processes.execute(CheckArguments.read(args).invocation(commands).supplied())
            for name in CheckArguments.NAMES:
                self._register(name, CheckArguments.DESCRIPTION, CheckArguments.choices_schema(commands), check,
                               contract=execution)
        if allow_commands:
            self._register(ProcessStartArguments.NAME, ProcessStartArguments.DESCRIPTION,
                           ProcessStartArguments.schema(), self.processes.start, contract=execution)
            self._register(ProcessWriteArguments.NAME, ProcessWriteArguments.DESCRIPTION,
                           ProcessWriteArguments.schema(), self.processes.write, contract=execution)
            shell_prefix = list(shell) if shell is not None else []
            if isinstance(shell, str) or not shell_prefix or any(not isinstance(item, str) or not item for item in shell_prefix):
                raise ValueError(self.INVALID_SHELL)
            async def execute_shell(args):
                return await self.processes.execute(ShellArguments.read(args).invocation(shell_prefix).supplied())
            self._register(ShellArguments.NAME, ShellArguments.DESCRIPTION, ShellArguments.schema(), execute_shell,
                           contract=execution)
        if allow_commands or commands or git:
            for name, method in ((ProcessReferenceArguments.STATUS_NAME, self.processes.status),
                                 (ProcessReferenceArguments.CANCEL_NAME, self.processes.cancel)):
                self._register(name, ProcessReferenceArguments.DESCRIPTION, ProcessReferenceArguments.schema(), method,
                               contract=mutation if name == ProcessReferenceArguments.CANCEL_NAME else self.READ_CONTRACT)
            self._register(ProcessOutputArguments.NAME, ProcessOutputArguments.DESCRIPTION,
                           ProcessOutputArguments.schema(), self.processes.output, contract=self.READ_CONTRACT)
        if diagnostics:
            inspector = DiagnosticTools(self.files.root)
            for declaration, operation in inspector.declarations():
                async def inspect(args, operation=operation):
                    return await asyncio.to_thread(operation, args)
                self._register(declaration.NAME, declaration.DESCRIPTION, declaration.schema(), inspect,
                               contract=self.READ_CONTRACT)
        if git:
            async def status(args):
                return await self.processes.execute(GitCommands.status().supplied())
            async def diff(args):
                return await self.processes.execute(GitCommands.diff(GitDiffArguments.read(args)).supplied())
            self._register(GitStatusArguments.NAME, GitStatusArguments.DESCRIPTION,
                           GitStatusArguments.schema(), status, contract=self.READ_CONTRACT)
            self._register(GitDiffArguments.NAME, GitDiffArguments.DESCRIPTION,
                           GitDiffArguments.schema(), diff, contract=self.READ_CONTRACT)

        for name, handler in (adapters or {}).items():
            spec = HostAdapters.selected(name, handler)
            self._register(name, HostAdapters.description(name), spec.parameters, handler,
                           contract=execution if spec.execution else self.QUERY_CONTRACT)

    def file_handler(self, operation, files):
        async def invoke(args):
            if self.closed:
                raise RuntimeError(self.CLOSED)
            if WorkspaceSelection.read(args).selected:
                return await self.test_workspaces.file_call(operation, args)
            from .path_access import current_path_access
            access = current_path_access()
            selected = files if access is None else files.with_access(access)
            return await drain_on_cancel(asyncio.to_thread(getattr(selected.for_arguments(args), operation), args))
        return invoke

    def binding_revision(self, contract, *, read_access=None, write_access=None, cwd_access=None):
        binding = self._execution_binding
        for name, value in (("read_access", read_access), ("write_access", write_access), ("cwd_access", cwd_access)):
            if value is not None:
                binding = replace(binding, **{name: deepcopy(value)})
        return binding.revision(contract)

    def _register(self, name, description, parameters, handler, *, contract):
        async def guarded(args):
            if self.closed:
                raise RuntimeError(self.CLOSED)
            return await handler(args)
        self.registry.register(Tool(name, description, parameters, guarded, contract=contract))

    def bind_prompts(self, agents) -> None:
        """조회만 공개한다. 영속 지침 변경은 별도의 Refinement 승인 경계를 사용한다."""
        async def read(args):
            return await agents.aprompt(PromptReadArguments.read(args).agent_id)
        self._register(PromptReadArguments.NAME, PromptReadArguments.DESCRIPTION, PromptReadArguments.schema(),
                       read, contract=self.READ_CONTRACT)

    async def close(self):
        if not self.closed:
            self.closed = self.CLOSED_STATE
            await drain_on_cancel(self.processes.close())
            if self.test_workspaces is not None:
                await drain_on_cancel(self.test_workspaces.close())

    async def __aenter__(self):
        if self.closed:
            raise RuntimeError(self.CLOSED)
        return self

    async def __aexit__(self, *exc):
        await self.close()
