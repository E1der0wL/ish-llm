"""Read hints checked against the selected Tool schema and Run constraints."""

from jsonschema import Draft202012Validator
from math import ceil, floor
from llm.core.policies import ArgumentConstraint, ConstraintMode
from llm.core.schema import SchemaKeyword


class ReaderCalls:
    @staticmethod
    def create(context, request, name, arguments, *, limit_field=None):
        definition = next((tool for tool in request.tools if tool.name == name), None)
        if definition is None:
            return None
        arguments = dict(arguments)
        scope = context.tool_scope if context is not None else None
        constraints = scope.argument_constraints if scope is not None else {}
        # These are new read suggestions, never repairs to a model's invocation.
        # Respect both explicit fixed pages and a narrower admitted range.
        rule = constraints.get(name, {}).get(limit_field)
        if rule is not None:
            rule = ArgumentConstraint(rule)
            if rule.mode == ConstraintMode.FIXED:
                arguments[limit_field] = rule.choices[0]
            elif rule.mode == ConstraintMode.BOUNDED and limit_field in arguments:
                bounds = rule.value_schema()
                value = arguments[limit_field]
                value = max(value, ceil(bounds.get(SchemaKeyword.MINIMUM, value)))
                arguments[limit_field] = min(value, floor(bounds.get(SchemaKeyword.MAXIMUM, value)))
        if not Draft202012Validator(definition.parameters).is_valid(arguments):
            return None
        if context is not None and context.tools is not None:
            try:
                _, arguments = context.tools.prepare(name, arguments, constraints=constraints)
            except ValueError:
                return None
        return {'tool': name, 'arguments': arguments}
