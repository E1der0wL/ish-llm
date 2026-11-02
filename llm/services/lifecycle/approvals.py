"""Project-owned remembered rule mutations, called under ProjectManager ownership.

The Project configuration is the only rule store. Interaction records remain the
only individual decision store; these functions do not execute or resume work.
"""

from copy import deepcopy
from dataclasses import replace

from jsonschema import Draft202012Validator

from llm.core.approval import (
    ApprovalCondition, ApprovalScope, ApprovalOrigin, RememberedApproval, remember_schema,
)
from llm.core.interactions import InteractionRequest
from llm.core.json_data import JsonValue
from llm.core.models import now
from llm.core.policies import ApprovalPolicy, ApprovalRule
from llm.services.runtime.approval import resolve_evaluator


def approval_rules(config):
    return ApprovalPolicy.from_policies(config.policies).rules


def protect_approvals(previous, candidate):
    """Ordinary config edits cannot forge, drop or rewrite managed grant evidence."""
    before = {rule.id: rule.to_dict() for rule in approval_rules(previous) if rule.remembered is not None}
    after = {rule.id: rule.to_dict() for rule in approval_rules(candidate) if rule.remembered is not None}
    if not JsonValue.same(before, after):
        raise ValueError('Remembered approvals must be changed through approval APIs')


def validate_approvals(project, evaluators):
    for rule in approval_rules(project.config):
        memory = rule.remembered
        if memory is None:
            continue
        if memory.scope.project_id != project.id:
            raise ValueError('Remembered approval belongs to another Project')
        if memory.revoked_at is None:
            resolve_evaluator(evaluators, memory.condition)


def _save(manager, project, rules):
    project.config.configure_policies({'approval': {'rules': [rule.to_dict() for rule in rules]}})
    manager._validate_config(project)
    manager.repository.save(project)


def remember_approval(manager, project, session, run, request, spec):
    Draft202012Validator(remember_schema(ApprovalRule.schema())).validate(spec)
    current = manager.access.require(project)
    if session.project_id != current.id or run.session_id != session.id:
        raise ValueError('Remembered approval scope does not own the request')
    policy = ApprovalPolicy.from_policies(current.config.policies)
    explicit_unknown = spec.get('basis') == ApprovalRule.EXPLICIT_UNKNOWN
    if request.kind != InteractionRequest.KIND_APPROVAL or request.category != spec['category']:
        raise ValueError('Remembered approval requires a matching approval request')
    if explicit_unknown:
        if request.risk is not None:
            raise ValueError('Explicit unknown allowance requires unknown risk')
    else:
        if request.risk is None:
            raise ValueError('Threshold allowance requires known risk')
        if request.risk_scheme != policy.risk_scheme:
            raise ValueError('Threshold allowance requires the Project risk scheme')
    from llm.services.runtime.interactions import InteractionRepository
    if request.category == InteractionRepository.RETRY_CATEGORY:
        raise ValueError('Uncertain retries cannot create remembered approval rules')
    if any(rule.id == spec['id'] for rule in policy.rules):
        raise ValueError('Approval rule ID already exists; revoked IDs cannot be reused')
    condition = ApprovalCondition.from_dict(spec['condition'])
    resolve_evaluator(manager.approval_evaluators, condition)
    memory = RememberedApproval(
        revision=1, scope=ApprovalScope(current.id, session.id if spec['scope'] == 'session' else None),
        condition=condition,
        origin=ApprovalOrigin(run.id, request.id, request.revision, request.fingerprint, now()),
        revoked_at=None,
    )
    rule = ApprovalRule(spec['id'], spec['category'], spec.get('max_risk'), memory, spec.get('basis'))
    _save(manager, current, [*policy.rules, rule])
    return rule.to_dict()


def _selected(manager, project, identifier, expected_revision):
    current = manager.access.require(project)
    rules = approval_rules(current.config)
    rule = next((item for item in rules if item.id == identifier), None)
    if rule is None or rule.remembered is None:
        raise ValueError('Unknown remembered approval rule')
    if rule.remembered.scope.project_id != current.id:
        raise ValueError('Remembered approval belongs to another Project')
    if type(expected_revision) is not int or rule.remembered.revision != expected_revision:
        raise ValueError('Approval rule revision changed; reload before editing')
    return current, rules, rule


def update_approval(manager, project, identifier, changes, *, expected_revision):
    current, rules, rule = _selected(manager, project, identifier, expected_revision)
    if rule.remembered.revoked_at is not None:
        raise ValueError('Revoked approval rules cannot be reactivated')
    schema = remember_schema(ApprovalRule.schema())
    editable = ('category', 'condition') if rule.basis else ('category', 'max_risk', 'condition')
    schema['properties'] = {key: schema['properties'][key] for key in editable}
    schema['required'] = []
    schema.pop('oneOf')
    schema['minProperties'] = 1
    Draft202012Validator(schema).validate(changes)
    condition = ApprovalCondition.from_dict(changes['condition']) if 'condition' in changes else rule.remembered.condition
    resolve_evaluator(manager.approval_evaluators, condition)
    replacement = replace(
        rule, category=changes.get('category', rule.category), max_risk=changes.get('max_risk', rule.max_risk),
        remembered=replace(rule.remembered, revision=rule.remembered.revision + 1, condition=condition),
    )
    _save(manager, current, [replacement if item.id == identifier else item for item in rules])
    return replacement.to_dict()


def revoke_approval(manager, project, identifier, *, expected_revision):
    current, rules, rule = _selected(manager, project, identifier, expected_revision)
    if rule.remembered.revoked_at is not None:
        return rule.to_dict()
    replacement = replace(rule, remembered=replace(
        rule.remembered, revision=rule.remembered.revision + 1, revoked_at=now()))
    # Withdrawal must remain available when a host evaluator is unavailable.
    # The normal Project policy validator still checks the complete stored shape.
    current.config.configure_policies({'approval': {'rules': [
        (replacement if item.id == identifier else item).to_dict() for item in rules]}})
    manager.repository.save(current)
    return replacement.to_dict()
