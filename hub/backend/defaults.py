"""Hub-owned creation templates; existing projects are never reseeded on open."""

from copy import deepcopy
import asyncio

from llm.core.models import ProjectConfig

from ..asset.guides import SKILLS, SYSTEM_PROMPT


async def create_project(backend, title, *, config, components, conversation_storage):
    settings = ProjectConfig(deepcopy(config))
    engines = backend.project_schema()["properties"]["config"]["properties"]["parameters"]["properties"]["engines"]["properties"]
    if "system_prompt" in engines.get("loop", {}).get("properties", {}).get("config", {}).get("properties", {}):
        settings.parameters.setdefault("engines", {}).setdefault("loop", {}).setdefault("config", {}).setdefault("system_prompt", SYSTEM_PROMPT)
    project = await backend.projects.acreate(title, config=settings, components=components,
                                             conversation_storage=conversation_storage)
    try:
        if "skills" in components:
            skills = await project.components.aget("skills")
            for identifier, title, description, instructions in SKILLS:
                await skills.acreate({"title": title, "description": description, "instructions": instructions,
                                      "hub_template_version": 1}, identifier=identifier)
    except BaseException:
        # Do not leave a partially seeded project as the next startup selection.
        await asyncio.shield(project.adelete())
        raise
    return project
