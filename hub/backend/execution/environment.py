"""Bind host execution context without changing llm's Engine or Tool APIs."""

from llm.components.processing import COMPLETION_PROCESSORS

from llm.core.agents import AgentDefinition
from llm.engines.contracts import EngineHooks, EngineContextBinding
from .turn_values import HubTurnContext
from .input_context import HubInputContext

from contextlib import aclosing
from copy import copy
from dataclasses import replace
import asyncio
import inspect

from llm.components.processing import CompletionSession
from llm.components.tools import ToolRegistry
from llm.services.infrastructure.storage import drain_on_cancel
from ...config.environment import ENVIRONMENT_OPTION, ShellEnvironment


class EnvironmentSession(CompletionSession):
    INSTRUCTION = (
        "The working directory captured from the user's ish shell for this request is "
        "{cwd!r}. Built-in file paths are relative to this directory. "
        "Process tools inherit the captured shell environment unless an explicit env is supplied. "
        "Sandbox commands still run inside their isolated test workspace.\n\n"
    )

    def __init__(self, cwd):
        self.cwd = cwd

    async def prepare(self, request):
        from ...asset.guides import WORK_GUIDANCE

        HubInputContext.insert(
            request, self.INSTRUCTION.format(cwd=self.cwd) + WORK_GUIDANCE
        )
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

    IMPLEMENTATION = "implementation"
    ABSENT = None
    UNSUPPORTED_CONTEXT = "Engine does not support these request context fields"

    def __init__(
        self,
        implementation,
        builtins,
        environment=ABSENT,
        *,
        recovery=ABSENT,
        retry_monitor=ABSENT,
        provider_recovery=ABSENT
    ):
        self.implementation = implementation
        self.builtins = builtins
        self.environment = environment
        self.recovery = recovery
        self.retry_monitor = retry_monitor
        self.provider_recovery = provider_recovery

    def __getattr__(self, name):
        value = getattr(
            object.__getattribute__(self, EnvironmentEngine.IMPLEMENTATION), name
        )
        if name == EngineHooks.VALIDATE_RESUME:

            def validate(checkpoint, **kwargs):
                EngineHooks.rebind_resume_context(kwargs, self.bind_context)
                return value(checkpoint, **kwargs)

            return validate
        if name == EngineHooks.BIND_EXECUTION:
            return lambda context: value(self.bind_context(context))
        if name == EngineHooks.WITH_INSTRUCTIONS:

            def compose(*args, **kwargs):
                worker = copy(self)
                worker.implementation = value(*args, **kwargs)
                return worker

            return compose
        return value

    def for_agent(self, definition):
        from llm.engines.registry import EngineRegistry

        registry = EngineRegistry()
        registry.register(AgentDefinition.engine(definition), self.implementation)
        worker = copy(self)
        worker.implementation = registry.resolve_agent(definition)
        return worker

    def for_context(self, request_context):
        options = dict(request_context)
        HubTurnContext.take(options)
        from ..tools.application import AgentMessageContext
        AgentMessageContext.take(options)
        environment = (
            ShellEnvironment.from_dict(options.pop(ENVIRONMENT_OPTION))
            if ENVIRONMENT_OPTION in options
            else self.ABSENT
        )
        factory = getattr(
            self.implementation, EngineContextBinding.CONTRACT.method, self.ABSENT
        )
        if callable(factory):
            implementation = factory(options)
        elif options:
            raise ValueError(self.UNSUPPORTED_CONTEXT)
        else:
            implementation = self.implementation
        if inspect.isawaitable(implementation):
            if inspect.iscoroutine(implementation):
                implementation.close()
            raise TypeError(EngineContextBinding.CONTRACT.asynchronous)
        if not callable(EngineHooks.executor(implementation)):
            raise TypeError(EngineContextBinding.CONTRACT.invalid_engine)
        return EnvironmentEngine(
            implementation,
            self.builtins,
            environment,
            recovery=self.recovery,
            retry_monitor=self.retry_monitor,
            provider_recovery=self.provider_recovery,
        )

    def bind_context(self, context):
        if self.environment is self.ABSENT:
            return context
        tools, capabilities = context.tools, dict(context.capabilities)
        if (
            ToolRegistry.CAPABILITY in capabilities
            and self.builtins is not self.ABSENT
            and self.builtins.name in context.project.components
        ):
            registry = self.builtins.resolve_environment(
                context.project, self.environment
            )
            # Preserve imported tools and Agent selections; never enable extra tools.
            tools = ToolRegistry(
                tuple(
                    (
                        registry.get(name)
                        if name in registry.names()
                        else context.tools.get(name)
                    )
                    for name in context.tools.names()
                )
            )
            capabilities[ToolRegistry.CAPABILITY] = tools
        if COMPLETION_PROCESSORS in capabilities:
            capabilities[COMPLETION_PROCESSORS] = (
                *capabilities[COMPLETION_PROCESSORS],
                EnvironmentProcessor(self.environment.cwd),
            )
        return replace(context, tools=tools, capabilities=capabilities)

    async def execute(self, context):
        bound = await drain_on_cancel(asyncio.to_thread(self.bind_context, context))
        if self.recovery is not self.ABSENT:
            from .tool_recovery import recovery_events

            stream = recovery_events(
                self.recovery,
                self.implementation,
                bound,
                self.environment,
                provider=self.provider_recovery,
            )
        else:
            stream = self.implementation.execute(bound)
        try:
            async with aclosing(stream) as events:
                async for event in events:
                    yield event
                    if self.retry_monitor is not self.ABSENT:
                        self.retry_monitor.observe_tool(bound, event)
        finally:
            if self.retry_monitor is not self.ABSENT:
                self.retry_monitor.clear_tools(bound.run.id)
