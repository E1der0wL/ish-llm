"""도메인 참조 검사와 명시적 복구. 실행을 재생하지 않고 저장 상태만 정리한다."""

from copy import deepcopy
from llm.core.contracts import Diagnostic, ResourceRef
from llm.core.plans import RecoveryPlan, RecoveryResult
from llm.core.models import MessageRole, MessageStatus, RunStatus, StepStatus, SessionStatus, now, new_id, validate_run_transition
from llm.services.infrastructure.storage import atomic_json, record, revision_token, reject_links
from llm.services.infrastructure.logging import log_event
from llm.services.lifecycle.steps import StepManager


def recover_session(sessions, repository, steps, session, store):
    """Session 소유권을 확보한 호출자만 사용한다. Runtime 시작과 수동 복구가 같은 규칙을 쓴다."""
    from llm.services.infrastructure.transactions import watch
    watch(session)
    recovered = []
    messages = {message.id: message for message in store.list()}
    for run in repository.list(session):
        stale = run.status in (RunStatus.PENDING, RunStatus.RUNNING)
        if stale:
            validate_run_transition(run.status, RunStatus.INTERRUPTED, reason="recovery")
        steps.recover(run)
        if stale:
            if sessions._owner(session).conversation_storage == "file" and run.assistant_message_id in messages:
                repository.reconcile_output(run, store)
            run.status, run.error_code, run.ended_at = RunStatus.INTERRUPTED, "process_restart", now()
            repository.save(run)
            log_event(run.paths.logs, "run.recovered", entity_id=run.id, status=run.status)
            recovered.append(deepcopy(run))
        message = messages.get(run.input_message_id)
        if message is not None and message.status == MessageStatus.QUEUED:
            store.bind_run(message.id, run.id)
            store.set_status(message.id, MessageStatus.COMMITTED)
            message.status = MessageStatus.COMMITTED
    for message in messages.values():
        if message.role == MessageRole.ASSISTANT and message.status == MessageStatus.STREAMING:
            store.set_status(message.id, MessageStatus.INTERRUPTED)
    session.current_run_id, session.status = None, SessionStatus.IDLE
    sessions._save_runtime(session)
    return recovered


