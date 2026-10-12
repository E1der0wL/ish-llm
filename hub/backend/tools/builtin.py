"""Host lifecycle and Project selection for llm's built-in Tool collection."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from threading import Lock
import sys
import shutil

from llm.components.tools import Tool, ToolContract, ToolRegistry
from llm.components.tools.builtin import BuiltinTools, BuiltinToolComponent
from .test_workspaces import HubTestWorkspaces

from ..interactions.questions import QuestionBroker


class HubBuiltinTools(BuiltinToolComponent):
    capabilities = ("tools", "completion_processors")

    def __init__(self, file_root=None, *, test_root=None):
        self.file_root = Path(file_root).expanduser().absolute() if file_root else None
        self.test_root = Path(test_root).expanduser().absolute() if test_root is not None else None
        self.questions = QuestionBroker()
        self.toolkits = {}
        self._resolved = {}
        self._toolkit_lock = Lock()
        # Schema discovery needs an existing host directory, not a Project.
        super().__init__(self._create(self.file_root or Path.cwd()), name="builtin_tools")

    def _create(self, root, environment=None):
        # Hub 제품 preset: 네트워크 차단, 명시적으로 선택한 파일만 복사.
        # Python 설치 경로는 실행에 필요한 read-only runtime이며 사용자 home 전체를 노출하지 않는다.
        spaces = None if self.test_root is None else HubTestWorkspaces(self.test_root, source_root=root,
            allow_network=False, env=environment,
            read_only_paths=tuple(dict.fromkeys((sys.base_prefix, sys.prefix))))
        toolkit = BuiltinTools(root, allow_commands=True, shell=["/bin/sh", "-c"], git=True, diagnostics=True,
                               test_workspaces=spaces)
        # Own the shell prefix explicitly. The builtin catalog also enables Git;
        # its shell/Git closures must not share a subsequently reassigned prefix.
        async def shell_execute(args):
            if toolkit.closed:
                raise RuntimeError("BuiltinTools is closed")
            return await toolkit.processes.execute({"argv": ["/bin/sh", "-c", args["command"]],
                **{key: value for key, value in args.items() if key != "command"}})
        handlers = {"shell_execute": shell_execute}
        if environment is not None:
            execution_tools = ("process_start", "shell_execute", "test_workspace_execute") if spaces else (
                "process_start", "shell_execute")
            if any("env" not in toolkit.registry.get(name).parameters.get("properties", {})
                   for name in execution_tools):
                raise RuntimeError("Update the llm plugin before using ish shell context: process tools must support env")
            from .processes import ShellProcessTools
            toolkit.processes = ShellProcessTools(toolkit.files, environment)
            methods = {"process_start": "start", "process_write": "write", "process_status": "status",
                       "process_cancel": "cancel", "process_output": "output"}
            def process_handler(method):
                async def invoke(args):
                    if toolkit.closed:
                        raise RuntimeError("BuiltinTools is closed")
                    return await getattr(toolkit.processes, method)(args)
                return invoke
            handlers.update({name: process_handler(method) for name, method in methods.items()})
        toolkit.registry = ToolRegistry(tuple(
            replace(tool, handler=handlers[name]) if name in handlers else tool
            for name in toolkit.registry.names() for tool in (toolkit.registry.get(name),)))
        toolkit.registry.register(Tool("ask_user",
            "Ask the user a necessary question and wait for their answer, then continue the SAME task. "
            "Use this instead of ending a response with a question when work needs user input. "
            "Choices are optional suggestions; free-text answers are always accepted.",
            {"type": "object", "properties": {"question": {"type": "string", "minLength": 1},
                "description": {"type": "string"}, "choices": {"type": "array", "items": {"type": "string"}}},
             "required": ["question"], "additionalProperties": False},
            self.questions.ask, contract=ToolContract(effect="read_only")))
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
                self.toolkits[key] = self._create(environment.cwd, dict(environment.variables))
            toolkit = self.toolkits[key]
        registry = BuiltinToolComponent(toolkit, name=self.name).resolve(project, "tools")
        # All defaults that can alter effects participate in approval binding.
        return ToolRegistry(tuple(replace(tool, contract=replace(tool.contract,
            revision=revision_token({"contract": tool.contract.revision, "shell": identity})))
            for name in registry.names() for tool in (registry.get(name),)))

    def for_project(self, project):
        key = project.id
        with self._toolkit_lock:
            if key not in self.toolkits:
                self.toolkits[key] = self._create(self.file_root or project.paths.root)
            return self.toolkits[key]

    def select_tools(self):
        """새 Project/최초 연결의 Hub 선택값. 이미 명시한 선택은 호출자가 보존한다."""
        # 선택된 Tool은 Run 시작 전에 검증된다. 사용할 수 없는 선택적 sandbox가
        # 일반 대화까지 막지 않도록 최초 preset에서만 설치 여부를 반영한다.
        return [name for name in self.toolkit.registry.names()
                if name != "test_workspace_execute" or shutil.which("bwrap") is not None]

    def resolve(self, project, capability):
        if capability == "completion_processors":
            from ..execution.completion import FollowupProcessor
            return FollowupProcessor()
        if capability != "tools":
            return super().resolve(project, capability)
        toolkit = self.for_project(project)
        if toolkit.closed:
            raise RuntimeError("BuiltinTools is closed")
        from llm.services.infrastructure.storage import revision_token
        # Cache only our host-owned built-ins. Imported Tool packages still use
        # llm's live resolver so source changes and contracts cannot go stale.
        key = revision_token({"options": self._options(project),
            "definitions": toolkit.registry.definitions(), "contracts": toolkit.registry.contracts()})
        with self._toolkit_lock:
            cached = self._resolved.get(project.id)
            if cached is None or cached[0] != key:
                registry = BuiltinToolComponent(toolkit, name=self.name).resolve(project, capability)
                self._resolved[project.id] = (key, registry)
            else:
                registry = cached[1]
            # Isolate mutable schemas/registrations while preserving runtime
            # handler identity (question broker, process registry, file root).
            handlers = [registry.get(name).handler for name in registry.names()]
            return deepcopy(registry, {id(handler): handler for handler in handlers})

    async def ensure_selected(self, project):
        record = await project.aget_data()
        config = record.config.to_dict()
        settings = config.setdefault("parameters", {}).setdefault("components", {}).setdefault(self.name, {}).setdefault("config", {})
        selected = list(record.components)
        changed = self.name not in selected or "enabled" not in settings
        if self.name not in selected:
            selected.append(self.name)
        settings.setdefault("enabled", self.select_tools())
        if changed:
            from llm.services.infrastructure.storage import revision_token
            await project.asave(config=config, components=selected, expected_components=list(record.components),
                                expected_version=revision_token(record.config.to_dict()))
        # Build and validate the first snapshot on project opening, off-loop,
        # before the user submits a request. Selection edits invalidate the key.
        await asyncio.to_thread(self.resolve, await project.aget_data(), "tools")

    async def close(self):
        self.questions.close()
        self._resolved.clear()
        for toolkit in (self.toolkit, *self.toolkits.values()):
            await toolkit.close()
