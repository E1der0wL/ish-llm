"""Public Engine extension discovery and synchronous binding contracts."""

from copy import deepcopy
from dataclasses import dataclass
import inspect

from llm.core.agents import AgentDefinition, AgentMode
from llm.core.configuration import UNSET
from llm.core.json_data import JsonObject
from llm.core.steering import SteeringMode


class EngineHooks:
    RESUME_DECISIONS = "decisions"
    EXECUTE = "execute"
    REQUIRED_CAPABILITIES = "required_capabilities"
    ADDITIONAL_CAPABILITIES = "additional_capabilities"
    STEERING_MODE = "steering_mode"
    AGENT_MODE = "agent_mode"
    CHECKPOINT_NAME = "checkpoint_name"
    VALIDATE_RESUME = "validate_resume"
    VALIDATE_SELECTIONS = "validate_selections"
    BIND_EXECUTION = "bind_execution"
    WITH_INSTRUCTIONS = "with_instructions"
    DESCRIBE_CONFIG = "describe_config"
    ABSENT = None
    NO_CAPABILITIES = ()
    INVALID_CAPABILITIES = "Engine required_capabilities must be a tuple of distinct names"
    UNSUPPORTED_RESUME = "Engine does not support checkpoint resume"

    @classmethod
    def decision_arguments(cls, decisions):
        return {cls.RESUME_DECISIONS: decisions} if decisions else {}

    @classmethod
    def executor(cls, engine):
        return getattr(engine, cls.EXECUTE, cls.ABSENT)

    @classmethod
    def checkpoint_name(cls, engine):
        return getattr(engine, cls.CHECKPOINT_NAME, cls.ABSENT)

    @classmethod
    def resume_validator(cls, engine):
        return getattr(engine, cls.VALIDATE_RESUME, cls.ABSENT)

    @classmethod
    def selection_validator(cls, engine):
        return getattr(engine, cls.VALIDATE_SELECTIONS, cls.ABSENT)

    @classmethod
    def discoverer(cls, engine):
        return getattr(engine, cls.ADDITIONAL_CAPABILITIES, cls.ABSENT)

    @classmethod
    def execution_binder(cls, engine):
        return getattr(engine, cls.BIND_EXECUTION, cls.ABSENT)

    @classmethod
    def instruction_binder(cls, engine):
        return getattr(engine, cls.WITH_INSTRUCTIONS, cls.ABSENT)

    @classmethod
    def schema_describer(cls, engine):
        return getattr(engine, cls.DESCRIBE_CONFIG, cls.ABSENT)

    @classmethod
    def required_names(cls, engine):
        return getattr(engine, cls.REQUIRED_CAPABILITIES, cls.NO_CAPABILITIES)


class ResumableHandlerContract:
    """Explicit trusted container declaration shared by Graph and Loop handlers."""

    ATTRIBUTE = "resumable_container"
    UNDECLARED = False
    DECLARED = True

    @classmethod
    def declared(cls, handler):
        return getattr(handler, cls.ATTRIBUTE, cls.UNDECLARED) is cls.DECLARED


def required_capabilities(engine) -> tuple[str, ...]:
    """An undeclared Engine needs no external capabilities."""
    names = getattr(engine, EngineHooks.REQUIRED_CAPABILITIES, EngineHooks.NO_CAPABILITIES)
    if (not isinstance(names, tuple) or any(not isinstance(name, str) or not name.strip() for name in names)
            or len(set(names)) != len(names)):
        raise ValueError(EngineHooks.INVALID_CAPABILITIES)
    return names


def steering_mode(engine) -> SteeringMode:
    return SteeringMode(getattr(engine, EngineHooks.STEERING_MODE, SteeringMode.UNSUPPORTED))


def agent_mode(engine) -> AgentMode:
    return AgentMode(getattr(engine, EngineHooks.AGENT_MODE, AgentMode.BEHAVIORAL))


@dataclass(frozen=True, slots=True)
class EngineBindingContract:
    """Fixed API diagnostics; both factories preserve the same public execution identity."""

    method: str
    unsupported: str
    asynchronous: str
    invalid_engine: str
    changed_mode: str
    changed_steering: str
    changed_checkpoint: str

    def bind(self, engine, value):
        factory = getattr(engine, self.method, EngineHooks.ABSENT)
        if not callable(factory):
            raise ValueError(self.unsupported)
        bound = factory(deepcopy(value))
        if inspect.isawaitable(bound):
            if inspect.iscoroutine(bound):
                bound.close()
            raise TypeError(self.asynchronous)
        if not callable(EngineHooks.executor(bound)):
            raise TypeError(self.invalid_engine)
        required_capabilities(bound)
        # Preserve short-circuit order: later hooks are not read on an earlier mismatch.
        if agent_mode(bound) != agent_mode(engine):
            raise ValueError(self.changed_mode)
        if steering_mode(bound) != steering_mode(engine):
            raise ValueError(self.changed_steering)
        if EngineHooks.checkpoint_name(bound) != EngineHooks.checkpoint_name(engine):
            raise ValueError(self.changed_checkpoint)
        return bound


class EngineContextBinding:
    INVALID_CONTEXT = "request_context must be a JSON object"
    CHANGED_CONTRACT = "Request context must preserve Engine execution contracts"
    CONTRACT = EngineBindingContract(
        method="for_context", unsupported="Selected Engine does not accept request context",
        asynchronous="Engine for_context must be synchronous", invalid_engine="for_context must return an Engine",
        changed_mode=CHANGED_CONTRACT, changed_steering=CHANGED_CONTRACT, changed_checkpoint=CHANGED_CONTRACT)

    @classmethod
    def bind(cls, engine, request_context=UNSET):
        if request_context is UNSET:
            return engine
        if not isinstance(request_context, dict):
            raise TypeError(cls.INVALID_CONTEXT)
        JsonObject.validate(request_context)
        return cls.CONTRACT.bind(engine, request_context)


class EngineAgentBinding:
    INVALID_CHECKPOINT = "Steerable Agent engine requires checkpoint_name and validate_resume"
    CONTRACT = EngineBindingContract(
        method="for_agent", unsupported="Agent engines must implement for_agent(definition)",
        asynchronous="Engine for_agent must be synchronous", invalid_engine="for_agent must return an Engine",
        changed_mode="Agent binding must preserve Engine Agent mode",
        changed_steering="Agent binding must preserve Engine steering mode",
        changed_checkpoint="Agent binding must preserve Engine checkpoint name")

    @classmethod
    def bind(cls, engine, definition):
        AgentDefinition.validate_mode(definition, agent_mode(engine))
        bound = cls.CONTRACT.bind(engine, definition)
        if steering_mode(bound) == SteeringMode.CONSUME:
            checkpoint = EngineHooks.checkpoint_name(bound)
            if (not isinstance(checkpoint, str) or not checkpoint.strip()
                    or not callable(EngineHooks.resume_validator(bound))):
                raise ValueError(cls.INVALID_CHECKPOINT)
        return bound
