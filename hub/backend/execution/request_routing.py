"""Narrow a selected root Loop through Engine/Tool public contracts."""

from copy import copy, deepcopy
from llm.core.agents import AgentDefinition

from .request_profile import RequestMode
from .completion_review import ConversationalInput
from .request_processing import RequestScopeProcessor, RoutedProcessor
from .tool_exposure import HubToolSelector


class RequestRouting:
    def __init__(self, definition=None, profile=None, layers=()):
        self.definition = deepcopy(definition)
        self.profile = profile
        self.layers = layers
        self._conversation_engine = None

    def enabled(self, context, implementation):
        from llm.engines.loop import LoopEngine
        from .retries import HubLoopEngine
        return not (self.profile is None or self.definition is None
                or type(implementation) not in (LoopEngine, HubLoopEngine)
                or context.output_step_id is not None
                or implementation.tool_selector is not None
                or self.definition.get(AgentDefinition.Field.POLICY, {}).get(AgentDefinition.PolicyField.REQUIRE_TOOL))

    def mode(self, context, implementation):
        if not self.enabled(context, implementation):
            return RequestMode.WORK
        if self.profile.mode == RequestMode.CONVERSATION:
            current = next((m for m in context.messages if m.id == context.run.input_message_id), None)
            if current is None or not ConversationalInput.matches(current.content):
                return RequestMode.WORK
        return self.profile.mode

    def brief(self, context, implementation):
        from ...asset.guides import SYSTEM_PROMPT
        return (self.mode(context, implementation) == RequestMode.CONVERSATION
                and self.definition.get(AgentDefinition.Field.SYSTEM_PROMPT) == SYSTEM_PROMPT)

    def engine(self, context, implementation, *, groups=None, discovery=False, constraints=None, lookup=None):
        from ...asset.guides import CONVERSATION_PROMPT
        if not self.enabled(context, implementation):
            return implementation
        if self.brief(context, implementation):
            if self._conversation_engine is None:
                definition = deepcopy(self.definition)
                definition[AgentDefinition.Field.SYSTEM_PROMPT] = CONVERSATION_PROMPT
                engine = implementation.for_agent(definition)
                for before, after in self.layers:
                    engine = engine.with_instructions(context, before=before, after=after)
                self._conversation_engine = engine
            engine = copy(self._conversation_engine)
        else:
            engine = copy(implementation)
        # The engine copy belongs to this binding/execution. Shared registered
        # engines never receive mutable selection or per-Run transcript state.
        engine.tool_selector = HubToolSelector(context.tools, self.mode(context, implementation),
                                              groups=groups, discovery=discovery, constraints=constraints, lookup=lookup)
        return engine

    @staticmethod
    def processors(processors, mode, *, restore_prompt=False):
        if mode == RequestMode.CONVERSATION:
            from llm.components.memory.processing import MemoryProcessor
            from llm.components.goals.processing import GoalProcessor
            from .tool_views import ToolViewProcessor
            from .environment import EnvironmentProcessor
            optional = (MemoryProcessor, GoalProcessor, ToolViewProcessor, EnvironmentProcessor)
            processors = tuple(RoutedProcessor(p, mode) if type(p) in optional else p for p in processors)
        return (*processors, RequestScopeProcessor(mode, restore_prompt=restore_prompt))
