"""Selected condition implementation boundary; no policy values or execution effects."""

import asyncio
import inspect
from abc import ABC, abstractmethod
from copy import deepcopy

from jsonschema import Draft202012Validator

from llm.core.approval import ApprovalCondition
from llm.core.json_data import JsonObject
from llm.core.schema import checked_schema, open_schema, OpenSchemaKind


class ApprovalEvaluator(ABC):
    """Trusted, effect-free evaluator. Config and implementation revision are explicit.

    evaluate may be synchronous or async. The runtime invokes it outside storage
    ownership and revalidates authority before committing a response. Implementations
    must change revision when their decision semantics change.
    """

    def __init__(self, *, revision: str):
        if not isinstance(revision, str) or not revision:
            raise ValueError('Approval evaluator requires an explicit revision')
        self.revision = revision

    @abstractmethod
    def describe_config(self):
        raise NotImplementedError

    def validate_config(self, config):
        JsonObject.validate(config)
        Draft202012Validator(checked_schema(self.describe_config())).validate(config)

    @abstractmethod
    def evaluate(self, request, config):
        raise NotImplementedError


class SchemaApprovalEvaluator(ApprovalEvaluator):
    """Application-supplied JSON Schema matches the public InteractionRequest JSON.

    This evaluator is available only when a host explicitly registers and a Project
    explicitly selects it. It does not choose matching fields, patterns or limits.
    """

    def describe_config(self):
        return open_schema('JSON Schema', category=OpenSchemaKind.SCHEMA)

    def validate_config(self, config):
        super().validate_config(config)
        checked_schema(config)

    def evaluate(self, request, config):
        return Draft202012Validator(config).is_valid(request.to_dict())


def resolve_evaluator(evaluators, condition: ApprovalCondition):
    evaluator = evaluators.get(condition.implementation)
    if not isinstance(evaluator, ApprovalEvaluator):
        raise ValueError(f'Approval evaluator unavailable: {condition.implementation}')
    if evaluator.revision != condition.revision:
        raise ValueError(f'Approval evaluator revision changed: {condition.implementation}')
    evaluator.validate_config(deepcopy(condition.config))
    return evaluator


async def evaluate_approval(evaluator, request, condition):
    result = await asyncio.to_thread(evaluator.evaluate, deepcopy(request), deepcopy(condition.config))
    if inspect.isawaitable(result):
        result = await result
    if type(result) is not bool:
        raise TypeError('Approval evaluator must return a boolean')
    return result
