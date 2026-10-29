"""Tool input observations; backend validation still gates every effect."""

from copy import deepcopy
from dataclasses import replace
from math import floor
from typing import Any

from jsonschema import ValidationError
from llm.components.tools import Tool, ToolRegistry
from llm.components.tools.constraints import constrained_arguments
from llm.components.tools.builtin.file_values import FileReadArguments, FileListArguments, FileSearchArguments
from llm.components.tools.builtin.process_values import ProcessOutputArguments
from llm.core.policies import ArgumentConstraint, ConstraintMode
from llm.core.schema import SchemaKeyword
from llm.core.model_protocol import ToolDefinition
from llm.core.tool_contracts import ToolEffect


class FileReadSchema:
    """Declare the native reader's mutually exclusive modes in its schema."""

    CHARACTER = (FileReadArguments.offset.__name__, FileReadArguments.max_chars.__name__)
    LINE = (FileReadArguments.start_line.__name__, FileReadArguments.max_lines.__name__)
    TAIL = FileReadArguments.tail_lines.__name__

    @staticmethod
    def present(names: tuple[str, ...]) -> dict:
        return {SchemaKeyword.ANY_OF: [{SchemaKeyword.REQUIRED: [name]} for name in names]}

    @classmethod
    def parameters(cls, original: dict) -> dict:
        schema = deepcopy(original)
        schema.setdefault(SchemaKeyword.ALL_OF, []).extend([
            {SchemaKeyword.NOT: {SchemaKeyword.ALL_OF: [
                cls.present(cls.CHARACTER), cls.present((*cls.LINE, cls.TAIL))]}},
            {SchemaKeyword.NOT: {SchemaKeyword.ALL_OF: [
                cls.present((cls.TAIL,)), cls.present(cls.LINE)]}},
        ])
        return schema


class ReadPageSize:
    """Reduce declared read sizes before effects, without relaxing admission."""

    FIELDS = {
        FileReadArguments.NAME: (FileReadArguments.max_chars.__name__,),
        ProcessOutputArguments.NAME: (ProcessOutputArguments.max_chars.__name__,),
        FileListArguments.NAME: (FileListArguments.limit.__name__,),
        FileSearchArguments.NAME: (FileSearchArguments.limit.__name__, FileSearchArguments.max_chars.__name__),
    }

    @classmethod
    def bounded(cls, name, arguments, constraints):
        if name not in cls.FIELDS or not isinstance(arguments, dict):
            return None
        changes = {}
        for field in cls.FIELDS[name]:
            value = arguments.get(field)
            rule = (constraints or {}).get(name, {}).get(field)
            if type(value) is not int or rule is None:
                continue
            rule = ArgumentConstraint(rule)
            maximum = rule.value_schema().get(SchemaKeyword.MAXIMUM)
            if rule.mode == ConstraintMode.BOUNDED and maximum is not None and value > maximum:
                changes[field] = floor(maximum)
        # The full native schema and policy intersection still validate the
        # candidate. No paths, offsets, query, page modes or fixed values are repaired.
        return {**arguments, **changes} if changes else None


