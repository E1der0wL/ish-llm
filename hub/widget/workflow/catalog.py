"""Presentation of backend-declared schemas, not a second graph validator."""

from llm.components.workflows.bindings import WorkflowBindings as BindingFields
from llm.core.schema import SchemaKeyword
from llm.components.workflows.contracts import WorkflowDocument
from llm.components.workflows.contracts import WorkflowNode

from copy import deepcopy
from ...backend.project.workflow_contracts import WorkflowCatalogData


class WorkflowCatalog:
    def __init__(self, declaration):
        data = WorkflowCatalogData.from_dict(declaration)
        self.schema = data.schema
        self.engines = data.engines
        self.variants = self.schema.get(SchemaKeyword.PROPERTIES, {}).get(WorkflowDocument.NODES, {}).get(
            SchemaKeyword.ADDITIONAL_PROPERTIES, {}).get(SchemaKeyword.ONE_OF, [])

    def kinds(self, engine):
        kinds = [item[SchemaKeyword.PROPERTIES][WorkflowNode.TYPE][SchemaKeyword.CONST] for item in self.variants
                 if SchemaKeyword.CONST in item.get(SchemaKeyword.PROPERTIES, {}).get(WorkflowNode.TYPE, {})]
        return list(dict.fromkeys([*kinds, *self.engines.get(engine, {})]))

    def node_schema(self, kind, engine):
        variant = next((item for item in self.variants
                        if item.get(SchemaKeyword.PROPERTIES, {}).get(WorkflowNode.TYPE, {}).get(SchemaKeyword.CONST) == kind), None)
        if variant is None:
            variant = next((item for item in self.variants
                            if SchemaKeyword.CONST not in item.get(SchemaKeyword.PROPERTIES, {}).get(WorkflowNode.TYPE, {})), {})
        schema = deepcopy(variant)
        properties = schema.setdefault(SchemaKeyword.PROPERTIES, {})
        handler = self.engines.get(engine, {}).get(kind) if isinstance(kind, str) else None
        if isinstance(handler, dict):
            properties = {**deepcopy(handler.get(SchemaKeyword.PROPERTIES, {})), **properties}
            schema[SchemaKeyword.PROPERTIES] = properties
            schema[SchemaKeyword.REQUIRED] = list(dict.fromkeys([*schema.get(SchemaKeyword.REQUIRED, []), *handler.get(SchemaKeyword.REQUIRED, [])]))
        # Hidden fields stay in SchemaForm.original and survive every apply.
        for name in (WorkflowNode.TYPE, WorkflowNode.METADATA, WorkflowNode.BODY, WorkflowNode.CASES, BindingFields.INPUTS, BindingFields.OUTPUTS,
                     BindingFields.INPUT_SCHEMA, BindingFields.OUTPUT_SCHEMA, BindingFields.RESUME_SCHEMA):
            properties.pop(name, None)
        return schema

    def field_schema(self, kind, name):
        for item in self.variants:
            if item.get(SchemaKeyword.PROPERTIES, {}).get(WorkflowNode.TYPE, {}).get(SchemaKeyword.CONST) == kind:
                return deepcopy(item[SchemaKeyword.PROPERTIES].get(name, {}))
        return {}

    def graph_schema(self):
        schema = deepcopy(self.schema)
        for name in (WorkflowDocument.SCHEMA_VERSION, WorkflowDocument.NODES, WorkflowDocument.EDGES, WorkflowNode.METADATA):
            schema[SchemaKeyword.PROPERTIES].pop(name, None)
        return schema
