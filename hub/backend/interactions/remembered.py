"""Hub chooses explicit matching conditions; llm owns grants and persistence."""

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid5, NAMESPACE_URL

from llm.core.approval import ApprovalCondition
from llm.core.interactions import InteractionOption, InteractionRequest
from llm.core.policies import ApprovalRule
from llm.components.tools.builtin.process_values import ProcessInvocation
from llm.services.runtime.approval import SchemaApprovalEvaluator
from llm.services.runtime.tool_approval_records import ToolApprovalAction
from llm.services.runtime.interactions import InteractionRepository

from ...locales.keys import MessageKey
from .command_range import CommandRangeApproval


class RememberScope(StrEnum):
    SESSION = 'session'
    PROJECT = 'project'


class ApprovalMatch(StrEnum):
    EXACT = 'exact'
    COMMAND_RANGE = 'command_range'


@dataclass(frozen=True, slots=True)
class ApprovalSelection:
    scope: RememberScope
    match: ApprovalMatch
    prefixes: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class ApprovalChoice:
    option_id: str
    label: str
    match: ApprovalMatch | None = None


class ToolApprovalConditions:
    """Build explicit Hub conditions without owning approval authority.

    Classification is intentionally not a condition: llm checks unknown/known
    risk independently. Only a confirmed shell range can vary command arguments.
    """

    IMPLEMENTATION = 'hub-exact-tool-action'
    REVISION = '1'
    ACTION_FIELDS = tuple(field.__name__ for field in (
        ToolApprovalAction.tool, ToolApprovalAction.arguments,
        ToolApprovalAction.contract, ToolApprovalAction.policy,
    ))

    @classmethod
    def evaluators(cls):
        return {cls.IMPLEMENTATION: SchemaApprovalEvaluator(revision=cls.REVISION),
                CommandRangeApproval.IMPLEMENTATION: CommandRangeApproval()}

    @classmethod
    def available(cls, request):
        return (request.kind == InteractionRequest.KIND_APPROVAL
                and request.category != InteractionRepository.RETRY_CATEGORY
                and isinstance(request.action, dict)
                and all(key in request.action for key in cls.ACTION_FIELDS)
                and request.find_approval() is not None)

    @classmethod
    def exact_condition(cls, action):
        properties = {key: {'const': deepcopy(action[key])} for key in cls.ACTION_FIELDS}
        return ApprovalCondition(cls.IMPLEMENTATION, cls.REVISION, {
            'type': 'object', 'required': [InteractionRequest.action.__name__],
            'properties': {InteractionRequest.action.__name__: {
                'type': 'object', 'required': list(properties), 'properties': properties,
            }},
        })

    @classmethod
    def remember(cls, request, selection):
        if not cls.available(request):
            raise ValueError('This interaction cannot create a remembered Tool approval rule')
        scope = RememberScope(selection.scope)
        match = ApprovalMatch(selection.match)
        action = {key: deepcopy(request.action[key]) for key in cls.ACTION_FIELDS}
        condition = (CommandRangeApproval.condition(action, selection.prefixes)
                     if match == ApprovalMatch.COMMAND_RANGE else cls.exact_condition(action))
        rule = {
            'id': uuid5(NAMESPACE_URL, f'{cls.request_key(request)}/{scope}/{match}').hex,
            'category': request.category,
            'scope': scope.value,
            'condition': condition.to_dict(),
        }
        if request.risk is None:
            rule['basis'] = ApprovalRule.EXPLICIT_UNKNOWN
        else:
            rule['max_risk'] = request.risk
        return rule

    @staticmethod
    def request_key(request):
        return f'hub-approval/{request.id}/{request.revision}'

    @classmethod
    def exact_action(cls, condition):
        """Only the exact condition generated here has an editable Hub form."""
        if (condition.implementation, condition.revision) != (cls.IMPLEMENTATION, cls.REVISION):
            return None
        properties = condition.config.get('properties', {}).get(
            InteractionRequest.action.__name__, {}).get('properties', {})
        if not all(isinstance(properties.get(key), dict) and set(properties[key]) == {'const'}
                   for key in cls.ACTION_FIELDS):
            return None
        action = {key: deepcopy(properties[key]['const']) for key in cls.ACTION_FIELDS}
        return action if cls.exact_condition(action) == condition else None

    @staticmethod
    def replace_arguments(action, arguments):
        """The management editor retains the approval's execution environment."""
        if not isinstance(arguments, dict):
            raise TypeError('Tool arguments must be a JSON object')
        original = action['arguments']
        for key in (ProcessInvocation.cwd.__name__, ProcessInvocation.env.__name__):
            if (key in original) != (key in arguments) or original.get(key) != arguments.get(key):
                raise ValueError('The working directory and environment cannot be changed in this rule')
        return {**action, 'arguments': deepcopy(arguments)}


def approval_choices(request, t):
    """Matching choices map to a backend approve option after explicit review."""
    result = []
    for option in request.options:
        result.append(ApprovalChoice(option.id,
            t(MessageKey.APPROVAL_ONCE) if option.effect == InteractionOption.EFFECT_APPROVE
            else t(MessageKey.APPROVAL_DECLINE) if option.effect == InteractionOption.EFFECT_DENY
            else option.label))
        if option.effect == InteractionOption.EFFECT_APPROVE and ToolApprovalConditions.available(request):
            result.append(ApprovalChoice(option.id, t(MessageKey.APPROVAL_REMEMBER), ApprovalMatch.EXACT))
    return result
