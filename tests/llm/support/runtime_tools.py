from llm.core.schema import implementation_schema
"""실행 정책 테스트용 Component capability 제공자. Project Tool 저장소를 사용하지 않는다."""
from llm.components.base import Component
from llm.components.tools import ToolRegistry, ToolData
from llm.services.infrastructure.storage import make_directory


class RuntimeTools(Component):
    name = directory = "tools"
    capabilities = ("tools",)
    data_class = ToolData

    def __init__(self, registry=None):
        self.registry = registry if registry is not None else ToolRegistry()

    def initialize(self, project):
        make_directory(self.root(project))

    def describe_config(self):
        return implementation_schema(config={'type': 'object', 'properties': {'enabled': {'type': 'array', 'items': {'type': 'string'}, 'uniqueItems': True, 'x-suggestions': list(self.registry.names())}}})

    @staticmethod
    def _selection_names(names):
        from llm.components.base import validate_name
        return [validate_name(n) for n in names]

    def enabled(self, project):
        return self.get_config(project).get("config", {}).get("enabled", [])

    def load(self, project, name):
        return ToolRegistry((self.registry.get(name),)).definitions()[0]

    def resolve(self, project, capability):
        if capability != "tools":
            return super().resolve(project, capability)
        return self.registry.select(tuple(self.enabled(project)))
