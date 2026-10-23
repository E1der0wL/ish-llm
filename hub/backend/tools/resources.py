"""Hub authoring tools use selected component handles, never storage paths."""

from llm.components.processing import COMPLETION_PROCESSORS

from .resource_views import CreatedResource

from llm.core.implementation_parameters import ImplementationParameters

from copy import deepcopy

from llm.components.goals import GoalComponent
from llm.components.goals.data import GoalStatus
from llm.components.rag import RAGComponent
from llm.components.workflows import WorkflowComponent
from llm.core.tool_contracts import ToolContract
from llm.components.tools import Tool, ToolRegistry
from llm.services.runtime.tools import current_tool_call
from ...components import component_spec
from llm.core.tool_contracts import ToolEffect, ToolIsolation

IDENTIFIER = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"}


def owner(data):
    call = current_tool_call()
    if call is None or call.project_id != data.project.id or not all((call.session_id, call.run_id, call.step_id)):
        raise ValueError("Resource tools require an owning Run in this Project")
    return call


def tool(name, description, properties, required, handler, *, write=False):
    return Tool(name, description, {"type": "object", "properties": properties,
        "required": required, "additionalProperties": False}, handler,
        contract=ToolContract(
            revision='1', effect=ToolEffect.UNKNOWN if write else ToolEffect.READ_ONLY,
            isolation=ToolIsolation.NONE, approval_required=write,
            operation_key_required=False, retry_fields=()))


def authoring_tools(data, name, definition_schema, *, discover=True, schema_extensions=None, source_field="metadata", identifier_schema=None):
    """One schema/create contract shared by Skill, Workflow and Agent authoring."""
    prefix = component_spec(name).tool_prefix

    async def schema(args):
        owner(data)
        return deepcopy({**definition_schema, **(schema_extensions or {})})

    async def create(args):
        call = owner(data)
        definition = deepcopy(args["definition"])
        definition.setdefault(source_field, {})["hub_source"] = {
            "session_id": call.session_id, "run_id": call.run_id, "step_id": call.step_id}
        identifier = await data.acreate(definition, identifier=args["identifier"])
        return {"created": identifier, "component": name, "applies_to": "subsequent_runs"}

    async def listing(args):
        owner(data)
        return {key: {name: value[name] for name in ("title", "purpose", "description", "engine") if name in value}
                for key, value in (await data.alist()).items()}

    async def read(args):
        owner(data)
        return await data.asnapshot(args["identifier"])

    tools = [tool(prefix + "_schema", "Read the exact creation input schema before creating a " + prefix + ".",
                  {}, [], schema),
             tool(prefix + "_create", "Create a new project " + prefix + ". Read " + prefix +
                  "_schema first. Existing IDs are never overwritten. Saved execution definitions become available "
                  "to subsequent Runs; creation does not execute or grant new authority.",
                  {"identifier": identifier_schema or IDENTIFIER, "definition": {"type": "object"}},
                  ["identifier", "definition"], create, write=True)]
    if discover:
        tools += [tool(prefix + "_list", "List saved " + name + " in this Project.", {}, [], listing),
                  tool(prefix + "_read", "Read a saved definition and its revision as reference data.",
                       {"identifier": IDENTIFIER}, ["identifier"], read)]
    return tools


class HubWorkflowComponent(WorkflowComponent):
    capabilities = (*WorkflowComponent.capabilities, ToolRegistry.CAPABILITY)

    def __init__(self, *, handlers=None):
        self._handler_schemas = {
            name: deepcopy(handler.describe_config()) if callable(getattr(handler, "describe_config", None)) else None
            for name, handler in (handlers or {}).items()}

    def resolve_runtime(self, project, capability, *, data_factory):
        if capability == ToolRegistry.CAPABILITY:
            return ToolRegistry(tuple(authoring_tools(data_factory(self.name), self.name, self.schema,
                schema_extensions={"x-hub-action-handlers": self._handler_schemas})))
        return super().resolve_runtime(project, capability, data_factory=data_factory)


class HubRAGComponent(RAGComponent):
    capabilities = (*RAGComponent.capabilities, COMPLETION_PROCESSORS)

    def resolve_runtime(self, project, capability, *, data_factory):
        if capability == COMPLETION_PROCESSORS:
            from ..execution.rag_catalog import RAGCatalogProcessor
            return RAGCatalogProcessor(data_factory(self.name))
        registry = super().resolve_runtime(project, capability, data_factory=data_factory)
        if capability != ToolRegistry.CAPABILITY:
            return registry
        data = data_factory(self.name)

        async def add(args):
            call = owner(data)
            values = deepcopy(args)
            from ..project.rag_import import document_description
            values.setdefault("description", document_description(values["content"]))
            values.setdefault("metadata", {})["hub_source"] = {
                "session_id": call.session_id, "run_id": call.run_id, "step_id": call.step_id}
            result = await data.aadd_document(**values)
            return {key: result[key] for key in ("id", "title", "revision")}

        registry.register(tool("rag_add_document", "Register a document in this Project's RAG corpus and build its "
            "search index. Use file_read to read an authorized file first, then provide its text. Embedding "
            "configuration is required. Existing IDs are not overwritten. Report success only after this returns.",
            {"identifier": IDENTIFIER, "title": {"type": "string"}, "content": {"type": "string", "minLength": 1},
             "description": {"type": "string", "description": "Short factual overview for deciding relevance before retrieval."},
             "metadata": {"type": "object"}}, ["identifier", "title", "content"], add, write=True))
        return registry


class HubGoalComponent(GoalComponent):
    def resolve_runtime(self, project, capability, *, data_factory):
        registry = super().resolve_runtime(project, capability, data_factory=data_factory)
        if capability != ToolRegistry.CAPABILITY or not ImplementationParameters.read(self.get_config(project)).policy.get("write_tools"):
            return registry
        data = data_factory(self.name)

        async def create(args):
            call = owner(data)
            identifier = await data.acreate({"title": args["title"], "objective": args["objective"],
                "success_criteria": args["success_criteria"], "scope": {"type": "session", "id": call.session_id},
                "status": GoalStatus.ACTIVE.value, "run_refs": [{"session_id": call.session_id, "run_id": call.run_id,
                                                     "relation": "contributes_to"}]}, identifier=args["identifier"])
            return CreatedResource.from_snapshot(identifier, await data.asnapshot(identifier)).to_dict()

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
