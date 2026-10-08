"""Host lifecycle and Project selection for llm's built-in Tool collection."""

import asyncio
from copy import deepcopy
from pathlib import Path
from threading import Lock

from llm.components.tools import Tool, ToolContract
from llm.components.tools.builtin import BuiltinTools, BuiltinToolComponent

from .questions import QuestionBroker


class HubBuiltinTools(BuiltinToolComponent):
    def __init__(self, file_root=None):
        self.file_root = Path(file_root).expanduser().absolute() if file_root else None
        self.questions = QuestionBroker()
        self.toolkits = {}
        self._resolved = {}
        self._toolkit_lock = Lock()
        # Schema discovery needs an existing host directory, not a Project.
        super().__init__(self._create(self.file_root or Path.cwd()), name="builtin_tools")

    def _create(self, root):
        toolkit = BuiltinTools(root, allow_commands=True, shell=["/bin/sh", "-c"], git=True, diagnostics=True)
        toolkit.registry.register(Tool("ask_user",
            "Ask the user a necessary question and wait for their answer, then continue the SAME task. "
            "Use this instead of ending a response with a question when work needs user input. "
            "Choices are optional suggestions; free-text answers are always accepted.",
            {"type": "object", "properties": {"question": {"type": "string", "minLength": 1},
                "description": {"type": "string"}, "choices": {"type": "array", "items": {"type": "string"}}},
             "required": ["question"], "additionalProperties": False},
            self.questions.ask, contract=ToolContract(effect="read_only")))
        return toolkit

    def for_project(self, project):
        key = project.id
        with self._toolkit_lock:
            if key not in self.toolkits:
                self.toolkits[key] = self._create(self.file_root or project.paths.root)
            return self.toolkits[key]

    def resolve(self, project, capability):
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
        settings.setdefault("enabled", list(self.toolkit.registry.names()))
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
