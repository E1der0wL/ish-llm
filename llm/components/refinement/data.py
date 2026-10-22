"""제안·승인·CAS 적용·CAS 되돌리기. 대상 공개 API와 동일 workspace transaction을 사용한다."""

from copy import deepcopy
from .lifecycle import RefinementStatus
from datetime import datetime, timezone

from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import async_method, revision_token, check_revision


# Refine은 작업 지침을 개선한다. Agent 권한/엔진/실행 정책을 수정하는 경로가 아니다.
EDITABLE = {"skills": {"instructions", "description", "title", "tags", "resources"},
            "prompts": {"messages", "description"},
            "agents": {"purpose", "description"},
            "memory": {"content", "kind", "tags"}}


class RefinementData(ComponentData):
    def _target(self, target, *, absent=False):
        from .component import TARGET
        from jsonschema import Draft202012Validator
        if not Draft202012Validator(TARGET).is_valid(target):
            raise ValueError("Refinement target must be a saved Project resource")
        if "session_id" in target and target["component"] != "memory":
            raise ValueError("Only scoped Memory targets accept session_id")
        handle = self.agents() if target["component"] == "agents" else self.related(target["component"])
        kwargs = {"session_id": target["session_id"]} if "session_id" in target else {}
        if absent:
            try:
                handle.load(target["identifier"], **kwargs)
            except FileNotFoundError:
                return handle, {}, kwargs
            raise FileExistsError("Refinement creation target already exists")
        value = handle.load(target["identifier"], **kwargs)
        return handle, value, kwargs

    def _baseline(self, proposal):
        operation = proposal["operation"]
        creating = operation in ("create", "fork")
        handle, current, kwargs = self._target(proposal["target"], absent=creating)
        if creating:
            if proposal["target"]["component"] != "skills" or proposal["expected_version"] is not None:
                raise ValueError("Skill creation requires an absent target and expected_version=null")
        else:
            if not proposal["expected_version"]:
                raise ValueError("expected_version is required")
            check_revision(current, proposal["expected_version"])
        if operation == "fork":
            parent = proposal.get("parent")
            from .component import PARENT
            from jsonschema import Draft202012Validator
            if not Draft202012Validator(PARENT).is_valid(parent):
                raise ValueError("Fork requires parent identifier and version")
            current = handle.load(parent["identifier"])
            check_revision(current, parent["version"])
        elif "parent" in proposal:
            raise ValueError("Only a fork accepts parent")
        return handle, current, kwargs

    def _bindings(self, skills):
        if not isinstance(skills, list) or any(not isinstance(s, str) for s in skills) or len(set(skills)) != len(skills):
            raise ValueError("Skill binding requires unique Skill IDs")
        handle = self.related("skills")
        return {key: handle.snapshot(key)["version"] for key in skills}

    def _require_evaluation(self, proposal):
        if self.get_config().get("policy", {}).get("require_evaluation") is True and not proposal.get("evaluations"):
            raise ValueError("Proposal requires evaluation before approval")

    def _proposal(self, identifier, expected_version, statuses):
        if expected_version is None:
            raise ValueError("expected_version is required")
        value = self.load(identifier)
        check_revision(value, expected_version)
        if value["status"] not in statuses:
            raise ValueError("Proposal status does not permit this operation")
        return value

    def _validate_evidence(self, evidence):
        from .component import EVIDENCE
        from jsonschema import Draft202012Validator
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("Proposal requires Run evidence")
        for ref in evidence:
            if not Draft202012Validator(EVIDENCE).is_valid(ref):
                raise ValueError("Invalid proposal evidence")
            record = self.reference(ref["session_id"], ref["run_id"], ref.get("step_id"))
            if "tool_call_id" in ref and record.get("step", {}).get("metadata", {}).get("tool_call_id") != ref["tool_call_id"]:
                raise ValueError("Evidence Tool call does not match its Step")
            if "error_code" in ref:
                owner = record.get("step", record["run"])
                code = owner.get("error_code") or owner.get("metadata", {}).get("error_code")
                if code != ref["error_code"]:
                    raise ValueError("Evidence error code does not match its source")

    def _replacement(self, proposal, current):
        target, patch = proposal["target"], proposal["patch"]
        if proposal["operation"] == "bind_skills":
            if target["component"] != "agents" or set(patch) != {"skills"}:
                raise ValueError("Binding may change only Agent resources.skills")
            versions = self._bindings(patch["skills"])
            if proposal["skill_versions"] != versions:
                raise ValueError("Bound Skill changed; create a new binding proposal")
            result = deepcopy(current)
            result.setdefault("resources", {})["skills"] = deepcopy(patch["skills"])
            if result == current:
                raise ValueError("Binding does not change the target")
            from llm.core.agents import AgentDefinition
            AgentDefinition.validate(target["identifier"], result)
            return result
        if not isinstance(patch, dict) or not patch or set(patch) - EDITABLE[target["component"]]:
            raise ValueError("Refinement patch contains unsupported or authority-changing fields")
        result = {**deepcopy(current), **deepcopy(patch)}
        if proposal["operation"] == "fork":
            result["lineage"] = {"parent": proposal["parent"]["identifier"], "parent_revision": proposal["parent"]["version"]}
        if result == current:
            raise ValueError("Refinement patch does not change the target")
        if target["component"] == "memory":
            if not isinstance(result.get("content"), str) or not result["content"].strip():
                raise ValueError("Memory candidate requires content")
            if not isinstance(result.get("kind"), str) or not result["kind"].strip():
                raise ValueError("Memory candidate requires kind")
            if not isinstance(result.get("tags"), list) or any(not isinstance(t, str) or not t.strip() for t in result["tags"]):
                raise ValueError("Memory candidate requires text tags")
        elif target["component"] == "agents":
            from llm.core.agents import AgentDefinition
            AgentDefinition.validate(target["identifier"], result)
        else:
            self.registry.get(target["component"]).validate_record(target["identifier"], result)
        return result

    def _commit(self, identifier, value, status, *, source, applied_target=None):
        prior = revision_token(value)
        value = deepcopy(value)
        if applied_target is not None:
            value["applied_target"] = deepcopy(applied_target)
        value["status"] = status
        value["history"].append({"status": status, "at": datetime.now(timezone.utc).isoformat(), "source": deepcopy(source)})
        # 내부 전이만 공통 CRUD 저장을 사용한다. 외부 save/update로 승인 상태를 꾸밀 수 없다.
        ComponentData.save(self, identifier, value, expected_version=prior)
        return self.snapshot(identifier)

    def _source(self, source):
        value = deepcopy(source) if source is not None else {"kind": "api"}
        if not isinstance(value, dict):
            raise ValueError("Proposal source must be an object")
        if "run_id" in value:
            self.reference(value.get("session_id"), value["run_id"], value.get("step_id"))
        return value

    @workspace_locked
    def _apply_authorized(self, identifier, *, expected_version, source):
        """ToolExecutor의 승인 이후에만 Tool adapter가 사용한다. 별도 승인 엔진은 없다."""
        value = self._proposal(identifier, expected_version, {RefinementStatus.PROPOSED, RefinementStatus.APPROVED})
        if value["status"] == RefinementStatus.PROPOSED:
            expected_version = self.approve(identifier, expected_version=expected_version, source=source)["version"]
        return self.apply(identifier, expected_version=expected_version, source=source)

    # 공개 API: 제안 입력은 immutable. 변경하려면 새 제안을 만든다.
    @workspace_locked
    def target_snapshot(self, target):
        _, data, _ = self._target(target)
        return {"data": data, "version": revision_token(data)}

    @workspace_locked
    def create(self, data, *, identifier=None, source=None):
        allowed = {"target", "operation", "expected_version", "reason", "evidence", "patch", "metadata", "parent"}
        required = {"target", "operation", "expected_version", "reason", "evidence", "patch"}
        if (not isinstance(data, dict) or set(data) - allowed or not required <= data.keys()
                or data.get("operation") not in ("update", "create", "fork", "bind_skills")
                or not isinstance(data["patch"], dict)):
            raise ValueError("Create requires a resource proposal, not lifecycle fields")
        _, before, _ = self._baseline(data)
        self._validate_evidence(data.get("evidence"))
        value = {**deepcopy(data), "before": before, "status": RefinementStatus.PROPOSED, "source": self._source(source), "history": [], "evaluations": []}
        if value["operation"] == "bind_skills":
            value["skill_versions"] = self._bindings(value["patch"].get("skills"))
        self._replacement(value, before)
        value["history"].append({"status": RefinementStatus.PROPOSED, "at": datetime.now(timezone.utc).isoformat(), "source": value["source"]})
        return super().create(value, identifier=identifier)

    def save(self, *args, **kwargs):
        raise ValueError("Proposal content is immutable; create a new proposal")

    def update(self, *args, **kwargs):
        raise ValueError("Use approve/reject/apply/rollback for proposal transitions")

    def delete(self, *args, **kwargs):
        raise ValueError("Proposal audit records cannot be deleted individually")

    @workspace_locked
    def validate(self, identifier):
        value = self.load(identifier)
        _, current, _ = self._baseline(value)
        self._validate_evidence(value["evidence"])
        candidate = self._replacement(value, current)
        return {"valid": True, "target_version": value["expected_version"],
                "baseline_version": revision_token(current) if current else None,
                "candidate_version": revision_token(candidate)}

    @workspace_locked
    def record_evaluation(self, identifier, evaluation, *, expected_version, source=None):
        """신뢰한 평가자가 결과를 기록한다. 평가 기준/모델/합격 판정은 호출자가 소유한다."""
        value = self._proposal(identifier, expected_version, {RefinementStatus.PROPOSED})
        validation = self.validate(identifier)
        from .component import EVALUATION
        from jsonschema import Draft202012Validator
        if not Draft202012Validator(EVALUATION).is_valid(evaluation):
            raise ValueError("Invalid refinement evaluation")
        if evaluation["baseline_version"] != validation["baseline_version"] or evaluation["candidate_version"] != validation["candidate_version"]:
            raise ValueError("Evaluation versions do not match this proposal")
        if evaluation.get("evidence"):
            self._validate_evidence(evaluation["evidence"])
        updated = deepcopy(value)
        updated["evaluations"].append({**deepcopy(evaluation), "source": self._source(source),
                                                     "at": datetime.now(timezone.utc).isoformat()})
        ComponentData.save(self, identifier, updated, expected_version=expected_version)
        return self.snapshot(identifier)

    @workspace_locked
    def approve(self, identifier, *, expected_version, source=None):
        value = self._proposal(identifier, expected_version, {RefinementStatus.PROPOSED})
        self.validate(identifier)
        self._require_evaluation(value)
        return self._commit(identifier, value, RefinementStatus.APPROVED, source=self._source(source))

    @workspace_locked
    def reject(self, identifier, *, expected_version, source=None):
        value = self._proposal(identifier, expected_version, {RefinementStatus.PROPOSED, RefinementStatus.APPROVED})
        return self._commit(identifier, value, RefinementStatus.REJECTED, source=self._source(source))

    @workspace_locked
    def fail(self, identifier, *, expected_version, reason, source=None):
        """UI/검증자가 적용 불가능한 제안을 명시적으로 종료한다. 대상 변경은 없다."""
        value = self._proposal(identifier, expected_version, {RefinementStatus.PROPOSED, RefinementStatus.APPROVED})
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Failed proposal requires a reason")
        return self._commit(identifier, value, RefinementStatus.FAILED, source={**self._source(source), "reason": reason})

    @workspace_locked
    def apply(self, identifier, *, expected_version, source=None):
        value = self._proposal(identifier, expected_version, {RefinementStatus.APPROVED})
        self.validate(identifier)
        self._require_evaluation(value)
        handle, current, kwargs = self._baseline(value)
        replacement = self._replacement(value, current)
        source = {**self._source(source), "kind": "refinement", "proposal_id": identifier}
        component, target_id = value["target"]["component"], value["target"]["identifier"]
        if value["operation"] == "fork":
            applied_id = handle.fork(value["parent"]["identifier"], replacement, identifier=target_id,
                                     expected_revision=value["parent"]["version"])
        elif value["operation"] == "create":
            applied_id = handle.create(replacement, identifier=target_id)
        elif component == "memory":
            from llm.components.memory.component import MemoryComponent
            # 보존된 원본 revision은 기존 review/consolidation에서 다시 검사한다.
            candidate = {key: deepcopy(replacement[key]) for key in ("content", "kind", "tags", "scope")}
            if "session_id" in replacement:
                candidate["session_id"] = replacement["session_id"]
            candidate.update(status=MemoryComponent.CANDIDATE, replaces=[{"id": target_id, "generation": current["generation"], "revision": current["revision"]}],
                             metadata={"refinement_proposal": identifier})
            applied_id = handle.create(candidate, source=source)
        elif component == "agents":
            handle.revise(target_id, replacement, expected_revision=value["expected_version"])
            applied_id = target_id
        else:
            handle.save(target_id, replacement, expected_version=value["expected_version"])
            applied_id = target_id
        after = handle.load(applied_id, **kwargs)
        receipt = {"identifier": applied_id, "version": revision_token(after)}
        return self._commit(identifier, value, RefinementStatus.APPLIED, source=source, applied_target=receipt)

    @workspace_locked
    def rollback(self, identifier, *, expected_version, source=None):
        value = self._proposal(identifier, expected_version, {RefinementStatus.APPLIED})
        target = value["target"]
        handle = self.agents() if target["component"] == "agents" else self.related(target["component"])
        kwargs = {"session_id": target["session_id"]} if "session_id" in target else {}
        applied = value["applied_target"]
        current = handle.load(applied["identifier"], **kwargs)
        check_revision(current, applied["version"])
        source = {**self._source(source), "kind": "refinement_rollback", "proposal_id": identifier}
        if value["operation"] in ("create", "fork"):
            # SkillData.delete가 Agent와 child lineage 모두를 같은 잠금에서 검사한다.
            handle.delete(applied["identifier"], expected_version=applied["version"])
        elif target["component"] == "memory":
            handle.delete(applied["identifier"], expected_revision=current["revision"], expected_generation=current["generation"], source=source, **kwargs)
        elif target["component"] == "agents":
            handle.revise(target["identifier"], value["before"], expected_revision=applied["version"])
        else:
            handle.save(target["identifier"], value["before"], expected_version=applied["version"])
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
