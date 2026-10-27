"""Hub authoring tools use selected component handles, never storage paths."""

from llm.components.processing import COMPLETION_PROCESSORS

from .resource_views import CreatedResource

from llm.components.goals.records import GoalDefinition, GoalSettings
from llm.components.goals.tool_values import GoalToolArguments, GoalToolContract
from llm.components.rag.document_records import RAGDocumentInput
from llm.components.rag.document_views import DocumentView
from llm.components.workflows.graph import WorkflowHandlerContract
from llm.components.workflows.contracts import WorkflowDocument
from .resource_contracts import (
    ResourceToolContract,
    DefinitionArguments,
    ResourceAuthorship,
    AuthoredDefinition,
    DefinitionPreview,
    AuthoringTool,
    WorkflowAuthoring,
    RAGAuthoring,
    GoalAuthoring,
)

from copy import deepcopy

from llm.components.goals import GoalComponent
from llm.components.rag import RAGComponent
from llm.components.workflows import WorkflowComponent
from llm.core.tool_contracts import ToolContract
from llm.components.tools import Tool, ToolRegistry
from llm.services.runtime.tools import current_tool_call
from ...components import component_spec
from llm.core.tool_contracts import ToolEffect, ToolIsolation

IDENTIFIER = DefinitionArguments.IDENTIFIER_SCHEMA


def owner(data):
    call = current_tool_call()
    if (
        call is ResourceToolContract.ABSENT
        or call.project_id != data.project.id
        or not all((call.session_id, call.run_id, call.step_id))
    ):
        raise ValueError(ResourceToolContract.MISSING_OWNER)
    return call


def tool(
    name,
    description,
    properties,
    required,
    handler,
    *,
    write=ResourceToolContract.READ_ONLY
):
    return Tool(
        name,
        description,
        ResourceToolContract.parameters(properties, required),
        handler,
        contract=ToolContract(
            revision=ResourceToolContract.REVISION,
            effect=ToolEffect.UNKNOWN if write else ToolEffect.READ_ONLY,
            isolation=ToolIsolation.NONE,
            approval_required=write,
            operation_key_required=ResourceToolContract.OPERATION_KEY_REQUIRED,
            retry_fields=(),
        ),
    )


def authoring_tools(
    data,
    name,
    definition_schema,
    *,
    discover=AuthoringTool.DISCOVER,
    schema_extensions=ResourceToolContract.ABSENT,
    source_field=ResourceAuthorship.DEFAULT_FIELD,
    identifier_schema=ResourceToolContract.ABSENT
):
    """One schema/create contract shared by Skill, Workflow and Agent authoring."""
    prefix = component_spec(name).tool_prefix

    async def schema(args):
        owner(data)
        return deepcopy({**definition_schema, **(schema_extensions or {})})

    async def create(args):
        call = owner(data)
        definition = deepcopy(DefinitionArguments(args).definition)
        ResourceAuthorship.stamp(definition, source_field, call)
        identifier = await data.acreate(
            definition, identifier=DefinitionArguments(args).identifier
        )
        return AuthoredDefinition(identifier, name).to_dict()

    async def listing(args):
        owner(data)
        return DefinitionPreview.project(await data.alist())

    async def read(args):
        owner(data)
        return await data.asnapshot(DefinitionArguments(args).identifier)

    tools = [
        tool(
            AuthoringTool.SCHEMA_NAME.format(prefix=prefix),
            AuthoringTool.SCHEMA_DESCRIPTION.format(prefix=prefix),
            {},
            [],
            schema,
        ),
        tool(
            AuthoringTool.CREATE_NAME.format(prefix=prefix),
            AuthoringTool.creation_description(prefix),
            DefinitionArguments.create_properties(identifier_schema),
            DefinitionArguments.create_required(),
            create,
            write=ResourceToolContract.WRITES,
        ),
    ]
    if discover:
        tools += [
            tool(
                AuthoringTool.LIST_NAME.format(prefix=prefix),
                AuthoringTool.LIST_DESCRIPTION.format(name=name),
                {},
                [],
                listing,
            ),
            tool(
                AuthoringTool.READ_NAME.format(prefix=prefix),
                AuthoringTool.READ_DESCRIPTION,
                DefinitionArguments.read_properties(),
                DefinitionArguments.read_required(),
                read,
            ),
        ]
    return tools


