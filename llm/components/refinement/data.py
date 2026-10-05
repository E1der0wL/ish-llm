"""제안·승인·CAS 적용·CAS 되돌리기. 대상 공개 API와 동일 workspace transaction을 사용한다."""

from copy import deepcopy
from datetime import datetime, timezone

from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import async_method, revision_token, check_revision


# Refine은 작업 지침을 개선한다. Agent 권한/엔진/실행 정책을 수정하는 경로가 아니다.
EDITABLE = {"skills": {"instructions", "description", "title", "tags", "resources"},
            "prompts": {"messages", "description"},
            "agents": {"purpose", "system_prompt", "description"},
            "memory": {"content", "kind", "tags"}}


class RefinementData(ComponentData):
    def _target(self, target):
        from .component import TARGET
        from jsonschema import Draft202012Validator
        if not Draft202012Validator(TARGET).is_valid(target):
            raise ValueError("Refinement target must be a saved Project resource")
        if "session_id" in target and target["component"] != "memory":
            raise ValueError("Only scoped Memory targets accept session_id")
        handle = self.related(target["component"])
        kwargs = {"session_id": target["session_id"]} if "session_id" in target else {}
        value = handle.load(target["identifier"], **kwargs)
        return handle, value, kwargs

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
        if not isinstance(patch, dict) or not patch or set(patch) - EDITABLE[target["component"]]:
            raise ValueError("Refinement patch contains unsupported or authority-changing fields")
        result = {**deepcopy(current), **deepcopy(patch)}
        if result == current:
            raise ValueError("Refinement patch does not change the target")
        if target["component"] == "memory":
            if not isinstance(result.get("content"), str) or not result["content"].strip():
                raise ValueError("Memory candidate requires content")
            if not isinstance(result.get("kind"), str) or not result["kind"].strip():
                raise ValueError("Memory candidate requires kind")
            if not isinstance(result.get("tags"), list) or any(not isinstance(t, str) or not t.strip() for t in result["tags"]):
                raise ValueError("Memory candidate requires text tags")
        else:
            self.registry.get(target["component"]).validate_record(target["identifier"], result)
        return result

    def _commit(self, identifier, value, status, *, source):
        prior = revision_token(value)
        value = deepcopy(value)
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
        value = self._proposal(identifier, expected_version, {"proposed", "approved"})
        if value["status"] == "proposed":
            expected_version = self._commit(identifier, value, "approved", source=source)["version"]
        return self.apply(identifier, expected_version=expected_version, source=source)

    # 공개 API: 제안 입력은 immutable. 변경하려면 새 제안을 만든다.
    @workspace_locked
    def target_snapshot(self, target):
        _, data, _ = self._target(target)
        return {"data": data, "version": revision_token(data)}

    @workspace_locked
    def create(self, data, *, identifier=None, source=None):
        allowed = {"target", "operation", "expected_version", "reason", "evidence", "patch", "metadata"}
        if not isinstance(data, dict) or set(data) - allowed or data.get("operation") != "update":
            raise ValueError("Create requires an update proposal, not lifecycle fields")
        if not data.get("expected_version"):
            raise ValueError("expected_version is required")
        _, before, _ = self._target(data.get("target"))
        check_revision(before, data["expected_version"])
        self._validate_evidence(data.get("evidence"))
        value = {**deepcopy(data), "before": before, "status": "proposed", "source": self._source(source), "history": []}
        self._replacement(value, before)
        value["history"].append({"status": "proposed", "at": datetime.now(timezone.utc).isoformat(), "source": value["source"]})
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
        _, current, _ = self._target(value["target"])
        check_revision(current, value["expected_version"])
        self._validate_evidence(value["evidence"])
        self._replacement(value, current)
        return {"valid": True, "target_version": revision_token(current)}

    @workspace_locked
    def approve(self, identifier, *, expected_version, source=None):
        value = self._proposal(identifier, expected_version, {"proposed"})
        self.validate(identifier)
        return self._commit(identifier, value, "approved", source=self._source(source))

    @workspace_locked
    def reject(self, identifier, *, expected_version, source=None):
        value = self._proposal(identifier, expected_version, {"proposed", "approved"})
        return self._commit(identifier, value, "rejected", source=self._source(source))

    @workspace_locked
    def fail(self, identifier, *, expected_version, reason, source=None):
        """UI/검증자가 적용 불가능한 제안을 명시적으로 종료한다. 대상 변경은 없다."""
        value = self._proposal(identifier, expected_version, {"proposed", "approved"})
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Failed proposal requires a reason")
        return self._commit(identifier, value, "failed", source={**self._source(source), "reason": reason})

    @workspace_locked
    def apply(self, identifier, *, expected_version, source=None):
        value = self._proposal(identifier, expected_version, {"approved"})
        self.validate(identifier)
        handle, current, kwargs = self._target(value["target"])
        replacement = self._replacement(value, current)
        source = {**self._source(source), "kind": "refinement", "proposal_id": identifier}
        component, target_id = value["target"]["component"], value["target"]["identifier"]
        if component == "memory":
            # 보존된 원본 revision은 기존 review/consolidation에서 다시 검사한다.
            candidate = {key: deepcopy(replacement[key]) for key in ("content", "kind", "tags", "scope")}
            if "session_id" in replacement:
                candidate["session_id"] = replacement["session_id"]
            candidate.update(status="candidate", metadata={"replaces": [{"id": target_id, "revision": current["revision"]}],
                             "refinement_proposal": identifier})
            applied_id = handle.create(candidate, source=source)
        elif component == "agents":
            handle.revise(target_id, replacement, expected_revision=value["expected_version"])
            applied_id = target_id
        else:
            handle.save(target_id, replacement, expected_version=value["expected_version"])
            applied_id = target_id
        after = handle.load(applied_id, **kwargs)
        receipt = {"identifier": applied_id, "version": revision_token(after)}
        updated = deepcopy(value)
        updated["applied_target"] = receipt
        # _commit의 CAS는 실제 기존 proposal을 기준으로 한다.
        ComponentData.save(self, identifier, updated, expected_version=expected_version)
        return self._commit(identifier, updated, "applied", source=source)

    @workspace_locked
    def rollback(self, identifier, *, expected_version, source=None):
        value = self._proposal(identifier, expected_version, {"applied"})
        target = value["target"]
        handle = self.related(target["component"])
        kwargs = {"session_id": target["session_id"]} if "session_id" in target else {}
        applied = value["applied_target"]
        current = handle.load(applied["identifier"], **kwargs)
        check_revision(current, applied["version"])
        source = {**self._source(source), "kind": "refinement_rollback", "proposal_id": identifier}
        if target["component"] == "memory":
            handle.delete(applied["identifier"], expected_revision=current["revision"], source=source, **kwargs)
        elif target["component"] == "agents":
            handle.revise(target["identifier"], value["before"], expected_revision=applied["version"])
        else:
            handle.save(target["identifier"], value["before"], expected_version=applied["version"])
        return self._commit(identifier, value, "rolled_back", source=source)

    atarget_snapshot = async_method(target_snapshot)
    acreate = async_method(create)
    asave = async_method(save)
    aupdate = async_method(update)
    adelete = async_method(delete)
    avalidate = async_method(validate)
    aapprove = async_method(approve)
    areject = async_method(reject)
    afail = async_method(fail)
    aapply = async_method(apply)
    arollback = async_method(rollback)
