"""제안·승인·CAS 적용·CAS 되돌리기. 대상 공개 API와 동일 workspace transaction을 사용한다."""

from llm.core.editable_snapshot import EditableSnapshot
from llm.core.component_reference import ComponentReference
from llm.core.implementation_parameters import ImplementationParameters
from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import async_method, revision_token, check_revision
from .lifecycle import RefinementStatus
from .records import (
    ProposalOperation,
    RefinementRecord,
    RefinementPolicy,
    RefinementSource,
    RefinementEvidence,
    RefinementValidation,
    RefinementEvaluation,
)
from .targets import RefinementTargets


# Public policy declaration remains bound to its single target-policy Owner.
EDITABLE = RefinementTargets.EDITABLE


class RefinementData(ComponentData):
    ABSENT = None
    PRESENT = RefinementTargets.PRESENT
    REQUIRED_VERSION = RefinementTargets.REQUIRED_VERSION
    EVALUATION_REQUIRED = "Proposal requires evaluation before approval"
    INVALID_STATUS = "Proposal status does not permit this operation"
    IMMUTABLE = "Proposal content is immutable; create a new proposal"
    TRANSITION_REQUIRED = "Use approve/reject/apply/rollback for proposal transitions"
    RETAIN_AUDIT = "Proposal audit records cannot be deleted individually"
    EVALUATION_MISMATCH = "Evaluation versions do not match this proposal"
    REQUIRED_REASON = "Failed proposal requires a reason"

    def _target(self, target, *, absent=PRESENT):
        return RefinementTargets(self).target(target, absent=absent)

    def _baseline(self, proposal):
        return RefinementTargets(self).baseline(proposal)

    def _bindings(self, skills):
        return RefinementTargets(self).bindings(skills)

    def _require_evaluation(self, proposal):
        if (
            RefinementPolicy.requires_evaluation(ImplementationParameters.read(self.get_config()).policy)
            and not proposal.optional_evaluations
        ):
            raise ValueError(self.EVALUATION_REQUIRED)

    def _proposal(self, identifier, expected_version, statuses):
        if expected_version is self.ABSENT:
            raise ValueError(self.REQUIRED_VERSION)
        value = self.load(identifier)
        check_revision(value, expected_version)
        proposal = RefinementRecord(value)
        if proposal.status not in statuses:
            raise ValueError(self.INVALID_STATUS)
        return proposal

    def _validate_evidence(self, evidence):
        for ref in RefinementEvidence.read_all(evidence):
            record = self.reference(ref.session_id, ref.run_id, ref.step_id)
            ref.check_reference(ComponentReference(record))

    def _replacement(self, proposal, current):
        return RefinementTargets(self).replacement(proposal, current)

    def _commit(self, identifier, value, status, *, source, applied_target=ABSENT):
        prior = revision_token(value.to_dict())
        value = value.transitioned(status, source, applied_target)
        # Internal transitions alone use common CRUD; public save/update cannot fabricate approval.
        ComponentData.save(self, identifier, value.to_dict(), expected_version=prior)
        return self.snapshot(identifier)

    def _source(self, source):
        value = RefinementSource.from_input(source)
        if value.has_run:
            self.reference(value.session_id, value.run_id, value.step_id)
        return value.to_dict()

    @workspace_locked
    def _apply_authorized(self, identifier, *, expected_version, source):
        """ToolExecutor의 승인 이후에만 Tool adapter가 사용한다. 별도 승인 엔진은 없다."""
        value = self._proposal(
            identifier, expected_version, {RefinementStatus.PROPOSED, RefinementStatus.APPROVED}
        )
        if value.status == RefinementStatus.PROPOSED:
            expected_version = EditableSnapshot.read(
                self.approve(identifier, expected_version=expected_version, source=source)
            ).version
        return self.apply(identifier, expected_version=expected_version, source=source)

    @workspace_locked
    def target_snapshot(self, target):
        _, data, _ = self._target(target)
        return EditableSnapshot(data, revision_token(data)).to_dict()

    @workspace_locked
    def create(self, data, *, identifier=ABSENT, source=ABSENT):
        proposal = RefinementRecord.from_input(data)
        _, before, _ = self._baseline(proposal)
        self._validate_evidence(proposal.optional_evidence)
        value = proposal.proposed(before, lambda: self._source(source))
        if value.operation == ProposalOperation.BIND_SKILLS:
            value.record_skill_versions(self._bindings(value.requested_skills))
        self._replacement(value, before)
        value.record_origin()
        return super().create(value.to_dict(), identifier=identifier)

    def save(self, *args, **kwargs):
        raise ValueError(self.IMMUTABLE)

    def update(self, *args, **kwargs):
        raise ValueError(self.TRANSITION_REQUIRED)

    def delete(self, *args, **kwargs):
        raise ValueError(self.RETAIN_AUDIT)

    @workspace_locked
    def validate(self, identifier):
        value = RefinementRecord(self.load(identifier))
        _, current, _ = self._baseline(value)
        self._validate_evidence(value.evidence)
        candidate = self._replacement(value, current)
        return RefinementValidation.create(value.expected_version, current, candidate).to_dict()

    @workspace_locked
    def record_evaluation(self, identifier, evaluation, *, expected_version, source=ABSENT):
        """신뢰한 평가자가 결과를 기록한다. 평가 기준/모델/합격 판정은 호출자가 소유한다."""
        value = self._proposal(identifier, expected_version, {RefinementStatus.PROPOSED})
        validation = RefinementValidation(self.validate(identifier))
        evaluation = RefinementEvaluation.read(evaluation)
        if not evaluation.matches(validation):
            raise ValueError(self.EVALUATION_MISMATCH)
        if evaluation.has_evidence:
            self._validate_evidence(evaluation.evidence)
        updated = value.evaluated(evaluation.to_dict(), lambda: self._source(source))
        ComponentData.save(self, identifier, updated.to_dict(), expected_version=expected_version)
        return self.snapshot(identifier)

    @workspace_locked
    def approve(self, identifier, *, expected_version, source=ABSENT):
        value = self._proposal(identifier, expected_version, {RefinementStatus.PROPOSED})
        self.validate(identifier)
        self._require_evaluation(value)
        return self._commit(identifier, value, RefinementStatus.APPROVED, source=self._source(source))

    @workspace_locked
    def reject(self, identifier, *, expected_version, source=ABSENT):
        value = self._proposal(
            identifier, expected_version, {RefinementStatus.PROPOSED, RefinementStatus.APPROVED}
        )
        return self._commit(identifier, value, RefinementStatus.REJECTED, source=self._source(source))

    @workspace_locked
    def fail(self, identifier, *, expected_version, reason, source=ABSENT):
        """UI/검증자가 적용 불가능한 제안을 명시적으로 종료한다. 대상 변경은 없다."""
        value = self._proposal(
            identifier, expected_version, {RefinementStatus.PROPOSED, RefinementStatus.APPROVED}
        )
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(self.REQUIRED_REASON)
        return self._commit(
            identifier,
            value,
            RefinementStatus.FAILED,
            source=RefinementSource.failed(self._source(source), reason),
        )

    @workspace_locked
    def apply(self, identifier, *, expected_version, source=ABSENT):
        value = self._proposal(identifier, expected_version, {RefinementStatus.APPROVED})
        self.validate(identifier)
        self._require_evaluation(value)
        handle, current, kwargs = self._baseline(value)
        replacement = self._replacement(value, current)
        source = RefinementSource.proposal(self._source(source), RefinementSource.APPLY, identifier)
        receipt = RefinementTargets(self).apply(
            identifier, value, handle, current, kwargs, replacement, source
        )
        return self._commit(
            identifier, value, RefinementStatus.APPLIED, source=source, applied_target=receipt
        )

    @workspace_locked
    def rollback(self, identifier, *, expected_version, source=ABSENT):
        value = self._proposal(identifier, expected_version, {RefinementStatus.APPLIED})
        targets = RefinementTargets(self)
        handle, current, kwargs = targets.rollback_target(value)
        source = RefinementSource.proposal(self._source(source), RefinementSource.ROLLBACK, identifier)
        targets.rollback(value, handle, current, kwargs, source)
        return self._commit(identifier, value, RefinementStatus.ROLLED_BACK, source=source)

    atarget_snapshot = async_method(target_snapshot)
    acreate = async_method(create)
    asave = async_method(save)
    aupdate = async_method(update)
    adelete = async_method(delete)
    avalidate = async_method(validate)
    arecord_evaluation = async_method(record_evaluation)
    aapprove = async_method(approve)
    areject = async_method(reject)
    afail = async_method(fail)
    aapply = async_method(apply)
    arollback = async_method(rollback)
