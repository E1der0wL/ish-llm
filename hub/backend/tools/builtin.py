"""Host lifecycle and Project selection for llm's built-in Tool collection."""

from llm.components.tools.builtin.workspace_values import WorkspaceExecuteArguments
from llm.services.infrastructure.linux_sandbox import LinuxSandbox

from llm.components.processing import COMPLETION_PROCESSORS

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from threading import Lock
import sys

from llm.components.tools import ToolRegistry
from llm.components.tools.builtin import BuiltinTools, BuiltinToolComponent
from .test_workspaces import HubTestWorkspaces
from .builtin_values import HubEnvironmentBinding, HubRegistryBinding, ResolvedToolkit
from llm.components.tools.builtin.process_values import (
    ShellArguments,
    ProcessInvocation,
    ProcessStartArguments,
    ProcessWriteArguments,
    ProcessReferenceArguments,
    ProcessOutputArguments,
)
from llm.core.agents import AgentDefinition

from ..interactions.questions import QuestionBroker
from ..interactions.question_values import QuestionTool


class HubBuiltinTools(BuiltinToolComponent):
    ABSENT = None
    SANDBOX_NETWORK = False
    COMMANDS_ENABLED = True
    GIT_ENABLED = True
    DIAGNOSTICS_ENABLED = True
    SHELL_PREFIX = ("/bin/sh", "-c")
    ENVIRONMENT_UNSUPPORTED = "Update the llm plugin before using ish shell context: process tools must support env"
    NAME = "builtin_tools"
    capabilities = (ToolRegistry.CAPABILITY, COMPLETION_PROCESSORS)

    def __init__(self, file_root=ABSENT, *, test_root=ABSENT):
        file_root = str(file_root) if isinstance(file_root, str) else file_root
        self.file_root = (
            Path(file_root).expanduser().absolute()
            if file_root is not HubBuiltinTools.ABSENT
            else HubBuiltinTools.ABSENT
        )
        self.test_root = (
            Path(test_root).expanduser().absolute()
            if test_root is not HubBuiltinTools.ABSENT
            else HubBuiltinTools.ABSENT
        )
        self.questions = QuestionBroker()
        self.toolkits = {}
        self._resolved = {}
        self._toolkit_lock = Lock()
        # Schema discovery needs an existing host directory, not a Project.
        super().__init__(
            self._create(
                Path.cwd()
                if self.file_root is HubBuiltinTools.ABSENT
                else self.file_root
            ),
            name=self.NAME,
        )

    def _create(self, root, environment=ABSENT):
        # Hub 제품 preset: 네트워크 차단, 명시적으로 선택한 파일만 복사.
        # Python 설치 경로는 실행에 필요한 read-only runtime이며 사용자 home 전체를 노출하지 않는다.
        spaces = (
            HubBuiltinTools.ABSENT
            if self.test_root is HubBuiltinTools.ABSENT
            else HubTestWorkspaces(
                self.test_root,
                source_root=root,
                allow_network=HubBuiltinTools.SANDBOX_NETWORK,
                env=environment,
                read_only_paths=tuple(dict.fromkeys((sys.base_prefix, sys.prefix))),
            )
        )
        toolkit = BuiltinTools(
            root,
            allow_commands=HubBuiltinTools.COMMANDS_ENABLED,
            shell=list(HubBuiltinTools.SHELL_PREFIX),
            git=HubBuiltinTools.GIT_ENABLED,
            diagnostics=HubBuiltinTools.DIAGNOSTICS_ENABLED,
            test_workspaces=spaces,
        )

        # Own the shell prefix explicitly. The builtin catalog also enables Git;
        # its shell/Git closures must not share a subsequently reassigned prefix.
        async def shell_execute(args):
            if toolkit.closed:
                raise RuntimeError(BuiltinTools.CLOSED)
            return await toolkit.processes.execute(
                ShellArguments.forward(args, HubBuiltinTools.SHELL_PREFIX)
            )

        handlers = {ShellArguments.NAME: shell_execute}
        if environment is not HubBuiltinTools.ABSENT:
            execution_tools = (
                (
                    ProcessStartArguments.NAME,
                    ShellArguments.NAME,
                    WorkspaceExecuteArguments.NAME,
                )
                if spaces
                else (ProcessStartArguments.NAME, ShellArguments.NAME)
            )
            if any(
                not ProcessInvocation.accepts_environment(
                    toolkit.registry.get(name).parameters
                )
                for name in execution_tools
            ):
                raise RuntimeError(HubBuiltinTools.ENVIRONMENT_UNSUPPORTED)
            from .processes import ShellProcessTools

            toolkit.processes = ShellProcessTools(toolkit.files, environment)
            methods = {
                ProcessStartArguments.NAME: ShellProcessTools.start.__name__,
                ProcessWriteArguments.NAME: ShellProcessTools.write.__name__,
                ProcessReferenceArguments.STATUS_NAME: ShellProcessTools.status.__name__,
                ProcessReferenceArguments.CANCEL_NAME: ShellProcessTools.cancel.__name__,
                ProcessOutputArguments.NAME: ShellProcessTools.output.__name__,
            }

            def process_handler(method):
                async def invoke(args):
                    if toolkit.closed:
                        raise RuntimeError(BuiltinTools.CLOSED)
                    return await getattr(toolkit.processes, method)(args)

                return invoke

            handlers.update(
                {name: process_handler(method) for name, method in methods.items()}
            )
        toolkit.registry = ToolRegistry(
            tuple(
                replace(tool, handler=handlers[name]) if name in handlers else tool
                for name in toolkit.registry.names()
                for tool in (toolkit.registry.get(name),)
            )
        )
        toolkit.registry.register(QuestionTool.create(self.questions.ask))
        from .paths import bind_paths

        bind_paths(toolkit)
        from .advice import bind_advice

        bind_advice(toolkit)
        return toolkit

    def resolve_environment(self, project, environment):
        """Immutable per-environment bindings preserve concurrent/queued Runs."""
        from llm.services.infrastructure.storage import revision_token

        identity = revision_token(environment.to_dict())
        key = (project.id, identity)
        with self._toolkit_lock:
            if key not in self.toolkits:
                self.toolkits[key] = self._create(
                    environment.cwd, dict(environment.variables)
                )
            toolkit = self.toolkits[key]
        registry = BuiltinToolComponent(toolkit, name=self.name).resolve(
            project, ToolRegistry.CAPABILITY
        )
        # All defaults that can alter effects participate in approval binding.
        return ToolRegistry(
            tuple(
                replace(
                    tool,
                    contract=replace(
                        tool.contract,
                        revision=revision_token(
                            HubEnvironmentBinding(
                                tool.contract.revision, identity
                            ).to_dict()
                        ),
                    ),
                )
                for name in registry.names()
                for tool in (registry.get(name),)
            )
        )

    def for_project(self, project):
        key = project.id
        with self._toolkit_lock:
            if key not in self.toolkits:
                self.toolkits[key] = self._create(
                    project.paths.root
                    if self.file_root is HubBuiltinTools.ABSENT
                    else self.file_root
                )
            return self.toolkits[key]

    def select_tools(self):
        """새 Project 생성 초기값. 이미 명시한 선택은 호출자가 보존한다."""
        # 선택된 Tool은 Run 시작 전에 검증된다. 사용할 수 없는 선택적 sandbox가
        # 일반 대화까지 막지 않도록 최초 preset에서만 설치 여부를 반영한다.
        return [
            name
            for name in self.toolkit.registry.names()
            if name != WorkspaceExecuteArguments.NAME
            or LinuxSandbox.locate() is not HubBuiltinTools.ABSENT
        ]

    def resolve(self, project, capability):
        if capability == COMPLETION_PROCESSORS:
            from ..execution.completion import FollowupProcessor

            return FollowupProcessor()
        if capability != ToolRegistry.CAPABILITY:
            return super().resolve(project, capability)
        toolkit = self.for_project(project)
        if toolkit.closed:
            raise RuntimeError(BuiltinTools.CLOSED)
        from llm.services.infrastructure.storage import revision_token

        # Cache only our host-owned built-ins. Imported Tool packages still use
        # llm's live resolver so source changes and contracts cannot go stale.
        key = revision_token(
            HubRegistryBinding(
                self._options(project),
                [tool.to_dict() for tool in toolkit.registry.definitions()],
                toolkit.registry.contracts(),
            ).to_dict()
        )
        with self._toolkit_lock:
            cached = self._resolved.get(project.id)
            if (
                cached is HubBuiltinTools.ABSENT
                or ResolvedToolkit.revision(cached) != key
            ):
                registry = BuiltinToolComponent(toolkit, name=self.name).resolve(
                    project, capability
                )
                self._resolved[project.id] = ResolvedToolkit.create(key, registry)
            else:
                registry = ResolvedToolkit.registry(cached)
            # Isolate mutable schemas/registrations while preserving runtime
            # handler identity (question broker, process registry, file root).
            handlers = [registry.get(name).handler for name in registry.names()]
            return deepcopy(registry, {id(handler): handler for handler in handlers})

    def resolve_runtime(self, project, capability, *, data_factory):
        registry = self.resolve(project, capability)
        if capability == ToolRegistry.CAPABILITY:
            from llm.core.agents import AgentDefinition
            from .resources import authoring_tools

            for item in authoring_tools(
                data_factory(self.name).agents(),
                AgentDefinition.REFERENCE_COMPONENT,
                AgentDefinition.schema,
            ):
                registry.register(item)
        return registry

    async def close(self):
        self.questions.close()
        self._resolved.clear()
        for toolkit in (self.toolkit, *self.toolkits.values()):
            await toolkit.close()