class ProjectRecovery:
    """검사 결과는 원본 fingerprint를 포함한다. 손상/누락을 추측하여 메우지 않는다."""

    def __init__(self, manager):
        self.manager = manager
        self.runs = manager.sessions.run_repository
        self.steps = StepManager(manager.backup_steps)

    def plan(self, project) -> RecoveryPlan:
        issues, actions, signatures = [], [], []
        def issue(code, *, session=None, run=None, detail=None, repairable=False):
            source = ResourceRef("run" if run else "session" if session else "project", run or session or project.id,
                                 project_id=project.id, session_id=session, run_id=run)
            issues.append(Diagnostic(code, code.replace("_", " "), source=source,
                                     details={"detail": detail, "repairable": repairable}))
        for session in self.manager.sessions.list(project, include_deleted=True):
            signatures.append(record(session))
            if self.manager.ownership.session_attached((project.id, session.id)):
                issue("runtime_attached", session=session.id)
                continue
            if session.status == SessionStatus.DELETED or project.conversation_storage != "file":
                issue("offline_file_session_required", session=session.id)
                continue
            start = len(issues)
            if any((session.paths.state / "maintenance").glob("*.json")):
                issue("pending_retention", session=session.id)
            try:
                reject_links(session.paths.state)
                reject_links(session.paths.conversation)
                store = self.manager.sessions.conversations(session)
                messages = {m.id: m for m in store.list()}
                runs = self.runs.list(session)
                identifiers = {r.id for r in runs}
                signatures.extend(record(m) for m in messages.values())
                signatures.extend(record(r) for r in runs)
                if session.current_run_id or session.status == SessionStatus.RUNNING:
                    issue("stale_session", session=session.id, repairable=True)
                if any(m.run_id and m.run_id not in identifiers for m in messages.values()):
                    issue("missing_run_reference", session=session.id)
                for run in runs:
                    stale = run.status in (RunStatus.RUNNING, RunStatus.PENDING)
                    if stale:
                        issue("stale_run", session=session.id, run=run.id, repairable=True)
                    source, assistant = messages.get(run.input_message_id), messages.get(run.assistant_message_id)
                    if source is None or source.role != MessageRole.USER or source.run_id not in (None, run.id):
                        issue("invalid_input_reference", session=session.id, run=run.id)
                    elif source.status == MessageStatus.QUEUED:
                        issue("claimed_queue_gap", session=session.id, run=run.id, repairable=True)
                    if assistant is not None and (assistant.role != MessageRole.ASSISTANT or assistant.run_id != run.id):
                        issue("invalid_assistant_reference", session=session.id, run=run.id)
                    elif assistant is None and not (stale or run.error_code == "process_restart"):
                        issue("missing_assistant", session=session.id, run=run.id)
                    if assistant is not None and assistant.status == MessageStatus.STREAMING:
                        issue("stale_assistant", session=session.id, run=run.id, repairable=True)
                    steps = self.steps.list(run)
                    signatures.extend(record(s) for s in steps)
                    if any(s.status in (StepStatus.RUNNING, StepStatus.PENDING) for s in steps):
                        issue("stale_step", session=session.id, run=run.id, repairable=True)
                    for name in run.metadata.get("checkpoints", []):
                        checkpoint = self.runs.checkpoint(run, name)
                        signatures.append(checkpoint)
                        if run.status != RunStatus.COMPLETED and any(mid not in messages for mid in checkpoint["header"].get("message_ids", [])):
                            issue("missing_checkpoint_message", session=session.id, run=run.id)
                    outputs = self.runs.outputs(run)
                    signatures.append([o.to_dict() if hasattr(o, "to_dict") else str(o) for o in outputs])
                    visible = [o for o in outputs if o.visibility == "user"]
                    final = next((o for o in visible if o.final and o.step_id is None and (o.text or o.data is None)), None)
                    text = final.text if final else "".join(o.text for o in visible)
                    if outputs and assistant is not None and text != assistant.content:
                        issue("output_gap", session=session.id, run=run.id, repairable=True)
            except (ValueError, OSError, KeyError, TypeError) as error:
                issue("unreadable_history", session=session.id, detail=str(error))
            found = issues[start:]
            if found and all(i.details["repairable"] for i in found):
                actions.append(session.id)
        for name in project.components:
            try:
                self.manager.components.get(name).validate_backup(project)
            except (ValueError, OSError, KeyError, TypeError) as error:
                issue("component_integrity", detail={"component": name, "error": str(error)})
        result = {"issues": [i.to_dict() for i in issues], "repair_sessions": actions}
        return RecoveryPlan(ResourceRef("project", project.id, project_id=project.id), issues, actions,
            revision_token({"project": record(project), "sources": signatures, **result}))

    def apply(self, project, expected_version):
        plan = self.plan(project)
        if not expected_version or expected_version != plan.version:
            raise ValueError("recovery_conflict: inspect a fresh recovery plan")
        journal = reject_links(project.paths.state / "recovery" / (new_id() + ".json"))
        state = {"status": "running", "plan": plan.to_dict(), "completed_sessions": [], "started_at": now()}
        atomic_json(journal, state)
        for identifier in plan.repair_sessions:
            session = self.manager.sessions.load(project, identifier)
            self.manager.sessions.attach_runtime(session)
            try:
                store = self.manager.sessions.conversations(session)
                recover_session(self.manager.sessions, self.runs, self.steps, session, store)
                for run in self.runs.list(session):
                    if any(m.id == run.assistant_message_id for m in store.list()):
                        self.runs.reconcile_output(run, store)
            finally:
                self.manager.sessions.detach_runtime(session)
            state["completed_sessions"].append(identifier)
            atomic_json(journal, state)
        state.update(status="completed", ended_at=now())
        atomic_json(journal, state)
        return RecoveryResult(state["completed_sessions"], str(journal), self.plan(project))
