"""Portable settings never transport backend-issued scoped approval evidence."""

from copy import deepcopy
from llm.core.policies import ApprovalPolicy


class ApprovalPresets:
    NONPORTABLE = 'Presets cannot contain remembered approvals; manage them in the owning Project'
    @staticmethod
    def portable(config):
        result = deepcopy(config)
        policy = ApprovalPolicy.from_policies(result.policies)
        if any(rule.remembered is not None for rule in policy.rules):
            result.configure_policies(ApprovalPolicy.rules_update(
                rule for rule in policy.rules if rule.remembered is None))
        return result

    @staticmethod
    def validate(config):
        if any(rule.remembered is not None for rule in ApprovalPolicy.from_policies(config.policies).rules):
            raise ValueError(ApprovalPresets.NONPORTABLE)

    @classmethod
    def apply(cls, candidate, current):
        cls.validate(candidate)
        result = deepcopy(candidate)
        managed = [rule for rule in ApprovalPolicy.from_policies(current.policies).rules
                   if rule.remembered is not None]
        if managed:
            rules = list(ApprovalPolicy.from_policies(result.policies).rules)
            result.configure_policies(ApprovalPolicy.rules_update(rules + managed))
        return result
