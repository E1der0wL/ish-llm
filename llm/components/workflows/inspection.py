"""Read-only draft diagnostics from the existing structural and handler contracts.

This never calls a handler's runtime validator or prepares a Tool/Agent. A caller
supplies declarations and an optional metadata reader; runtime admission is separate.
"""

from copy import deepcopy
from dataclasses import dataclass
from jsonschema import Draft202012Validator

from llm.core.contracts import Diagnostic, JsonValue, ResourceRef
from llm.core.resource_references import ResourceTarget
from .graph import (ControlNodeType, WorkflowHandlerContract, COMMON_NODE_FIELDS,
                    validate_graph, workflow_schema, WorkflowStructureError)
from .contracts import WorkflowDocument, WorkflowNode


@dataclass(frozen=True, slots=True)
class InspectedDefinition(JsonValue):
    """One effect-free metadata read; a missing revision is explicitly unverified."""
    data: dict
    revision: str | None

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if not isinstance(self.data, dict) or self.revision is not None and (
                not isinstance(self.revision, str) or not self.revision):
            raise ValueError('Invalid inspected definition snapshot')


@dataclass(frozen=True, slots=True)
class WorkflowInspection(JsonValue):
    diagnostics: list[Diagnostic]
    unverified: list[str]
    references: list[ResourceRef]

    @property
    def valid(self):
        """Static validity, never execution permission or proof of dynamic checks."""
        return not self.diagnostics


def pointer(parts):
    return ''.join('/' + str(part).replace('~', '~0').replace('/', '~1') for part in parts)


def inspect_graph(definition, *, handlers, read_reference=None):
    """Inspect a detached draft. Private action fields belong to the selected handler.

    read_reference(ResourceTarget) returns an InspectedDefinition using an
    explicitly effect-free, record-level metadata lookup.
    A missing reader leaves references unverified, rather than silently accepting them.
    """
    data = deepcopy(definition)
    issues, unchecked = [], ['runtime_authorization', 'provider_availability', 'dynamic_inputs']
    observed, loaded = {}, {}
    reported = set()

    def read(target):
        if not isinstance(target, ResourceTarget) or target.identifier is None:
            raise ValueError('Workflow definition references require a record identifier')
        key = target.component, target.identifier
        if key not in loaded:
            snapshot = read_reference(target)
            if not isinstance(snapshot, InspectedDefinition):
                raise TypeError('Metadata readers must return InspectedDefinition')
            loaded[key] = snapshot.data
            observed[key] = ResourceRef(target.component, target.identifier,
                                       component=target.component, version=snapshot.revision)
            if snapshot.revision is None:
                unchecked.append(f'{target.component}/{target.identifier}: reference revision')
        return deepcopy(loaded[key])

    def issue(code, message, path, owner):
        identity = code, message, path, owner
        if identity in reported:
            return
        reported.add(identity)
        details = {'path': pointer(path), 'owner': owner}
        # Locations come from the structured validator path, never its message.
        for index, part in enumerate(path[:-1]):
            if part == WorkflowDocument.NODES:
                details.update(node_id=path[index + 1], workflow_path=list(path[:index]))
            elif part == WorkflowDocument.EDGES and type(path[index + 1]) is int:
                details.update(edge_index=path[index + 1], workflow_path=list(path[:index]))
        issues.append(Diagnostic(code, message, details=details))

    def inspect(document, path, references):
        schema_errors = list(Draft202012Validator(workflow_schema()).iter_errors(document))
        if schema_errors:
            for error in schema_errors:
                issue('workflow_schema_invalid', error.message, (*path, *error.absolute_path), 'workflows')
            return
        try:
            validate_graph(document)
        except (TypeError, ValueError) as error:
            location = error.path if isinstance(error, WorkflowStructureError) else ()
            issue('workflow_structure_invalid', str(error), (*path, *location), 'workflows')
            # Fields satisfy the schema; independent action declarations can still be inspected.
        for name, node in document[WorkflowDocument.NODES].items():
            node_path = (*path, WorkflowDocument.NODES, name)
            kind = node[WorkflowNode.TYPE]
            if kind == ControlNodeType.LOOP:
                inspect(node[WorkflowNode.BODY], (*node_path, WorkflowNode.BODY), references)
                continue
            if kind == ControlNodeType.WORKFLOW:
                target = ResourceTarget(WorkflowDocument.COMPONENT, node[WorkflowNode.WORKFLOW])
                if target.identifier in references:
                    issue('workflow_reference_cycle', 'Nested Workflow reference cycle', node_path, 'workflows')
                elif read_reference is None:
                    unchecked.append(pointer(node_path) + ': reference')
                else:
                    try:
                        child = read(target)
                    except (KeyError, FileNotFoundError, ValueError) as error:
                        issue('workflow_reference_invalid', str(error), node_path, 'workflows')
                    else:
                        inspect(child, (*node_path, WorkflowNode.WORKFLOW), (*references, target.identifier))
                continue
            if kind in ControlNodeType:
                continue
            handler = handlers.get(kind)
            if handler is None:
                issue('workflow_handler_missing', f'Unregistered handler: {kind}', node_path, kind)
                continue
            schema = WorkflowHandlerContract.describe_snapshot(handler)
            if schema is None:
                unchecked.append(pointer(node_path) + ': handler schema')
                continue
            Draft202012Validator.check_schema(schema)
            options = {key: value for key, value in node.items() if key not in COMMON_NODE_FIELDS}
            errors = list(Draft202012Validator(schema).iter_errors(options))
            for error in errors:
                issue('workflow_handler_invalid', error.message, (*node_path, *error.absolute_path), kind)
            if errors:
                continue
            reader = getattr(handler, WorkflowHandlerContract.REFERENCES_METHOD, None)
            if reader is None:
                unchecked.append(pointer(node_path) + ': handler references')
                continue
            for target in reader(deepcopy(node)):
                if read_reference is None:
                    unchecked.append(pointer(node_path) + ': reference')
                else:
                    try:
                        read(target)
                    except (KeyError, FileNotFoundError, ValueError) as error:
                        issue('workflow_reference_invalid', str(error), node_path, kind)

    inspect(data, (), ())
    return WorkflowInspection(issues, list(dict.fromkeys(unchecked)), list(observed.values()))
