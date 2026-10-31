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
from .binding_cache import BindingCache
from .request_profile import RequestProfile
from .request_routing import RequestRouting


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

        from llm.core.model_protocol import ModelRole
        present = any(m.message.role in (ModelRole.SYSTEM, ModelRole.DEVELOPER)
                      and WORK_GUIDANCE in m.message.text for m in request.messages)
        HubInputContext.insert(request, self.INSTRUCTION.format(cwd=self.cwd)
                               + ("" if present else WORK_GUIDANCE))
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
        self._bindings = BindingCache()
        self._lookup = self._lookup_tools = None
        self.routing = RequestRouting()

    def __getattr__(self, name):
        value = getattr(
            object.__getattribute__(self, EnvironmentEngine.IMPLEMENTATION), name
        )
        if name == EngineHooks.VALIDATE_RESUME:

            def validate(checkpoint, **kwargs):
                EngineHooks.rebind_resume_context(kwargs, self.bind_context)
                bound = kwargs.get(EngineHooks.RESUME_CONTEXT)
                engine = self.routed_engine(bound) if bound is not None else self.implementation
                return getattr(engine, name)(checkpoint, **kwargs)

            return validate
        if name == EngineHooks.BIND_EXECUTION:
            def bind(context):
                bound = self.bind_context(context)
                return getattr(self.routed_engine(bound), name)(bound)
            return bind
        if name == EngineHooks.WITH_INSTRUCTIONS:

            def compose(context, *, before=(), after=()):
                worker = copy(self)
                worker._bindings = BindingCache()
                worker._lookup = worker._lookup_tools = None
                worker.implementation = value(context, before=before, after=after)
                worker.routing = RequestRouting(self.routing.definition, self.routing.profile,
                    (*self.routing.layers, (tuple(before), tuple(after))))
                return worker

            return compose
        return value

    def for_agent(self, definition):
        from llm.engines.registry import EngineRegistry

        registry = EngineRegistry()
        registry.register(AgentDefinition.engine(definition), self.implementation)
        worker = copy(self)
        worker._bindings = BindingCache()
        worker._lookup = worker._lookup_tools = None
        worker.implementation = registry.resolve_agent(definition)
        worker.routing = RequestRouting(definition)
        return worker

    def for_context(self, request_context):
        options = dict(request_context)
        profile = RequestProfile.take(options)
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
        worker = EnvironmentEngine(
            implementation,
            self.builtins,
            environment,
            recovery=self.recovery,
            retry_monitor=self.retry_monitor,
            provider_recovery=self.provider_recovery,
        )
        worker.routing = RequestRouting(self.routing.definition, profile, self.routing.layers)
        return worker

    def bind_context(self, context):
        from ..tools.validation import ToolInputRegistry
        from ..tools.application import HubApplicationTools
        from .tool_catalog import ToolCatalog
        from .tool_lookup import ToolLookup
        mode = self.routing.mode(context, self.implementation)
        if (COMPLETION_PROCESSORS in context.capabilities and self.builtins is not None
                and self.builtins.name in context.project.components):
            from .tool_views import ToolViewProcessor
            processors = context.capabilities[COMPLETION_PROCESSORS]
            if not any(item.name == ToolViewProcessor.name for item in processors):
                context = replace(context, capabilities={**context.capabilities,
                    COMPLETION_PROCESSORS: (*processors, ToolViewProcessor(processors))})
        tools, capabilities = context.tools, dict(context.capabilities)
        constraints = context.tool_scope.argument_constraints if context.tool_scope is not None else None
        environment_tools = (
            self.environment is not self.ABSENT
            and ToolRegistry.CAPABILITY in capabilities
            and self.builtins is not self.ABSENT
            and self.builtins.name in context.project.components
        )
        discovery = (self.builtins is not None
            and self.builtins.name in context.project.components
            and HubApplicationTools.HELP in context.tools.names())
        if environment_tools or discovery:
            def build():
                groups = ToolCatalog.project_groups(context.project, self.builtins)
                selected = context.tools
                if environment_tools and selected.names():
                    registry = self.builtins.resolve_environment(context.project, self.environment)
                    names = frozenset(registry.names())
                    # Preserve imported tools and Agent selections; never enable extra tools.
                    selected = ToolInputRegistry.observe(registry.derive(tuple(
                        registry.get(name) if name in names else selected.get(name)
                        for name in selected.names())))
                if discovery:
                    self._lookup = ToolLookup(selected, groups=groups, constraints=constraints)
                    selected = ToolLookup.bind(selected, lookup=self._lookup)
                else:
                    self._lookup = None
                return ToolInputRegistry.observe(selected)
            tools = self._bindings.bind(context, self.environment, build, constraints=constraints)
        else:
            self._lookup = None
        tools = ToolInputRegistry.observe(tools)
        self._lookup_tools = tools
        if ToolRegistry.CAPABILITY in capabilities:
            capabilities[ToolRegistry.CAPABILITY] = tools
        if COMPLETION_PROCESSORS in capabilities:
            processors = capabilities[COMPLETION_PROCESSORS]
            if self.environment is not self.ABSENT:
                processors = (*processors, EnvironmentProcessor(self.environment.cwd))
            if self.routing.enabled(context, self.implementation):
                processors = self.routing.processors(processors, mode,
                    restore_prompt=self.routing.brief(context, self.implementation))
            capabilities[COMPLETION_PROCESSORS] = processors
        return replace(context, tools=tools, capabilities=capabilities)

    def routed_engine(self, bound):
        from .tool_catalog import ToolCatalog
        from ..tools.application import HubApplicationTools
        lookup = self._lookup if bound.tools is self._lookup_tools else None
        groups = (ToolCatalog.project_groups(bound.project, self.builtins)
                  if lookup is None and self.routing.enabled(bound, self.implementation) else None)
        discovery = (self.builtins is not None and self.builtins.name in bound.project.components
                     and HubApplicationTools.HELP in bound.tools.names())
        constraints = bound.tool_scope.argument_constraints if bound.tool_scope is not None else None
        return self.routing.engine(bound, self.implementation, groups=groups, discovery=discovery,
                                   constraints=constraints,
                                   lookup=lookup)

    async def execute(self, context):
        bound = await drain_on_cancel(asyncio.to_thread(self.bind_context, context))
        implementation = await drain_on_cancel(asyncio.to_thread(self.routed_engine, bound))
        if self.recovery is not self.ABSENT:
            from .tool_recovery import recovery_events

            stream = recovery_events(
                self.recovery,
                implementation,
                bound,
                self.environment,
                provider=self.provider_recovery,
            )
        else:
            stream = implementation.execute(bound)
        try:
            async with aclosing(stream) as events:
                async for event in events:
                    yield event
                    if self.retry_monitor is not self.ABSENT:
                        self.retry_monitor.observe_tool(bound, event)
        finally:
            self._bindings.clear()
            self._lookup = self._lookup_tools = None
            if self.retry_monitor is not self.ABSENT:
                self.retry_monitor.clear_tools(bound.run.id)