class HubWorkflowComponent(WorkflowComponent):
    capabilities = (*WorkflowComponent.capabilities, ToolRegistry.CAPABILITY)

    def __init__(self, *, handlers=ResourceToolContract.ABSENT):
        self._handler_schemas = {
            name: WorkflowHandlerContract.describe_snapshot(handler)
            for name, handler in (handlers or {}).items()
        }

    def resolve_runtime(self, project, capability, *, data_factory):
        if capability == ToolRegistry.CAPABILITY:
            return ToolRegistry(
                tuple(
                    authoring_tools(
                        data_factory(self.name),
                        self.name,
                        self.schema,
                        schema_extensions=WorkflowAuthoring.extensions(
                            self._handler_schemas
                        ),
                        source_field=WorkflowDocument.METADATA,
                    )
                )
            )
        return super().resolve_runtime(project, capability, data_factory=data_factory)


class HubRAGComponent(RAGComponent):
    capabilities = (*RAGComponent.capabilities, COMPLETION_PROCESSORS)

    def resolve_runtime(self, project, capability, *, data_factory):
        if capability == COMPLETION_PROCESSORS:
            from ..execution.rag_catalog import RAGCatalogProcessor

            return RAGCatalogProcessor(data_factory(self.name))
        registry = super().resolve_runtime(
            project, capability, data_factory=data_factory
        )
        if capability != ToolRegistry.CAPABILITY:
            return registry
        data = data_factory(self.name)

        async def add(args):
            call = owner(data)
            values = deepcopy(args)
            from ..project.rag_import import document_description

            RAGDocumentInput.set_description_if_absent(
                values, document_description(RAGDocumentInput(values).content)
            )
            ResourceAuthorship.stamp(values, RAGDocumentInput.Field.METADATA, call)
            result = await data.aadd_document(**values)
            return DocumentView.created(result)

        registry.register(
            tool(
                RAGAuthoring.NAME,
                RAGAuthoring.DESCRIPTION,
                RAGAuthoring.properties(),
                RAGAuthoring.required(),
                add,
                write=ResourceToolContract.WRITES,
            )
        )
        return registry


class HubGoalComponent(GoalComponent):
    def resolve_runtime(self, project, capability, *, data_factory):
        registry = super().resolve_runtime(
            project, capability, data_factory=data_factory
        )
        if (
            capability != ToolRegistry.CAPABILITY
            or not GoalSettings(self.get_config(project)).write_tools_observation
        ):
            return registry
        data = data_factory(self.name)

        async def create(args):
            call = owner(data)
            identifier = await data.acreate(
                GoalDefinition.session_task(args, call, GoalAuthoring.RELATION),
                identifier=GoalToolArguments(args).identifier,
            )
            return CreatedResource.from_snapshot(
                identifier, await data.asnapshot(identifier)
            ).to_dict()

        async def complete(args):
            call = owner(data)
            if GoalToolArguments(args).identifier not in await data.alist(
                session_id=call.session_id
            ):
                raise ValueError(GoalToolContract.OUTSIDE_SCOPE)
            return await data.acomplete(**args)

        registry.register(
            tool(
                GoalAuthoring.CREATE_NAME,
                GoalAuthoring.CREATE_DESCRIPTION,
                GoalAuthoring.create_properties(),
                GoalAuthoring.create_required(),
                create,
                write=ResourceToolContract.WRITES,
            )
        )
        registry.register(
            tool(
                GoalAuthoring.COMPLETE_NAME,
                GoalAuthoring.COMPLETE_DESCRIPTION,
                GoalAuthoring.complete_properties(),
                GoalAuthoring.complete_required(),
                complete,
                write=ResourceToolContract.WRITES,
            )
        )
        from .record_policy import InternalRecords
        return InternalRecords.select(registry, InternalRecords.GOALS)
