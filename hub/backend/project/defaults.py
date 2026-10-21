"""Hub-owned creation templates; existing projects are never reseeded on open."""

from copy import deepcopy
import asyncio

from llm.core.models import ProjectConfig
from llm.components.rag.prompts import ExtractionFailurePolicy

from ...asset.guides import SKILLS, SYSTEM_PROMPT, TEMPLATE_VERSION


CONVERSATION_STORAGE = "file"


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
        "search": {"method": "hybrid", "expand": "chunk", "limit": 5, "rerank": True,
                   "candidate_count": 20, "rrf_constant": 60, "max_hops": 2,
                   "relation_limit": 20, "relation_ranking": "source"},
        "graph": {"buffer_pool_size": 64 * 1024 * 1024, "max_num_threads": 2}},
        "policy": {"embedding_concurrency": 2, "ingestion": {"max_active": 1},
                   "extraction": {"failure_policy": ExtractionFailurePolicy.DISABLED.value}}}


async def create_project(backend, title, *, config, components, conversation_storage):
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
            parameters = project_config.parameters.setdefault("components", {})
            parameters[name] = ProjectConfig.merge(preset, parameters.get(name, {}))
    if "rag" in components:
        parameters = project_config.parameters.setdefault("components", {})
        parameters["rag"] = ProjectConfig.merge(rag_defaults(), parameters.get("rag", {}))
    retry = project_config.policies.setdefault("tool_retry", {})
    retry.setdefault("max_retries", 2)
    retry.setdefault("delay_seconds", 0)
    if "builtin_tools" in components:
        from ..tools.builtin import HubBuiltinTools
        builtin = backend.project_manager.components.get("builtin_tools")
        if isinstance(builtin, HubBuiltinTools):
            project_config.parameters.setdefault("components", {}).setdefault("builtin_tools", {}).setdefault(
                "config", {}).setdefault("enabled", builtin.select_tools())
    engines = backend.describe_project_config()["properties"]["config"]["properties"]["parameters"]["properties"]["engines"]["properties"]
    if "system_prompt" in engines.get("loop", {}).get("properties", {}).get("config", {}).get("properties", {}):
        project_config.parameters.setdefault("engines", {}).setdefault("loop", {}).setdefault("config", {}).setdefault("system_prompt", SYSTEM_PROMPT)
    for name in ("loop", "_hub_title"):
        if "provider" in engines.get(name, {}).get("properties", {}).get("policy", {}).get("properties", {}):
            project_config.parameters.setdefault("engines", {}).setdefault(name, {}).setdefault("policy", {}).setdefault(
                "provider", provider_defaults())
    project = await backend.projects.acreate(title, config=project_config, components=components,
                                             conversation_storage=conversation_storage)
    try:
        if "skills" in components:
            skills = await project.components.aget("skills")
            for identifier, title, description, instructions in SKILLS:
                await skills.acreate({"title": title, "description": description, "instructions": instructions,
                                      "metadata": {"hub_template_version": TEMPLATE_VERSION}}, identifier=identifier)
    except BaseException:
        # Do not leave a partially seeded project as the next startup selection.
        await asyncio.shield(project.adelete())
        raise
    return project
