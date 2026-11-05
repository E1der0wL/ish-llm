"""On-demand management through public Project/Run approval APIs only."""

from dataclasses import dataclass
from enum import StrEnum

from llm.core.interactions import InteractionRequest, InteractionView
from llm.core.models import RunStatus
from llm.core.policies import ApprovalPolicy, ApprovalRule
from llm.services.query import Query

from .remembered import ToolApprovalConditions
from .command_range import CommandRangeApproval, CommandChain
from .approval_operations import ApprovalQuery, ApprovalRuleTarget, ApprovalEdit, ApprovalTarget, ApprovalAssessment, ApprovalApplication


class ApprovalAction(StrEnum):
    LIST = 'list'
    UPDATE = 'update'
    REVOKE = 'revoke'
    CANCEL = 'cancel'
    RECLASSIFY = 'reclassify'
    APPLY = 'apply'

    def read(self, value):
        match self:
            case ApprovalAction.LIST:
                return ApprovalQuery.from_dict(value)
            case ApprovalAction.REVOKE:
                return ApprovalRuleTarget.from_dict(value)
            case ApprovalAction.UPDATE:
                return ApprovalEdit.from_dict(value)
            case ApprovalAction.RECLASSIFY:
                return ApprovalAssessment.from_dict(value)
            case ApprovalAction.APPLY:
                return ApprovalApplication.from_dict(value)
            case _:
                return ApprovalTarget.from_dict(value)


@dataclass(frozen=True, slots=True)
class ApprovalEntry:
    session_id: str
    session_title: str
    run_id: str
    view: dict

    @property
    def request(self):
        return self.observation.request

    @property
    def observation(self):
        return InteractionView.from_dict(self.view)


@dataclass(frozen=True, slots=True)
class ApprovalCatalog:
    rules: tuple[ApprovalRule, ...]
    interactions: tuple[ApprovalEntry, ...]
    enabled: bool
    risk_scheme: str
    resumed: bool = False


class ApprovalManagement:
    READ_ONLY = 'This condition is not editable by the Hub approval editor'
    def __init__(self, runtime):
        self.runtime = runtime

    async def _catalog(self, project, include_interactions, resumed):
        policy = ApprovalPolicy.from_policies((await project.aget_data()).config.policies)
        rules = tuple(ApprovalRule.from_dict(record) for record in await project.approvals.alist())
        entries = []
        if include_interactions:
            for session in await project.sessions.alist():
                runs = await session.run.alist(query=Query(status=RunStatus.PAUSED))
                if not runs:
                    continue
                title = (await session.aget_data()).title
                for run in runs:
                    async with self.runtime.approval_flow.lock(session.id, run.id):
                        if await self.runtime.approval_flow.submitted(session.id, run.id):
                            continue
                        for view in await run.ainteraction_views():
                            if (view.request.kind == InteractionRequest.KIND_APPROVAL
                                    and (view.can_respond or view.can_resume)):
                                entries.append(ApprovalEntry(session.id, title, run.id, view.to_dict()))
        return ApprovalCatalog(rules, tuple(entries), policy.enabled, policy.risk_scheme, resumed)

    async def perform(self, project_id, action, payload=None):
        action = ApprovalAction(action)
        payload = action.read({} if payload is None else payload)
        project = await self.runtime.backend.projects.aload(project_id)
        resumed = False
        if action == ApprovalAction.UPDATE:
            # The editor changes arguments/threshold only. Keep the captured
            # Tool contract and environment; scopes and proof are backend-owned.
            rule = next(rule for item in await project.approvals.alist()
                        if (rule := ApprovalRule.from_dict(item)).id == payload.id)
            condition = rule.remembered.condition
            original = ToolApprovalConditions.exact_action(condition)
            ranged = CommandRangeApproval.config(condition)
            if original is not None:
                original = ToolApprovalConditions.replace_arguments(original, payload.arguments)
                updated = ToolApprovalConditions.exact_condition(original)
            elif ranged is not None:
                updated = CommandRangeApproval.condition(ranged.action, CommandChain.prefixes(payload.prefixes))
            else:
                raise ValueError(ApprovalManagement.READ_ONLY)
            from llm.core.approval import RememberedApproval
            changes = {RememberedApproval.condition.__name__: updated.to_dict()}
            if rule.basis != ApprovalRule.EXPLICIT_UNKNOWN:
                changes[ApprovalRule.max_risk.__name__] = payload.max_risk
            await project.approvals.aupdate(rule.id, changes,
                                           expected_revision=payload.revision)
        elif action == ApprovalAction.REVOKE:
            await project.approvals.arevoke(payload.id, expected_revision=payload.revision)
        elif action in (ApprovalAction.CANCEL, ApprovalAction.RECLASSIFY, ApprovalAction.APPLY):
            session = await project.sessions.aload(payload.session_id)
            run = await session.run.aload(payload.run_id)
            async with self.runtime.approval_flow.lock(session.id, run.id):
                if action == ApprovalAction.CANCEL:
                    await run.acancel_interaction(payload.request)
                elif action == ApprovalAction.RECLASSIFY:
                    request = payload.request
                    # Explicit user assessment; never claim that the model
                    # supplied a score or that reclassification itself approves.
                    await run.areclassify(request, risk=payload.risk, risk_scheme=payload.risk_scheme)
                else:
                    await run.apply_approval()
                    resumed = await self.runtime.approval_flow.resume_approved(session, run, preview=True)
            self.runtime.snapshot_reader.invalidate(session.id)
            self.runtime.urgent_observation = True
        self.runtime.dirty.set()
        return await self._catalog(project, payload.include_interactions, resumed)
