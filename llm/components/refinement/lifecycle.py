"""Refinement owns proposal states and the transitions preserved in its history."""

from enum import StrEnum


class RefinementStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


TRANSITIONS = {
    RefinementStatus.PROPOSED: {RefinementStatus.APPROVED, RefinementStatus.REJECTED, RefinementStatus.FAILED},
    RefinementStatus.APPROVED: {RefinementStatus.APPLIED, RefinementStatus.REJECTED, RefinementStatus.FAILED},
    RefinementStatus.APPLIED: {RefinementStatus.ROLLED_BACK},
    RefinementStatus.REJECTED: set(),
    RefinementStatus.ROLLED_BACK: set(),
    RefinementStatus.FAILED: set(),
}
