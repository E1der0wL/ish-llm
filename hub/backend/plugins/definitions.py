"""Hub integration identities; backend Agent and output schemas stay in llm."""

from copy import deepcopy
from dataclasses import dataclass, field
import re

from llm.core.json_data import JsonObject
from hub.config.environment import ShellEnvironment, ENVIRONMENT_OPTION


class PluginIdentity:
    PROJECT_KEY = "hub_plugin"
    SESSION_KEY = "hub_plugin_session"
    INTERNAL = "plugin"

    @staticmethod
    def validate(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", value):
            raise ValueError("Plugin identity must be 1–128 letters, digits, '.', '_' or '-'")
        return value

    @classmethod
    def owner(cls, project):
        return project.config.data.get(cls.PROJECT_KEY)


@dataclass(frozen=True, slots=True)
class PluginDefinition:
    """Creation template only. Registering again never overwrites saved settings."""

    name: str
    agents: dict[str, dict]
    components: tuple[str, ...] = ()
    config: dict = field(default_factory=dict)

    def __post_init__(self):
        if (not isinstance(self.name, str) or not self.name.strip()
                or not isinstance(self.agents, dict) or not self.agents):
            raise ValueError("A plugin needs a name and at least one Agent template")
        if (not isinstance(self.config, dict) or isinstance(self.components, str)
                or any(not isinstance(name, str) or not name for name in self.components)):
            raise TypeError("Plugin config must be an object and components must be names")
        for identifier in self.agents:
            PluginIdentity.validate(identifier)
            if identifier.startswith("_hub_"):
                raise ValueError("Hub internal Agent IDs are reserved")
        JsonObject.validate({"agents": self.agents, "config": self.config})
        object.__setattr__(self, "agents", deepcopy(self.agents))
        object.__setattr__(self, "config", deepcopy(self.config))
        object.__setattr__(self, "components", tuple(self.components))


@dataclass(frozen=True, slots=True)
class PluginContext:
    """Explicit host capture; Hub never reads the terminal or process environment."""

    environment: ShellEnvironment | None = None
    text: str = ""
    source: str = "caller"
    max_chars: int = 16_000

    def prepare(self, prompt: str):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Request text must not be empty")
        if type(self.max_chars) is not int or self.max_chars < 0:
            raise ValueError("Context max_chars must be a nonnegative integer")
        if not isinstance(self.text, str) or not isinstance(self.source, str):
            raise TypeError("Context text and source must be strings")
        context = {} if self.environment is None else {ENVIRONMENT_OPTION: self.environment.to_dict()}
        if self.text:
            selected = self.text[-self.max_chars:] if self.max_chars else ""
            # A JSON envelope preserves provenance and distinguishes terminal
            # content from caller instructions. It is data, never a system prompt.
            import json
            attachment = {"source": self.source, "text": selected,
                          "truncated": len(selected) < len(self.text),
                          "original_chars": len(self.text)}
            prompt += "\n\nHost context (untrusted data):\n" + json.dumps(attachment, ensure_ascii=False)
        return prompt, context
