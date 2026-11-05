"""Project-owned remembered rule mutations, called under ProjectManager ownership.

The Project configuration is the only rule store. Interaction records remain the
only individual decision store; these functions do not execute or resume work.
"""

from dataclasses import replace

from llm.core.approval import (
    ApprovalOrigin, RememberedApproval, RememberRequest,
)
from llm.core.interactions import InteractionRequest, InteractionRisk
from llm.core.json_data import JsonValue
from llm.core.models import now
from llm.core.policies import ApprovalPolicy, ApprovalRule
from llm.services.runtime.approval import resolve_evaluator


MANAGED_RULES = 'Remembered approvals must be changed through approval APIs'
FOREIGN_RULE = 'Remembered approval belongs to another Project'
FOREIGN_REQUEST = 'Remembered approval scope does not own the request'
MISMATCHED_REQUEST = 'Remembered approval requires a matching approval request'
UNKNOWN_REQUIRED = 'Explicit unknown allowance requires unknown risk'
KNOWN_REQUIRED = 'Threshold allowance requires known risk'
MISMATCHED_SCHEME = 'Threshold allowance requires the Project risk scheme'
UNCERTAIN_RETRY = 'Uncertain retries cannot create remembered approval rules'
DUPLICATE_RULE = 'Approval rule ID already exists; revoked IDs cannot be reused'
UNKNOWN_RULE = 'Unknown remembered approval rule'
CHANGED_REVISION = 'Approval rule revision changed; reload before editing'
REVOKED_RULE = 'Revoked approval rules cannot be reactivated'

def approval_rules(config):
    return ApprovalPolicy.from_policies(config.policies).rules


def protect_approvals(previous, candidate):
    """Ordinary config edits cannot forge, drop or rewrite managed grant evidence."""
    before = {rule.id: rule.to_dict() for rule in approval_rules(previous) if rule.remembered is not None}
    after = {rule.id: rule.to_dict() for rule in approval_rules(candidate) if rule.remembered is not None}
    if not JsonValue.same(before, after):
        raise ValueError(MANAGED_RULES)


def validate_approvals(project, evaluators):
    for rule in approval_rules(project.config):
        memory = rule.remembered
        if memory is None:
            continue
        if memory.scope.project_id != project.id:
            raise ValueError(FOREIGN_RULE)
        if memory.revoked_at is None:
            resolve_evaluator(evaluators, memory.condition)


def _save(manager, project, rules):
    project.config.configure_policies(ApprovalPolicy.rules_update(rules))
    manager._validate_config(project)
    manager.repository.save(project)


def remember_approval(manager, project, session, run, request, spec):
    spec = RememberRequest.read(spec)
    current = manager.access.require(project)
    if session.project_id != current.id or run.session_id != session.id:
        raise ValueError(FOREIGN_REQUEST)
    policy = ApprovalPolicy.from_policies(current.config.policies)
    explicit_unknown = spec.rule.basis == ApprovalRule.EXPLICIT_UNKNOWN
    if request.kind != InteractionRequest.KIND_APPROVAL or request.category != spec.rule.category:
        raise ValueError(MISMATCHED_REQUEST)
    if explicit_unknown:
        if request.risk is not InteractionRisk.UNKNOWN:
            raise ValueError(UNKNOWN_REQUIRED)
    else:
        if request.risk is InteractionRisk.UNKNOWN:
            raise ValueError(KNOWN_REQUIRED)
        if request.risk_scheme != policy.risk_scheme:
            raise ValueError(MISMATCHED_SCHEME)
    from llm.services.runtime.interactions import InteractionRepository
    if request.category == InteractionRepository.RETRY_CATEGORY:
        raise ValueError(UNCERTAIN_RETRY)
    if any(rule.id == spec.rule.id for rule in policy.rules):
        raise ValueError(DUPLICATE_RULE)
    condition = spec.condition
    resolve_evaluator(manager.approval_evaluators, condition)
    memory = RememberedApproval(
        revision=RememberedApproval.INITIAL_REVISION, scope=spec.bound_scope(current.id, session.id),
        condition=condition,
        origin=ApprovalOrigin(run.id, request.id, request.revision, request.fingerprint, now()),
        revoked_at=RememberedApproval.NOT_REVOKED,
    )
    rule = replace(spec.rule, remembered=memory)
    _save(manager, current, [*policy.rules, rule])
    return rule.to_dict()


def _selected(manager, project, identifier, expected_revision):
    current = manager.access.require(project)
    rules = approval_rules(current.config)
    rule = next((item for item in rules if item.id == identifier), None)
    if rule is None or rule.remembered is None:
        raise ValueError(UNKNOWN_RULE)
    if rule.remembered.scope.project_id != current.id:
        raise ValueError(FOREIGN_RULE)
    if type(expected_revision) is not int or rule.remembered.revision != expected_revision:
        raise ValueError(CHANGED_REVISION)
    return current, rules, rule


def update_approval(manager, project, identifier, changes, *, expected_revision):
    current, rules, rule = _selected(manager, project, identifier, expected_revision)
    if rule.remembered.revoked_at is not None:
        raise ValueError(REVOKED_RULE)
    replacement = rule.updated(changes)
    resolve_evaluator(manager.approval_evaluators, replacement.remembered.condition)
    _save(manager, current, [replacement if item.id == identifier else item for item in rules])
    return replacement.to_dict()


def revoke_approval(manager, project, identifier, *, expected_revision):
    current, rules, rule = _selected(manager, project, identifier, expected_revision)
    if rule.remembered.revoked_at is not None:
        return rule.to_dict()
    replacement = replace(rule, remembered=rule.remembered.revoke(now()))
    # Withdrawal must remain available when a host evaluator is unavailable.
    # The normal Project policy validator still checks the complete stored shape.
    current.config.configure_policies(ApprovalPolicy.rules_update(
        replacement if item.id == identifier else item for item in rules))
    manager.repository.save(current)
    return replacement.to_dict()
