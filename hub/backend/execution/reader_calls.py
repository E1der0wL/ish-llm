"""Read hints checked against the selected Tool schema and Run constraints."""

from jsonschema import Draft202012Validator
from llm.core.policies import ArgumentConstraint, ConstraintMode


class ReaderCalls:
    @staticmethod
    def create(context, request, name, arguments, *, limit_field=None):
        definition = next((tool for tool in request.tools if tool.name == name), None)
        if definition is None:
            return None
        arguments = dict(arguments)
        scope = context.tool_scope if context is not None else None
        constraints = scope.argument_constraints if scope is not None else {}
        # Use a host-fixed page size exactly; never recommend a conflicting size.
        rule = constraints.get(name, {}).get(limit_field)
        if rule is not None and ArgumentConstraint(rule).mode == ConstraintMode.FIXED:
            arguments[limit_field] = ArgumentConstraint(rule).choices[0]
        if not Draft202012Validator(definition.parameters).is_valid(arguments):
            return None
        if context is not None and context.tools is not None:
            try:
                _, arguments = context.tools.prepare(name, arguments, constraints=constraints)
            except ValueError:
                return None
        return {'tool': name, 'arguments': arguments}
