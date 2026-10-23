"""MCP discovery snapshot storage. Connection freshness and execution stay with MCPData."""

from llm.components.base import validate_name
from llm.services.infrastructure.storage import atomic_json, read_json
from .records import MCPCatalog


class MCPCatalogRepository:
    DIRECTORY = "catalogs"
    SUFFIX = ".json"
    NOT_PREPARED = "MCP {identifier}: call aprepare before enabling Tools"

    def path(self, project, component, identifier):
        return component._checked(component.root(project) / self.DIRECTORY / (validate_name(identifier) + self.SUFFIX))

    def read(self, project, component, identifier):
        path = self.path(project, component, identifier)
        if not path.exists():
            raise ValueError(self.NOT_PREPARED.format(identifier=identifier))
        return MCPCatalog.from_dict(read_json(path))

    def write(self, project, component, identifier, catalog):
        atomic_json(self.path(project, component, identifier), catalog.to_dict())
