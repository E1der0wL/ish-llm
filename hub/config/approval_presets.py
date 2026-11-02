"""Portable settings never transport backend-issued scoped approval evidence."""

from copy import deepcopy
from llm.core.policies import ApprovalPolicy


class ApprovalPresets:
    @staticmethod
    def portable(config):
        result = deepcopy(config)
        policy = ApprovalPolicy.from_policies(result.policies)
        if any(rule.remembered is not None for rule in policy.rules):
            result.configure_policies({ApprovalPolicy.SECTION: {
                ApprovalPolicy.rules.__name__: [rule.to_dict() for rule in policy.rules
                                               if rule.remembered is None]}})
        return result

    @staticmethod
    def validate(config):
        if any(rule.remembered is not None for rule in ApprovalPolicy.from_policies(config.policies).rules):
            raise ValueError('Presets cannot contain remembered approvals; manage them in the owning Project')

    @classmethod
    def apply(cls, candidate, current):
        cls.validate(candidate)
        result = deepcopy(candidate)
        managed = [rule.to_dict() for rule in ApprovalPolicy.from_policies(current.policies).rules
                   if rule.remembered is not None]
        if managed:
            rules = [rule.to_dict() for rule in ApprovalPolicy.from_policies(result.policies).rules]
            result.configure_policies({ApprovalPolicy.SECTION: {ApprovalPolicy.rules.__name__: rules + managed}})
        return result
