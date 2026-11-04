"""Presentation-only essential settings; backend schemas remain authoritative.

These paths select controls, never runtime defaults or persistence fields. Unknown
extensions and schema-required inputs remain reachable. Full JSON editing retains
advanced, nullable, host-fixed and future fields under the original validation.
"""

from dataclasses import dataclass
from llm.core.agents import AgentDefinition

from ...config.general import GeneralSettings
from ...config.metadata import SettingMetadata
from ...config.theme import HubTheme
from ...config.sections import PreferenceSection


def paths(*names: str) -> frozenset[tuple[str, ...]]:
    return frozenset(tuple(name.split(SettingMetadata.PATH_SEPARATOR)) for name in names)


@dataclass(frozen=True, slots=True)
class EssentialSettings:
    scope: str
    name: str = ""

    GLOBAL = "global"
    PROJECT = "project"
    ENGINE = "engine"
    COMPONENT = "component"
    AGENT = "agent"
    CONNECTION = paths("model", "api_key", "api_base")
    GENERATION = CONNECTION | paths("reasoning_effort", "max_tokens", "max_completion_tokens")
    ENGINE_FIELDS = {
        "loop": paths("policy.max_iterations", "policy.provider.max_attempts",
                      "policy.completion.max_tokens", "policy.completion.reserve_tokens",
                      "policy.tool_feedback"),
        "graph": paths("workflow", "policy.max_steps"),
    }
    PROJECT_FIELDS = paths(
        "title", "config.policies.tools.max_calls",
        "config.policies.tool_retry.max_retries", "config.policies.run.timeout_seconds",
        "config.policies.context.max_chars",
    )
    COMPONENT_FIELDS = {
        "tools": paths("config.enabled"),
        "rag": paths("config.search.method", "config.search.rerank"),
        "memory": paths(),
        "vision": paths("config.ocr.backend", "config.ocr.backends.tesseract.language"),
        "goals": paths(),
        "refinement": paths(),
        "builtin_tools": paths("config.enabled", "policy.read_access.roots",
                               "policy.read_access.follow_symlinks", "policy.write_access.roots",
                               "policy.write_access.follow_symlinks", "policy.cwd_access.roots",
                               "policy.cwd_access.follow_symlinks"),
    }
    MODEL_PREFIXES = {
        "rag": paths("config.embedding_params", "config.extraction_params", "config.rerank_params"),
        "memory": paths("config.processing.completion"),
        "vision": paths("config.completion"),
    }
    AGENT_FIELDS = paths("purpose", "description", "system_prompt", "resources.prompt",
                         "resources.skills", "resources.rag", "resources.mcp")
    ADVANCED_GENERAL = frozenset((
        GeneralSettings.provider_max_detail_chars.__name__,
        GeneralSettings.recovery_timeout_seconds.__name__, GeneralSettings.recovery_input_chars.__name__,
        GeneralSettings.recovery_summary_chars.__name__, GeneralSettings.recovery_max_summary_calls.__name__,
    ))
    ADVANCED_APPEARANCE = frozenset((
        HubTheme.output_refresh_interval.__name__, HubTheme.progress_refresh_interval.__name__,
    ))

    def __call__(self, path: tuple[str, ...], *, required: bool = False) -> bool:
        if required:
            return True
        if self.scope == self.GLOBAL:
            hidden = (self.ADVANCED_GENERAL if self.name == PreferenceSection.GENERAL else
                      self.ADVANCED_APPEARANCE if self.name == PreferenceSection.APPEARANCE else ())
            return not path or path[0] not in hidden
        if self.scope == self.PROJECT:
            return path in self.PROJECT_FIELDS
        if self.scope == self.ENGINE:
            return self.name not in self.ENGINE_FIELDS or path in self.ENGINE_FIELDS[self.name]
        if self.scope == self.AGENT:
            if path[:1] == (AgentDefinition.Field.ENGINE_OPTIONS,):
                return EssentialSettings(self.ENGINE, self.name)(path[1:])
            return path in self.AGENT_FIELDS or (
                path[:1] == (AgentDefinition.Field.COMPLETION,) and path[1:] in self.GENERATION
            )
        if self.scope == self.COMPONENT:
            if not path:
                return False
            if self.name not in self.COMPONENT_FIELDS:
                return True
            return path in self.COMPONENT_FIELDS[self.name] or any(
                path[:len(prefix)] == prefix and path[len(prefix):] in self.CONNECTION
                for prefix in self.MODEL_PREFIXES.get(self.name, ())
            )
        raise ValueError(self.scope)
