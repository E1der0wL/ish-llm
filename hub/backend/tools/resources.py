"""Hub authoring tools use selected component handles, never storage paths."""

from copy import deepcopy

from llm.components.agents import AgentComponent
from llm.components.goals import GoalComponent
from llm.components.rag import RAGComponent
from llm.components.workflows import WorkflowComponent
from llm.components.tools import Tool, ToolContract, ToolRegistry
from llm.services.runtime.tools import current_tool_call
from ...components import component_spec

IDENTIFIER = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"}


def owner(data):
    call = current_tool_call()
    if call is None or call.project_id != data.project.id or not all((call.session_id, call.run_id, call.step_id)):
        raise ValueError("Resource tools require an owning Run in this Project")
    return call


def tool(name, description, properties, required, handler, *, write=False):
    return Tool(name, description, {"type": "object", "properties": properties,
        "required": required, "additionalProperties": False}, handler,
        contract=ToolContract(effect="unknown" if write else "read_only", approval_required=write))


def authoring_tools(data, component, *, discover=True, schema_extensions=None):
    """One schema/create contract shared by Skill, Workflow and Agent authoring."""
    prefix = component_spec(component.name).singular

    async def schema(args):
        owner(data)
        return deepcopy({**component.schema, **(schema_extensions or {})})

    async def create(args):
        call = owner(data)
        definition = deepcopy(args["definition"])
        definition.setdefault("metadata", {})["hub_source"] = {
            "session_id": call.session_id, "run_id": call.run_id, "step_id": call.step_id}
        identifier = await data.acreate(definition, identifier=args["identifier"])
        return {"created": identifier, "component": component.name, "applies_to": "subsequent_runs"}

    async def listing(args):
        owner(data)
        return {key: {name: value[name] for name in ("title", "purpose", "description", "engine") if name in value}
                for key, value in (await data.alist()).items()}

    async def read(args):
        owner(data)
        return await data.asnapshot(args["identifier"])

    tools = [tool(prefix + "_schema", "Read the exact JSON definition schema before creating a " + prefix + ".",
                  {}, [], schema),
             tool(prefix + "_create", "Create a new project " + prefix + ". Read " + prefix +
                  "_schema first. Existing IDs are never overwritten. Saved execution definitions become available "
                  "to subsequent Runs; creation does not execute or grant new authority.",
                  {"identifier": IDENTIFIER, "definition": {"type": "object"}},
                  ["identifier", "definition"], create, write=True)]
    if discover:
        tools += [tool(prefix + "_list", "List saved " + component.name + " in this Project.", {}, [], listing),
                  tool(prefix + "_read", "Read a saved definition and its revision as reference data.",
                       {"identifier": IDENTIFIER}, ["identifier"], read)]
    return tools


class HubWorkflowComponent(WorkflowComponent):
    capabilities = (*WorkflowComponent.capabilities, "tools")

    def __init__(self, *, handlers=None):
        self._handler_schemas = {
            name: deepcopy(handler.describe_config()) if callable(getattr(handler, "describe_config", None)) else None
            for name, handler in (handlers or {}).items()}

    def resolve_runtime(self, project, capability, *, data_factory):
        if capability == "tools":
            return ToolRegistry(tuple(authoring_tools(data_factory(self.name), self,
                schema_extensions={"x-hub-action-handlers": self._handler_schemas})))
        return super().resolve_runtime(project, capability, data_factory=data_factory)


class HubAgentComponent(AgentComponent):
    def resolve_runtime(self, project, capability, *, data_factory):
        registry = super().resolve_runtime(project, capability, data_factory=data_factory)
        if capability == "tools":
            for item in authoring_tools(data_factory(self.name), self):
                registry.register(item)
        return registry


class HubRAGComponent(RAGComponent):
    def resolve_runtime(self, project, capability, *, data_factory):
        registry = super().resolve_runtime(project, capability, data_factory=data_factory)
        if capability != "tools":
            return registry
        data = data_factory(self.name)

        async def add(args):
            call = owner(data)
            values = deepcopy(args)
            values.setdefault("metadata", {})["hub_source"] = {
                "session_id": call.session_id, "run_id": call.run_id, "step_id": call.step_id}
            result = await data.aadd_document(**values)
            return {key: result[key] for key in ("id", "title", "revision")}

        registry.register(tool("rag_add_document", "Register a document in this Project's RAG corpus and build its "
            "search index. Use file_read to read an authorized file first, then provide its text. Embedding "
            "configuration is required. Existing IDs are not overwritten. Report success only after this returns.",
            {"identifier": IDENTIFIER, "title": {"type": "string"}, "content": {"type": "string", "minLength": 1},
             "metadata": {"type": "object"}}, ["identifier", "title", "content"], add, write=True))
        return registry


class HubGoalComponent(GoalComponent):
    def resolve_runtime(self, project, capability, *, data_factory):
        registry = super().resolve_runtime(project, capability, data_factory=data_factory)
        if capability != "tools" or not self.get_config(project).get("policy", {}).get("write_tools"):
            return registry
        data = data_factory(self.name)

        async def create(args):
            call = owner(data)
            identifier = await data.acreate({"title": args["title"], "objective": args["objective"],
                "success_criteria": args["success_criteria"], "scope": {"type": "session", "id": call.session_id},
                "status": "active", "run_refs": [{"session_id": call.session_id, "run_id": call.run_id,
                                                     "relation": "contributes_to"}]}, identifier=args["identifier"])
            return {"id": identifier, **await data.asnapshot(identifier)}

        async def complete(args):
            call = owner(data)
            if args["identifier"] not in await data.alist(session_id=call.session_id):
                raise ValueError("Goal is outside the current Session scope")
            return await data.acomplete(**args)

        registry.register(tool("goal_create", "Track an authorized multi-step task in this Session with observable "
            "success criteria. Reuse existing goals; do not create goals for casual questions or invent new tasks.",
            {"identifier": IDENTIFIER, "title": {"type": "string", "minLength": 1},
             "objective": {"type": "string", "minLength": 1},
             "success_criteria": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}},
            ["identifier", "title", "objective", "success_criteria"], create, write=True))
        registry.register(tool("goal_complete", "Mark an existing goal complete only after verifying all its success "
            "criteria. Does not change or restart any Run.",
            {"identifier": IDENTIFIER, "expected_version": {"type": "string", "minLength": 1}},
            ["identifier", "expected_version"], complete, write=True))
        return registry
