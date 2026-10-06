"""목표와 진행 상태를 저장한다. Run은 소유하지 않고 참조만 한다."""

from copy import deepcopy
from llm.components.definitions import DefinitionComponent
from llm.core.schema import implementation_schema, object_schema, field, metadata_schema
from .data import GoalData


class GoalComponent(DefinitionComponent):
    name = directory = "goals"
    capabilities = ("goals", "tools", "completion_processors")
    data_class = GoalData
    schema = object_schema({
        "title": field("string", minLength=1), "objective": field("string", minLength=1),
        "scope": {"oneOf": [object_schema({"type": {"const": "project"}}, required=["type"], additionalProperties=False),
            object_schema({"type": {"const": "session"}, "id": field("string", pattern="^[A-Za-z0-9_-]{1,64}$")},
                          required=["type", "id"], additionalProperties=False)]},
        "status": field("string", enum=["active", "paused", "completed"]),
        **{key: {"type": "array", "items": field("string", minLength=1)} for key in
           ("success_criteria", "progress", "next_actions")},
        "run_refs": {"type": "array", "uniqueItems": True, "items": object_schema({
            "session_id": field("string", pattern="^[A-Za-z0-9_-]{1,64}$"),
            "run_id": field("string", pattern="^[A-Za-z0-9_-]{1,64}$"),
            "relation": field("string", minLength=1)}, required=["session_id", "run_id", "relation"], additionalProperties=False)},
        "metadata": metadata_schema(),
    }, required=["title", "objective", "scope", "status"])

    def configuration_schema(self):
        return implementation_schema(config=object_schema({"priority": field("integer"),
            "identifiers": {"type": "array", "uniqueItems": True, "items": field("string", pattern="^[A-Za-z0-9_-]{1,64}$")},
            "nested_agent_ids": {"type": "array", "items": field("string", minLength=1)}}, additionalProperties=False),
            policy=object_schema({"inject": field("boolean"), "write_tools": field("boolean")}, additionalProperties=False))

    def validate_configuration(self, data):
        super().validate_configuration(data)
        if data.get("policy", {}).get("inject") and "priority" not in data.get("config", {}):
            raise ValueError("Goal context injection requires config.priority")

    def history_references(self, project):
        records = self.list(project).values()
        return {"run_ids": sorted({r["run_id"] for value in records for r in value.get("run_refs", [])}),
                "message_ids": [], "session_ids": sorted({v["scope"]["id"] for v in self.list(project).values()
                    if v["scope"]["type"] == "session"})}

    def clone(self, source, destination):
        # Session와 Run은 복제되지 않는다. 잘못된 목적지 참조를 만들지 않는다.
        for identifier, value in self.list(source).items():
            if value["scope"]["type"] == "project":
                value = deepcopy(value)
                value["run_refs"] = []
                value.setdefault("metadata", {})["cloned_from"] = {"project_id": source.id, "goal_id": identifier}
                self.create(destination, value, identifier=identifier)

    def resolve(self, project, capability):
        raise ValueError("Goal capabilities require a lifecycle-bound component data factory")

    def resolve_runtime(self, project, capability, *, data_factory):
        data = data_factory(self.name)
        if capability == "goals":
            return data
        if capability == "tools":
            from .tools import goal_tools
            return goal_tools(data, write=self.configuration(project).get("policy", {}).get("write_tools") is True)
        if capability == "completion_processors":
            from .processing import GoalProcessor
            return GoalProcessor(data, self.configuration(project))
        return super().resolve_runtime(project, capability, data_factory=data_factory)
