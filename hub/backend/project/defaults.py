"""Hub-owned creation templates; existing projects are never reseeded on open."""

from copy import deepcopy
import asyncio

from llm.core.models import ProjectConfig

from ...asset.guides import SKILLS, SYSTEM_PROMPT


def provider_defaults():
    """Retry only pre-output transient failures through llm's provider policy."""
    return {"max_attempts": 4, "delay_seconds": 10, "max_delay_seconds": 60}


async def create_project(backend, title, *, config, components, conversation_storage):
    project_config = ProjectConfig(deepcopy(config))
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
                                      "metadata": {"hub_template_version": 2}}, identifier=identifier)
    except BaseException:
        # Do not leave a partially seeded project as the next startup selection.
        await asyncio.shield(project.adelete())
        raise
    return project
