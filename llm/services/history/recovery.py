"""도메인 참조 검사와 명시적 복구. 실행을 재생하지 않고 저장 상태만 정리한다."""

from llm.core.models import ResourceKind

from llm.core.results import OutputVisibility
from llm.core.models import RunTransition

from copy import deepcopy
from llm.core.contracts import Diagnostic, ResourceRef
from llm.core.plans import RecoveryPlan, RecoveryResult
from llm.core.models import ConversationStorage, MessageRole, MessageStatus, RunStatus, StepStatus, SessionStatus, now, new_id, validate_run_transition
from llm.services.infrastructure.storage import atomic_json, record, revision_token, reject_links
from llm.services.infrastructure.logging import log_event
from llm.services.lifecycle.steps import StepManager
from llm.core.steering import is_instruction, RunInstruction, InstructionDataError
from llm.services.runtime.requests import RequestAdmission


def recover_session(sessions, repository, steps, session, store):
    """Session 소유권을 확보한 호출자만 사용한다. Runtime 시작과 수동 복구가 같은 규칙을 쓴다."""
    from llm.services.infrastructure.transactions import watch
    from llm.services.runtime.runs import RunErrorCode
    watch(session)
    recovered = []
    messages = {message.id: message for message in store.list()}
    runs = repository.list(session)
    RequestAdmission.history(list(messages.values()), runs, project_id=session.project_id,
        session_id=session.id, persistent=sessions._owner(session).conversation_storage == ConversationStorage.FILE)
    sessions.transfers.validate_session(sessions._owner(session), session, list(messages.values()))
    # 모든 지시를 먼저 읽어 검증한다. 뒤쪽의 손상으로 앞선 Run/Step만 복구되지 않게 한다.
    from llm.services.runtime.steering import instruction_targets, instruction_records
    for message in messages.values():
        if is_instruction(message):
            RunInstruction.validate_message(message)
    for run in runs:
        if run.status in (RunStatus.PENDING, RunStatus.RUNNING):
            instruction_targets(run)
        if "steering" in run.metadata and run.status != RunStatus.COMPLETED:
            for name in run.metadata.get("checkpoints", []):
                # 여기서는 형식을 검사한다. memory 대화의 정상적인 재시작 소실은
                # Session 시작을 막지 않으며 메시지 존재 검사는 명시적 재개가 소유한다.
                list(instruction_records(repository.checkpoint(run, name)))
    for run in runs:
        stale = run.status in (RunStatus.PENDING, RunStatus.RUNNING)
        if stale:
            validate_run_transition(run.status, RunStatus.INTERRUPTED, reason=RunTransition.RECOVERY)
        steps.recover(run)
        if stale:
            if "steering" in run.metadata:
                from llm.services.runtime.steering import finish_instructions, close_targets
                finish_instructions(store, run.id, RunErrorCode.PROCESS_RESTART.value)
                close_targets(run, RunErrorCode.PROCESS_RESTART.value)
            if sessions._owner(session).conversation_storage == ConversationStorage.FILE and run.assistant_message_id in messages:
                repository.reconcile_output(run, store)
            run.status, run.error_code, run.ended_at = RunStatus.INTERRUPTED, RunErrorCode.PROCESS_RESTART.value, now()
            repository.save(run)
            assistant = messages.get(run.assistant_message_id)
            if assistant is not None and assistant.status == MessageStatus.QUEUED:
                store.set_status(assistant.id, MessageStatus.INTERRUPTED)
            log_event(run.paths.logs, "run.recovered", entity_id=run.id, status=run.status)
            recovered.append(deepcopy(run))
    for message in messages.values():
        if message.role == MessageRole.ASSISTANT and message.status == MessageStatus.STREAMING:
            store.set_status(message.id, MessageStatus.INTERRUPTED)
    session.active_run_ids, session.status = [], SessionStatus.IDLE
    sessions._save_runtime(session)
    return recovered


