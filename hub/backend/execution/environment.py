"""Bind host execution context without changing llm's Engine or Tool APIs."""

from llm.core.model_protocol import ModelMessage

from contextlib import aclosing
from copy import copy
from dataclasses import replace
import asyncio
import inspect

from llm.components.processing import CompletionMessage, CompletionSession
from llm.components.tools import ToolRegistry
from llm.services.infrastructure.storage import drain_on_cancel
from ...config.environment import ENVIRONMENT_OPTION, ShellEnvironment


class EnvironmentSession(CompletionSession):
    def __init__(self, cwd):
        self.cwd = cwd

    async def prepare(self, request):
        from ...asset.guides import WORK_GUIDANCE
        index = 1 if request.messages and request.messages[0].message.role == "system" else 0
        request.messages.insert(index, CompletionMessage(ModelMessage("system", "The working directory captured from the user's ish shell for this request is "
            f"{self.cwd!r}. Built-in file paths are relative to this directory. "
            "Process tools inherit the captured shell environment unless an explicit env is supplied. "
            "Sandbox commands still run inside their isolated test workspace.\n\n" + WORK_GUIDANCE)))
        if False:
            yield


class EnvironmentProcessor:
    name = "hub.environment"
    priority = 999
    close_timeout = None

    def __init__(self, cwd):
        self.cwd = cwd

    def session(self, context):
        return EnvironmentSession(self.cwd)


def underlying_engine(engine):
    return engine.implementation if isinstance(engine, EnvironmentEngine) else engine


class EnvironmentEngine:
    """Keep request-owned shell context separate from the selected Agent."""

    def __init__(self, implementation, builtins, environment=None, *, recovery=None, retry_monitor=None,
                 provider_recovery=None):
        self.implementation = implementation
        self.builtins = builtins
        self.environment = environment
        self.recovery = recovery
        self.retry_monitor = retry_monitor
        self.provider_recovery = provider_recovery

    def __getattr__(self, name):
        value = getattr(object.__getattribute__(self, "implementation"), name)
        if name == "validate_resume":
            def validate(checkpoint, **kwargs):
                if kwargs.get("context") is not None:
                    kwargs["context"] = self.bind_context(kwargs["context"])
                return value(checkpoint, **kwargs)
            return validate
        if name == "bind_execution":
            return lambda context: value(self.bind_context(context))
        if name == "with_instructions":
            def compose(*args, **kwargs):
                worker = copy(self)
                worker.implementation = value(*args, **kwargs)
                return worker
            return compose
        return value

    def for_agent(self, definition):
        from llm.engines.registry import EngineRegistry
        registry = EngineRegistry()
        registry.register(definition["engine"], self.implementation)
        worker = copy(self)
        worker.implementation = registry.resolve_agent(definition)
        return worker

    def for_context(self, request_context):
        options = dict(request_context)
        from .turns import TURN_OPTION
        turn = options.pop(TURN_OPTION, None)
        if turn is not None and (not isinstance(turn, dict) or set(turn) != {"source"}
                                 or not isinstance(turn["source"], str) or not turn["source"]):
            raise ValueError("Invalid Hub turn reference")
        environment = (ShellEnvironment.from_dict(options.pop(ENVIRONMENT_OPTION))
                       if ENVIRONMENT_OPTION in options else None)
        factory = getattr(self.implementation, "for_context", None)
        if callable(factory):
            implementation = factory(options)
        elif options:
            raise ValueError("Engine does not support these request context fields")
        else:
            implementation = self.implementation
        if inspect.isawaitable(implementation):
            if inspect.iscoroutine(implementation):
                implementation.close()
            raise TypeError("Engine for_context must be synchronous")
        if not callable(getattr(implementation, "execute", None)):
            raise TypeError("for_context must return an Engine")
        return EnvironmentEngine(implementation, self.builtins, environment, recovery=self.recovery,
                                 retry_monitor=self.retry_monitor, provider_recovery=self.provider_recovery)

    def bind_context(self, context):
        if self.environment is None:
            return context
        tools, capabilities = context.tools, dict(context.capabilities)
        if "tools" in capabilities and self.builtins is not None and self.builtins.name in context.project.components:
            registry = self.builtins.resolve_environment(context.project, self.environment)
            # Preserve imported tools and Agent selections; never enable extra tools.
            tools = ToolRegistry(tuple(registry.get(name) if name in registry.names() else context.tools.get(name)
                                       for name in context.tools.names()))
            capabilities["tools"] = tools
        if "completion_processors" in capabilities:
            capabilities["completion_processors"] = (
                *capabilities["completion_processors"], EnvironmentProcessor(self.environment.cwd))
        return replace(context, tools=tools, capabilities=capabilities)

    async def execute(self, context):
        bound = await drain_on_cancel(asyncio.to_thread(self.bind_context, context))
        if self.recovery is not None:
            from .tool_recovery import recovery_events
            stream = recovery_events(self.recovery, self.implementation, bound, self.environment,
                                     provider=self.provider_recovery)
        else:
            stream = self.implementation.execute(bound)
        try:
            async with aclosing(stream) as events:
                async for event in events:
                    yield event
                    if self.retry_monitor is not None:
                        self.retry_monitor.observe_tool(bound, event)
        finally:
            if self.retry_monitor is not None:
                self.retry_monitor.clear_tools(bound.run.id)
