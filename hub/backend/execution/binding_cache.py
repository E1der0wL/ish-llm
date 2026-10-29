"""One request's derived host Tool bindings, never authorization decisions."""

from dataclasses import dataclass

from llm.core.models import Project, Run
from llm.components.tools import ToolRegistry
from ..tools.validation import ToolInputRegistry


@dataclass(slots=True)
class BindingCache:
    key: object = None
    registry: object = None
    source: object = None

    def clear(self):
        self.key = self.registry = self.source = None

    def bind(self, context, environment, build):
        # Native/custom registries may own additional mutable admission hooks.
        # Only the standard registry and Hub's diagnostic adapter are reusable.
        if (not isinstance(context.project, Project) or not isinstance(context.run, Run)
                or type(context.tools) not in (ToolRegistry, ToolInputRegistry)):
            return build()
        source = context.tools
        key = (context.run.id, context.project.id, context.session.id,
               environment.to_dict() if environment is not None else None, tuple(context.project.components),
               context.project.config.to_dict(), source.execution_binding({}),
               tuple(id(source.get(name).handler) for name in source.names()))
        if self.key != key or self.source is not source:
            registry = build()  # Native registration checks all changed bindings.
            self.key, self.source, self.registry = key, source, registry
        # The binder and executor must not share a mutable selected registry.
        return ToolInputRegistry.detached(self.registry)
