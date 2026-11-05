"""Hub chooses explicit matching conditions; llm owns grants and persistence."""

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid5, NAMESPACE_URL

from llm.core.approval import ApprovalCondition, RememberRequest, RememberScope
from llm.core.schema import SchemaKeyword as K, SchemaType as T
from llm.core.interactions import InteractionOption, InteractionRequest
from llm.core.policies import ApprovalRule
from llm.components.tools.builtin.process_values import ProcessInvocation
from llm.services.runtime.approval import SchemaApprovalEvaluator
from llm.services.runtime.interactions import InteractionRepository

from ...locales.keys import MessageKey
from .command_range import CommandRangeApproval
from .approval_matching import MatchedToolAction


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
    ACTION_FIELDS = MatchedToolAction.names()
    UNAVAILABLE = 'This interaction cannot create a remembered Tool approval rule'
    INVALID_ARGUMENTS = 'Tool arguments must be a JSON object'
    ENVIRONMENT_CHANGED = 'The working directory and environment cannot be changed in this rule'
    REQUEST_KEY = 'hub-approval/{id}/{revision}'
    RULE_KEY = '{key}/{scope}/{match}'

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
        properties = {key: {K.CONST: value} for key, value in MatchedToolAction.capture(action).to_dict().items()}
        return ApprovalCondition(cls.IMPLEMENTATION, cls.REVISION, {
            K.TYPE: T.OBJECT, K.REQUIRED: [InteractionRequest.action.__name__],
            K.PROPERTIES: {InteractionRequest.action.__name__: {
                K.TYPE: T.OBJECT, K.REQUIRED: list(properties), K.PROPERTIES: properties,
            }},
        })

    @classmethod
    def remember(cls, request, selection):
        if not cls.available(request):
            raise ValueError(cls.UNAVAILABLE)
        scope = RememberScope(selection.scope)
        match = ApprovalMatch(selection.match)
        action = MatchedToolAction.capture(request.action)
        condition = (CommandRangeApproval.condition(action, selection.prefixes)
                     if match == ApprovalMatch.COMMAND_RANGE else cls.exact_condition(action))
        rule = ApprovalRule(uuid5(NAMESPACE_URL, cls.RULE_KEY.format(
            key=cls.request_key(request), scope=scope, match=match)).hex, request.category,
            max_risk=request.risk, basis=ApprovalRule.EXPLICIT_UNKNOWN if request.risk is None else None)
        return {**rule.to_dict(), RememberRequest.scope.__name__: scope.value,
                RememberRequest.condition.__name__: condition.to_dict()}

    @staticmethod
    def request_key(request):
        return ToolApprovalConditions.REQUEST_KEY.format(id=request.id, revision=request.revision)

    @classmethod
    def exact_action(cls, condition):
        """Only the exact condition generated here has an editable Hub form."""
        if (condition.implementation, condition.revision) != (cls.IMPLEMENTATION, cls.REVISION):
            return None
        properties = condition.config.get(K.PROPERTIES, {}).get(
            InteractionRequest.action.__name__, {}).get(K.PROPERTIES, {})
        if not all(isinstance(properties.get(key), dict) and set(properties[key]) == {K.CONST}
                   for key in cls.ACTION_FIELDS):
            return None
        action = MatchedToolAction.from_dict({key: deepcopy(properties[key][K.CONST]) for key in cls.ACTION_FIELDS})
        return action if cls.exact_condition(action) == condition else None

    @staticmethod
    def replace_arguments(action, arguments):
        """The management editor retains the approval's execution environment."""
        if not isinstance(arguments, dict):
            raise TypeError(ToolApprovalConditions.INVALID_ARGUMENTS)
        action = MatchedToolAction.capture(action)
        original = action.arguments
        for key in (ProcessInvocation.cwd.__name__, ProcessInvocation.env.__name__):
            if (key in original) != (key in arguments) or original.get(key) != arguments.get(key):
                raise ValueError(ToolApprovalConditions.ENVIRONMENT_CHANGED)
        return action.with_arguments(arguments)


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
