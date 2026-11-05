"""Read-only draft diagnostics from the existing structural and handler contracts.

This never calls a handler's runtime validator or prepares a Tool/Agent. A caller
supplies declarations and an optional metadata reader; runtime admission is separate.
"""

from copy import deepcopy
from dataclasses import dataclass, fields
from jsonschema import Draft202012Validator

from llm.core.contracts import Diagnostic, JsonValue, ResourceRef
from llm.core.resource_references import ResourceTarget
from llm.core.json_data import JsonPointer
from llm.core.configuration import UNSET
from .graph import (ControlNodeType, WorkflowHandlerContract, COMMON_NODE_FIELDS,
                    validate_graph, workflow_schema, WorkflowStructureError)
from .contracts import WorkflowDocument, WorkflowNode


@dataclass(frozen=True, slots=True)
class InspectedDefinition(JsonValue):
    """One effect-free metadata read; a missing revision is explicitly unverified."""
    INVALID = 'Invalid inspected definition snapshot'

    data: dict
    revision: str | None

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if not isinstance(self.data, dict) or self.revision is not None and (
                not isinstance(self.revision, str) or not self.revision):
            raise ValueError(InspectedDefinition.INVALID)


@dataclass(frozen=True, slots=True)
class WorkflowInspection(JsonValue):
    INVALID_REFERENCE = 'Workflow definition references require a record identifier'
    INVALID_READER = 'Metadata readers must return InspectedDefinition'
    MISSING_REFERENCE = 'Workflow reference is required'
    CYCLIC_REFERENCE = 'Nested Workflow reference cycle'
    UNREGISTERED_HANDLER = 'Unregistered handler: {kind}'

    SCHEMA_INVALID = 'workflow_schema_invalid'
    STRUCTURE_INVALID = 'workflow_structure_invalid'
    REFERENCE_INVALID = 'workflow_reference_invalid'
    REFERENCE_CYCLE = 'workflow_reference_cycle'
    HANDLER_MISSING = 'workflow_handler_missing'
    HANDLER_INVALID = 'workflow_handler_invalid'
    DYNAMIC_CHECKS = ('runtime_authorization', 'provider_availability', 'dynamic_inputs')
    REFERENCE_UNCHECKED = ': reference'
    SCHEMA_UNCHECKED = ': handler schema'
    REFERENCES_UNCHECKED = ': handler references'
    REVISION_UNCHECKED = '{component}/{identifier}: reference revision'

    diagnostics: list[Diagnostic]
    unverified: list[str]
    references: list[ResourceRef]

    @property
    def valid(self):
        """Static validity, never execution permission or proof of dynamic checks."""
        return not self.diagnostics


@dataclass(frozen=True, slots=True)
class InspectionLocation:
    path: str
    owner: str
    node_id: object = UNSET
    edge_index: object = UNSET
    workflow_path: object = UNSET

    @classmethod
    def from_path(cls, path, owner):
        location = cls(JsonPointer.from_parts(path), owner)
        from dataclasses import replace
        for index, part in enumerate(path[:-1]):
            if part == WorkflowDocument.NODES:
                location = replace(location, node_id=path[index + 1], workflow_path=list(path[:index]))
            elif part == WorkflowDocument.EDGES and type(path[index + 1]) is int:
                location = replace(location, edge_index=path[index + 1], workflow_path=list(path[:index]))
        return location

    def to_dict(self):
        return {item.name: deepcopy(getattr(self, item.name)) for item in fields(self)
                if getattr(self, item.name) is not UNSET}


