"""Agent manager projection across Home and plugin-owned Projects."""

from .definitions import PluginIdentity
from ..project.record_management import RecordManagement
from ..project.management_values import ManagementAction, AgentLocation


async def list_agents(backend, home_id):
    rows = []
    home = await backend.projects.aload(home_id)
    projects = [(home, None)]
    for project in await backend.projects.alist():
        if project.id != home_id:
            record = await project.aget_data()
            if PluginIdentity.owner(record):
                projects.append((project, record.title))
    for project, plugin in projects:
        manager = await RecordManagement.for_project(project, "agents")
        rows.extend(AgentLocation.entry(row, project, plugin=plugin) for row in await manager.list())
    return rows


async def manage_agents(backend, home_id, target_id, operation, argument, version):
    project = await backend.projects.aload(target_id)
    if target_id != home_id and not PluginIdentity.owner(await project.aget_data()):
        raise ValueError("Agent manager target must be Home or a plugin Project")
    if operation == ManagementAction.LIST:
        return await list_agents(backend, home_id)
    manager = await RecordManagement.for_project(project, "agents")
    result = await manager.execute(operation, argument, version)
    if operation in (ManagementAction.READ, ManagementAction.DESCRIBE):
        return result
    return await list_agents(backend, home_id)