class ToolInputRegistry(ToolRegistry):
    """Preserve selections/contracts and explain rejected input before execution."""

    @classmethod
    def detached(cls, registry, *, adapt=False):
        """Copy validated definitions, never the runtime handlers or their state."""
        handlers = (registry.get(name).handler for name in registry.names())
        memo = {id(handler): handler for handler in handlers}
        if adapt:
            memo[id(ToolRegistry)] = cls
        return deepcopy(registry, memo)

    @classmethod
    def observe(cls, registry: ToolRegistry | None) -> ToolRegistry | None:
        # An application registry may own additional validation/binding hooks.
        # Do not flatten that authority into a copied standard registry.
        if registry is None or type(registry) is not ToolRegistry:
            return registry
        # Use Python's copy protocol to adapt an already validated snapshot.
        # Substituting the exact standard registry class in the copy memo keeps
        # its backend-owned state/layout intact without reading private fields
        # or registering every Tool again. Custom registries are left above.
        # Runtime handlers must retain identity; schemas remain detached.
        return cls.detached(registry, adapt=True)

    def select(self, names: tuple[str, ...]) -> "ToolInputRegistry":
        selected = super().select(names)
        return type(self).observe(selected)

    def definitions(self, *, names=None, constraints: dict | None = None) -> tuple[ToolDefinition, ...]:
        result = []
        for definition in super().definitions(names=names, constraints=constraints):
            fields = (constraints or {}).get(definition.name, {})
            if fields:
                schema = deepcopy(definition.parameters)
                fixed = []
                for key, value in fields.items():
                    rule = ArgumentConstraint(value)
                    field = schema[SchemaKeyword.PROPERTIES][key]
                    # Keep native allOf validation intact, but also expose the
                    # policy at the field's surface for provider/model readers.
                    field.update(rule.value_schema())
                    if rule.mode == ConstraintMode.FIXED:
                        fixed.append(key)
                        field[SchemaKeyword.ENUM] = rule.choices
                        field[SchemaKeyword.DESCRIPTION] = (
                            'Fixed by the current Tool argument policy. Use the sole enum/const '
                            'value exactly; this is not an upper bound.')
                # A provider must see the fields injected by the backend when
                # evaluating cross-field constraints (e.g. character vs line pages).
                if fixed:
                    schema[SchemaKeyword.REQUIRED] = list(dict.fromkeys([
                        *schema.get(SchemaKeyword.REQUIRED, ()), *fixed]))
                definition = replace(definition, parameters=schema)
            result.append(definition)
        return tuple(result)

    def prepare(self, name: str, arguments: dict, *, constraints: dict | None = None) -> tuple[Tool, dict[str, Any]]:
        candidate = None
        if name in ReadPageSize.FIELDS and name in self.names():
            if self.get(name).contract.effect == ToolEffect.READ_ONLY:
                candidate = ReadPageSize.bounded(name, arguments, constraints)
        if candidate is not None:
            # Loop validates calls before entering the Tool retry boundary.
            # A smaller read size is safe here; results retain truncation and
            # any native continuation offsets. Execution records admitted arguments.
            arguments = candidate
        try:
            return super().prepare(name, arguments, constraints=constraints)
        except ValueError as rejected:
            if name not in self.names():
                raise ValueError(f"Tool {name!r} is not available in this request "
                                 f"({len(self.names())} selected tools)") from rejected
            tool = self.get(name)
            try:
                constrained_arguments(tool.parameters, arguments, (constraints or {}).get(name, {}))
            except ValidationError as error:
                path = '.'.join(str(part) for part in error.absolute_path) or 'arguments'
                if error.validator == SchemaKeyword.REQUIRED:
                    missing = [key for key in error.validator_value
                               if isinstance(error.instance, dict) and key not in error.instance]
                    detail = 'missing required fields: ' + ', '.join(missing)
                elif error.validator == SchemaKeyword.TYPE:
                    detail = f'expected type {error.validator_value}'
                elif (error.validator == SchemaKeyword.ADDITIONAL_PROPERTIES
                      and isinstance(error.instance, dict)
                      and SchemaKeyword.PATTERN_PROPERTIES not in error.schema):
                    unknown = error.instance.keys() - error.schema.get(SchemaKeyword.PROPERTIES, {}).keys()
                    detail = 'unknown fields: ' + ', '.join(sorted(unknown))
                elif error.validator == SchemaKeyword.NOT and name == FileReadArguments.NAME:
                    detail = ('character paging (offset/max_chars) cannot be combined with '
                              'start_line/max_lines/tail_lines; tail_lines also excludes start_line/max_lines')
                elif error.validator == SchemaKeyword.CONST:
                    detail = ('must equal the fixed value in the Tool schema; '
                              'a fixed value is not an upper bound')
                else:
                    detail = f'violates {error.validator}; check the Tool schema and argument constraints'
                raise ValueError(f"Tool {name!r}, {path}: {detail}") from rejected
            except (ValueError, TypeError):
                pass  # Preserve rejection when input is not a valid JSON object.
            raise ValueError(f"Tool {name!r}: {self.INVALID_ARGUMENTS}") from rejected
