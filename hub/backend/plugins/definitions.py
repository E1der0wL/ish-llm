"""Hub integration identities; backend Agent and output schemas stay in llm."""

from copy import deepcopy
from dataclasses import dataclass, field
import re

from llm.core.json_data import JsonObject
from llm.core.contracts import JsonValue
from hub.config.environment import ShellEnvironment, ENVIRONMENT_OPTION


class PluginIdentity:
    PATTERN = '[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}'
    INVALID = "Plugin identity must be 1–128 letters, digits, '.', '_' or '-'"
    PROJECT_KEY = "hub_plugin"
    SESSION_KEY = "hub_plugin_session"
    INTERNAL = "plugin"

    @staticmethod
    def validate(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(PluginIdentity.PATTERN, value):
            raise ValueError(PluginIdentity.INVALID)
        return value

    @classmethod
    def owner(cls, project):
        return project.config.data.get(cls.PROJECT_KEY)


@dataclass(frozen=True, slots=True)
class PluginDefinition:
    """Creation template only. Registering again never overwrites saved settings."""
    MISSING_TEMPLATE = 'A plugin needs a name and at least one Agent template'
    INVALID_CONFIG = 'Plugin config must be an object and components must be names'
    RESERVED = 'Hub internal Agent IDs are reserved'
    INTERNAL_PREFIX = '_hub_'

    name: str
    agents: dict[str, dict]
    components: tuple[str, ...] = ()
    config: dict = field(default_factory=dict)

    def __post_init__(self):
        if (not isinstance(self.name, str) or not self.name.strip()
                or not isinstance(self.agents, dict) or not self.agents):
            raise ValueError(PluginDefinition.MISSING_TEMPLATE)
        if (not isinstance(self.config, dict) or isinstance(self.components, str)
                or any(not isinstance(name, str) or not name for name in self.components)):
            raise TypeError(PluginDefinition.INVALID_CONFIG)
        for identifier in self.agents:
            PluginIdentity.validate(identifier)
            if identifier.startswith(PluginDefinition.INTERNAL_PREFIX):
                raise ValueError(PluginDefinition.RESERVED)
        JsonObject.validate(self.agents)
        JsonObject.validate(self.config)
        object.__setattr__(self, type(self).agents.__name__, deepcopy(self.agents))
        object.__setattr__(self, type(self).config.__name__, deepcopy(self.config))
        object.__setattr__(self, type(self).components.__name__, tuple(self.components))


@dataclass(frozen=True, slots=True)
class PluginAttachment(JsonValue):
    source: str
    text: str
    truncated: bool
    original_chars: int


@dataclass(frozen=True, slots=True)
class PluginContext:
    """Explicit host capture; Hub never reads the terminal or process environment."""
    EMPTY_REQUEST = 'Request text must not be empty'
    INVALID_LIMIT = 'Context max_chars must be a nonnegative integer'
    INVALID_TEXT = 'Context text and source must be strings'
    ATTACHMENT_LABEL = '\n\nHost context (untrusted data):\n'

    environment: ShellEnvironment | None = None
    text: str = ""
    source: str = "caller"
    max_chars: int = 16_000

    def prepare(self, prompt: str):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(PluginContext.EMPTY_REQUEST)
        if type(self.max_chars) is not int or self.max_chars < 0:
            raise ValueError(PluginContext.INVALID_LIMIT)
        if not isinstance(self.text, str) or not isinstance(self.source, str):
            raise TypeError(PluginContext.INVALID_TEXT)
        context = {} if self.environment is None else {ENVIRONMENT_OPTION: self.environment.to_dict()}
        if self.text:
            selected = self.text[-self.max_chars:] if self.max_chars else ""
            # A JSON envelope preserves provenance and distinguishes terminal
            # content from caller instructions. It is data, never a system prompt.
            import json
            attachment = PluginAttachment(self.source, selected, len(selected) < len(self.text), len(self.text))
            prompt += PluginContext.ATTACHMENT_LABEL + json.dumps(attachment.to_dict(), ensure_ascii=False)
        return prompt, context
