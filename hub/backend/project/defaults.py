"""Hub-owned creation templates; existing projects are never reseeded on open."""
from hub.backend.execution.engine_selection import HubEngineIdentity

from copy import deepcopy
import asyncio

from llm.core.models import ConversationStorage, ProjectConfig
from llm.services.schema import ProjectConfiguration
from llm.components.tools.selection import ToolSelection
from llm.components.rag.prompts import ExtractionFailurePolicy
from llm.components.rag.search import SearchMethod, SearchExpansion, RelationRanking

from ...asset.guides import STARTER_SKILLS, SYSTEM_PROMPT, TEMPLATE_VERSION
from .agents import DEFAULT_AGENT
from .title_contract import TitleEngineIdentity


CONVERSATION_STORAGE = ConversationStorage.FILE.value


def creation_values(backend, values):
    """Materialize Hub choices only for missing creation fields."""
    result = deepcopy(values)
    result.setdefault("conversation_storage", CONVERSATION_STORAGE)
    result.setdefault("components", list(backend.project_manager.components.names()))
    return result


def provider_defaults():
    """Retry only pre-output transient failures through llm's provider policy."""
    return {"max_attempts": 4, "delay_seconds": 10, "max_delay_seconds": 60}


def rag_defaults():
    """Model-independent defaults; graph extraction is an explicit later opt-in."""
    return {"config": {
        "chunk_size": 1200, "embedding_cache_max_bytes": 16 * 1024 * 1024,
        "extraction_batch_size": 8, "search_cache_chars": 1_000_000, "index_batch_size": 64,
        "search": {"method": SearchMethod.HYBRID.value, "expand": SearchExpansion.CHUNK.value, "limit": 5, "rerank": True,
                   "candidate_count": 20, "rrf_constant": 60, "max_hops": 2,
                   "relation_limit": 20, "relation_ranking": RelationRanking.SOURCE.value},
        "graph": {"buffer_pool_size": 64 * 1024 * 1024, "max_num_threads": 2}},
        "policy": {"embedding_concurrency": 2, "ingestion": {"max_active": 1},
                   "extraction": {"failure_policy": ExtractionFailurePolicy.DISABLED.value}}}


async def create_project(backend, title, *, config, components, conversation_storage,
                         agent_id=DEFAULT_AGENT, agent_definition=None):
    project_config = ProjectConfig.from_dict(deepcopy(config))
    # Application creation presets; explicit user policies always win.
    presets = {
        "vision": {"config": {"ocr": {"backend": "tesseract"}}, "policy": {"ocr": {"timeout": 60}}},
        "memory": {"config": {"search_strategy": "keyword", "search_status": "all", "search_limit": 5,
            "processing": {"priority": 10, "recall_limit": 5, "recall_query_chars": 2000}},
            "policy": {"tool_write_status": "candidate", "max_search_results": 20,
                       "processing": {"recall": True, "context_chars": 4000, "failure_mode": "continue"}}},
        "goals": {"config": {"priority": 20}, "policy": {"inject": True, "write_tools": True}},
        "refinement": {"policy": {"proposal_operations": ["update", "create", "fork", "bind_skills"],
                                   "apply_tools": True}},
    }
    for name, preset in presets.items():
        if name in components:
            current = project_config.parameter_targets.component(name).to_dict()
            project_config.set_component_parameters(name, ProjectConfig.merge(preset, current))
    if "rag" in components:
        current = project_config.parameter_targets.component("rag").to_dict()
        project_config.set_component_parameters("rag", ProjectConfig.merge(rag_defaults(), current))
    retry = project_config.policies.setdefault("tool_retry", {})
    retry.setdefault("max_retries", 2)
    retry.setdefault("delay_seconds", 0)
    if "builtin_tools" in components:
        from ..tools.builtin import HubBuiltinTools
        builtin = backend.project_manager.components.get("builtin_tools")
        if isinstance(builtin, HubBuiltinTools):
            parameters = project_config.parameter_targets.component("builtin_tools")
            defaults = builtin.select_tools()
            selection = ToolSelection.read(parameters.config, scope=builtin.name)
            if not selection.configured:
                selection = ToolSelection.selected(defaults, scope=builtin.name)
            project_config.set_component_parameters("builtin_tools", parameters.with_config(selection.to_dict()).to_dict())
    engines = ProjectConfiguration.engine_parameter_schemas(backend.describe_project_config())
    if "system_prompt" in engines.get(HubEngineIdentity.LOOP, {}).get("properties", {}).get("config", {}).get("properties", {}):
        parameters = project_config.parameter_targets.engine(HubEngineIdentity.LOOP)
        values = parameters.config
        values.setdefault("system_prompt", SYSTEM_PROMPT)
        project_config.set_engine_parameters(HubEngineIdentity.LOOP, parameters.with_config(values).to_dict())
    for name in (HubEngineIdentity.LOOP, TitleEngineIdentity.NAME):
        if "provider" in engines.get(name, {}).get("properties", {}).get("policy", {}).get("properties", {}):
            parameters = project_config.parameter_targets.engine(name)
            values = parameters.policy
            values.setdefault("provider", provider_defaults())
            project_config.set_engine_parameters(name, parameters.with_policy(values).to_dict())
    project = await backend.projects.acreate(title, config=project_config, components=components,
                                             conversation_storage=conversation_storage)
    try:
        from .agents import initialize
        await initialize(backend, project, identifier=agent_id, definition=agent_definition)
        if "skills" in components:
            skills = await project.components.aget("skills")
            for skill in STARTER_SKILLS:
                await skills.aimport_directory(skill.directory)
    except BaseException:
        # Do not leave a partially seeded project as the next startup selection.
        await asyncio.shield(project.adelete())
        raise
    return project