def inspect_graph(definition, *, handlers, read_reference=None):
    """Inspect a detached draft. Private action fields belong to the selected handler.

    read_reference(ResourceTarget) returns an InspectedDefinition using an
    explicitly effect-free, record-level metadata lookup.
    A missing reader leaves references unverified, rather than silently accepting them.
    """
    data = deepcopy(definition)
    issues, unchecked = [], list(WorkflowInspection.DYNAMIC_CHECKS)
    observed, loaded = {}, {}
    reported = set()

    def read(target):
        if not isinstance(target, ResourceTarget) or target.identifier is None:
            raise ValueError(WorkflowInspection.INVALID_REFERENCE)
        key = target.component, target.identifier
        if key not in loaded:
            snapshot = read_reference(target)
            if not isinstance(snapshot, InspectedDefinition):
                raise TypeError(WorkflowInspection.INVALID_READER)
            loaded[key] = snapshot.data
            observed[key] = ResourceRef(target.component, target.identifier,
                                       component=target.component, version=snapshot.revision)
            if snapshot.revision is None:
                unchecked.append(WorkflowInspection.REVISION_UNCHECKED.format(
                    component=target.component, identifier=target.identifier))
        return deepcopy(loaded[key])

    def issue(code, message, path, owner):
        identity = code, message, path, owner
        if identity in reported:
            return
        reported.add(identity)
        # Locations come from the structured validator path, never its message.
        issues.append(Diagnostic(code, message, details=InspectionLocation.from_path(path, owner).to_dict()))

    def inspect(document, path, references):
        schema_errors = list(Draft202012Validator(workflow_schema()).iter_errors(document))
        if schema_errors:
            for error in schema_errors:
                issue(WorkflowInspection.SCHEMA_INVALID, error.message, (*path, *error.absolute_path), WorkflowDocument.COMPONENT)
            return
        try:
            validate_graph(document)
        except (TypeError, ValueError) as error:
            location = error.path if isinstance(error, WorkflowStructureError) else ()
            issue(WorkflowInspection.STRUCTURE_INVALID, str(error), (*path, *location), WorkflowDocument.COMPONENT)
            # Fields satisfy the schema; independent action declarations can still be inspected.
        for name, node in document[WorkflowDocument.NODES].items():
            node_path = (*path, WorkflowDocument.NODES, name)
            kind = node[WorkflowNode.TYPE]
            if kind == ControlNodeType.LOOP:
                # A draft may fail structural validation while independent
                # action declarations remain inspectable. Missing bodies are
                # invalid schema inputs, never invented empty workflows.
                inspect(node.get(WorkflowNode.BODY), (*node_path, WorkflowNode.BODY), references)
                continue
            if kind == ControlNodeType.WORKFLOW:
                if WorkflowNode.WORKFLOW not in node:
                    issue(WorkflowInspection.REFERENCE_INVALID, WorkflowInspection.MISSING_REFERENCE,
                          (*node_path, WorkflowNode.WORKFLOW), WorkflowDocument.COMPONENT)
                    continue
                target = ResourceTarget(WorkflowDocument.COMPONENT, node[WorkflowNode.WORKFLOW])
                if target.identifier in references:
                    issue(WorkflowInspection.REFERENCE_CYCLE, WorkflowInspection.CYCLIC_REFERENCE, node_path, WorkflowDocument.COMPONENT)
                elif read_reference is None:
                    unchecked.append(JsonPointer.from_parts(node_path) + WorkflowInspection.REFERENCE_UNCHECKED)
                else:
                    try:
                        child = read(target)
                    except (KeyError, FileNotFoundError, ValueError) as error:
                        issue(WorkflowInspection.REFERENCE_INVALID, str(error), node_path, WorkflowDocument.COMPONENT)
                    else:
                        inspect(child, (*node_path, WorkflowNode.WORKFLOW), (*references, target.identifier))
                continue
            if kind in ControlNodeType:
                continue
            handler = handlers.get(kind)
            if handler is None:
                issue(WorkflowInspection.HANDLER_MISSING, WorkflowInspection.UNREGISTERED_HANDLER.format(kind=kind), node_path, kind)
                continue
            schema = WorkflowHandlerContract.describe_snapshot(handler)
            if schema is None:
                unchecked.append(JsonPointer.from_parts(node_path) + WorkflowInspection.SCHEMA_UNCHECKED)
                continue
            Draft202012Validator.check_schema(schema)
            options = {key: value for key, value in node.items() if key not in COMMON_NODE_FIELDS}
            errors = list(Draft202012Validator(schema).iter_errors(options))
            for error in errors:
                issue(WorkflowInspection.HANDLER_INVALID, error.message, (*node_path, *error.absolute_path), kind)
            if errors:
                continue
            reader = getattr(handler, WorkflowHandlerContract.REFERENCES_METHOD, None)
            if reader is None:
                unchecked.append(JsonPointer.from_parts(node_path) + WorkflowInspection.REFERENCES_UNCHECKED)
                continue
            for target in reader(deepcopy(node)):
                if read_reference is None:
                    unchecked.append(JsonPointer.from_parts(node_path) + WorkflowInspection.REFERENCE_UNCHECKED)
                else:
                    try:
                        read(target)
                    except (KeyError, FileNotFoundError, ValueError) as error:
                        issue(WorkflowInspection.REFERENCE_INVALID, str(error), node_path, kind)

    inspect(data, (), ())
    return WorkflowInspection(issues, list(dict.fromkeys(unchecked)), list(observed.values()))