class ProjectRecovery:
    """검사 결과는 원본 fingerprint를 포함한다. 손상/누락을 추측하여 메우지 않는다."""

    JOURNAL_RUNNING = "running"
    JOURNAL_COMPLETED = "completed"


    def __init__(self, manager):
        self.manager = manager
        self.runs = manager.sessions.run_repository
        self.steps = StepManager(manager.backup_steps)

    def plan(self, project) -> RecoveryPlan:
        from llm.services.runtime.runs import RunErrorCode
        issues, actions, signatures = [], [], []
        def issue(code, *, session=None, run=None, detail=None, repairable=False):
            source = ResourceRef(ResourceKind.RUN if run else ResourceKind.SESSION if session else ResourceKind.PROJECT, run or session or project.id,
                                 project_id=project.id, session_id=session, run_id=run)
            issues.append(Diagnostic(code, code.replace("_", " "), source=source,
                                     details={"detail": detail, "repairable": repairable}))
        for session in self.manager.sessions.list(project, include_deleted=True):
            signatures.append(record(session))
            if self.manager.ownership.session_attached((project.id, session.id)):
                issue("runtime_attached", session=session.id)
                continue
            if session.status == SessionStatus.DELETED or project.conversation_storage != ConversationStorage.FILE:
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
                for message in messages.values():
                    if is_instruction(message):
                        RunInstruction.validate_message(message)
                runs = self.runs.list(session)
                for run in runs:
                    source = messages.get(run.input_message_id)
                    if source is None or source.role != MessageRole.USER:
                        issue("invalid_input_reference", session=session.id, run=run.id)
                RequestAdmission.history(list(messages.values()), runs, project_id=project.id,
                    session_id=session.id, persistent=True)
                self.manager.sessions.transfers.validate_session(project, session, list(messages.values()))
                identifiers = {r.id for r in runs}
                signatures.extend(record(m) for m in messages.values())
                signatures.extend(record(r) for r in runs)
                if session.active_run_ids or session.status == SessionStatus.RUNNING:
                    issue("stale_session", session=session.id, repairable=True)
                if any(m.run_id and m.run_id not in identifiers for m in messages.values()):
                    issue("missing_run_reference", session=session.id)
                for run in runs:
                    from llm.services.runtime.steering import instruction_targets
                    instruction_targets(run)
                    stale = run.status in (RunStatus.RUNNING, RunStatus.PENDING)
                    if stale:
                        issue("stale_run", session=session.id, run=run.id, repairable=True)
                    source, assistant = messages.get(run.input_message_id), messages.get(run.assistant_message_id)
                    if source is None or run.id not in RequestAdmission.run_ids(source):
                        issue("invalid_input_reference", session=session.id, run=run.id)
                    if assistant is not None and (assistant.role != MessageRole.ASSISTANT or assistant.run_id != run.id):
                        issue("invalid_assistant_reference", session=session.id, run=run.id)
                    elif assistant is None and not (stale or run.error_code == RunErrorCode.PROCESS_RESTART.value):
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
                        checkpoint_ids = list(checkpoint["header"].get("message_ids", []))
                        if "steering" in run.metadata:
                            from llm.services.runtime.steering import instruction_records
                            checkpoint_ids.extend(mid for _, value in instruction_records(checkpoint) for mid in value.message_ids)
                        if run.status != RunStatus.COMPLETED and any(mid not in messages for mid in checkpoint_ids):
                            issue("missing_checkpoint_message", session=session.id, run=run.id)
                    outputs = self.runs.outputs(run)
                    signatures.append([o.to_dict() if hasattr(o, "to_dict") else str(o) for o in outputs])
                    visible = [o for o in outputs if o.visibility == OutputVisibility.USER]
                    final = next((o for o in visible if o.final and o.step_id is None and (o.text or o.data is None)), None)
                    text = final.text if final else "".join(o.text for o in visible)
                    if outputs and assistant is not None and text != assistant.content:
                        issue("output_gap", session=session.id, run=run.id, repairable=True)
            except InstructionDataError as error:
                issue(error.code, session=session.id, detail=str(error))
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
        return RecoveryPlan(ResourceRef(ResourceKind.PROJECT, project.id, project_id=project.id), issues, actions,
            revision_token({"project": record(project), "sources": signatures, **result}))

    def apply(self, project, expected_version):
        plan = self.plan(project)
        if not expected_version or expected_version != plan.version:
            raise ValueError("recovery_conflict: inspect a fresh recovery plan")
        journal = reject_links(project.paths.state / "recovery" / (new_id() + ".json"))
        state = {"status": ProjectRecovery.JOURNAL_RUNNING, "plan": plan.to_dict(), "completed_sessions": [], "started_at": now()}
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
        state.update(status=ProjectRecovery.JOURNAL_COMPLETED, ended_at=now())
        atomic_json(journal, state)
        return RecoveryResult(state["completed_sessions"], str(journal), self.plan(project))
