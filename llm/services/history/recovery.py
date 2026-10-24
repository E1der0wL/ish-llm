"""도메인 참조 검사와 명시적 복구. 실행을 재생하지 않고 저장 상태만 정리한다."""

from llm.core.steering_channel import SteeringChannel
from llm.core.checkpoint_records import CheckpointDeclarations
from llm.core.checkpoints import CheckpointConversation

from llm.core.models import ResourceKind

from llm.core.results import OutputVisibility
from llm.core.models import RunTransition

from copy import deepcopy
from llm.core.contracts import JsonValue, ResourceRef
from llm.core.plans import RecoveryPlan, RecoveryResult
from llm.core.models import (
    Run,
    Step,
    ConversationStorage,
    MessageRole,
    MessageStatus,
    RunStatus,
    SessionStatus,
    now,
    new_id,
    validate_run_transition,
)
from llm.services.infrastructure.storage import atomic_json, record, revision_token, reject_links
from llm.services.infrastructure.logging import log_event
from llm.services.lifecycle.steps import StepManager
from llm.core.steering import is_instruction, RunInstruction, InstructionDataError
from llm.services.runtime.requests import RequestAdmission
from .maintenance_records import (
    HistoryMaintenanceStorage,
    HistoryIssueCode,
    RecoveryDiagnostic,
    RecoveryEvidence,
    RecoveryProgress,
)


class SessionRecovery:
    ABSENT = None
    RECOVERED_EVENT = "run.recovered"

    @classmethod
    def recover(cls, sessions, repository, steps, session, store):
        """Session 소유권을 확보한 호출자만 사용한다. Runtime 시작과 수동 복구가 같은 규칙을 쓴다."""
        from llm.services.infrastructure.transactions import watch
        from llm.services.runtime.runs import RunErrorCode

        watch(session)
        recovered = []
        messages = {message.id: message for message in store.list()}
        runs = repository.list(session)
        RequestAdmission.history(
            list(messages.values()),
            runs,
            project_id=session.project_id,
            session_id=session.id,
            persistent=sessions._owner(session).conversation_storage == ConversationStorage.FILE,
        )
        sessions.transfers.validate_session(sessions._owner(session), session, list(messages.values()))
        # 모든 지시를 먼저 읽어 검증한다. 뒤쪽의 손상으로 앞선 Run/Step만 복구되지 않게 한다.
        from llm.services.runtime.steering import instruction_targets, instruction_records

        for message in messages.values():
            if is_instruction(message):
                RunInstruction.validate_message(message)
        for run in runs:
            if run.status in Run.ACTIVE_STATES:
                instruction_targets(run)
            if SteeringChannel.present(run.metadata) and run.status != RunStatus.COMPLETED:
                for name in CheckpointDeclarations.read(run.metadata).names:
                    # 여기서는 형식을 검사한다. memory 대화의 정상적인 재시작 소실은
                    # Session 시작을 막지 않으며 메시지 존재 검사는 명시적 재개가 소유한다.
                    list(instruction_records(repository.checkpoint(run, name)))
        for run in runs:
            stale = run.status in Run.ACTIVE_STATES
            if stale:
                validate_run_transition(run.status, RunStatus.INTERRUPTED, reason=RunTransition.RECOVERY)
            steps.recover(run)
            if stale:
                if SteeringChannel.present(run.metadata):
                    from llm.services.runtime.steering import finish_instructions, close_targets

                    finish_instructions(store, run.id, RunErrorCode.PROCESS_RESTART.value)
                    close_targets(run, RunErrorCode.PROCESS_RESTART.value)
                if (
                    sessions._owner(session).conversation_storage == ConversationStorage.FILE
                    and run.assistant_message_id in messages
                ):
                    repository.reconcile_output(run, store)
                run.status, run.error_code, run.ended_at = (
                    RunStatus.INTERRUPTED,
                    RunErrorCode.PROCESS_RESTART.value,
                    now(),
                )
                repository.save(run)
                assistant = messages.get(run.assistant_message_id)
                if assistant is not cls.ABSENT and assistant.status == MessageStatus.QUEUED:
                    store.set_status(assistant.id, MessageStatus.INTERRUPTED)
                log_event(run.paths.logs, cls.RECOVERED_EVENT, entity_id=run.id, status=run.status)
                recovered.append(deepcopy(run))
        for message in messages.values():
            if message.role == MessageRole.ASSISTANT and message.status == MessageStatus.STREAMING:
                store.set_status(message.id, MessageStatus.INTERRUPTED)
        session.active_run_ids, session.status = [], SessionStatus.IDLE
        sessions._save_runtime(session)
        return recovered


def recover_session(sessions, repository, steps, session, store):
    """Runtime and explicit recovery share the same current-state operation."""
    return SessionRecovery.recover(sessions, repository, steps, session, store)


class ProjectRecovery:
    """검사 결과는 원본 fingerprint를 포함한다. 손상/누락을 추측하여 메우지 않는다."""

    JOURNAL_RUNNING = RecoveryProgress.RUNNING
    JOURNAL_COMPLETED = RecoveryProgress.COMPLETED
    ABSENT = None
    EMPTY_TEXT = ""
    INCLUDE_DELETED = True
    PERSISTENT_HISTORY = True
    CONFLICT = "recovery_conflict: inspect a fresh recovery plan"

    def __init__(self, manager):
        self.manager = manager
        self.runs = manager.sessions.run_repository
        self.steps = StepManager(manager.backup_steps)

    def plan(self, project) -> RecoveryPlan:
        from llm.services.runtime.runs import RunErrorCode

        issues, actions, signatures = [], [], []

        def issue(
            code,
            *,
            session=RecoveryDiagnostic.ABSENT,
            run=RecoveryDiagnostic.ABSENT,
            detail=RecoveryDiagnostic.ABSENT,
            repairable=RecoveryDiagnostic.NOT_REPAIRABLE,
        ):
            issues.append(
                RecoveryDiagnostic.create(
                    project.id, code, session=session, run=run, detail=detail, repairable=repairable
                )
            )

        for session in self.manager.sessions.list(project, include_deleted=self.INCLUDE_DELETED):
            signatures.append(record(session))
            if self.manager.ownership.session_attached((project.id, session.id)):
                issue(HistoryIssueCode.RUNTIME_ATTACHED, session=session.id)
                continue
            if (
                session.status == SessionStatus.DELETED
                or project.conversation_storage != ConversationStorage.FILE
            ):
                issue(HistoryIssueCode.OFFLINE_FILE_SESSION_REQUIRED, session=session.id)
                continue
            start = len(issues)
            if HistoryMaintenanceStorage.pending(session):
                issue(HistoryIssueCode.PENDING_RETENTION, session=session.id)
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
                    if source is self.ABSENT or source.role != MessageRole.USER:
                        issue(HistoryIssueCode.INVALID_INPUT_REFERENCE, session=session.id, run=run.id)
                RequestAdmission.history(
                    list(messages.values()),
                    runs,
                    project_id=project.id,
                    session_id=session.id,
                    persistent=self.PERSISTENT_HISTORY,
                )
                self.manager.sessions.transfers.validate_session(project, session, list(messages.values()))
                identifiers = {r.id for r in runs}
                signatures.extend(record(m) for m in messages.values())
                signatures.extend(record(r) for r in runs)
                if session.active_run_ids or session.status == SessionStatus.RUNNING:
                    issue(
                        HistoryIssueCode.STALE_SESSION,
                        session=session.id,
                        repairable=RecoveryDiagnostic.REPAIRABLE_EVIDENCE,
                    )
                if any(m.run_id and m.run_id not in identifiers for m in messages.values()):
                    issue(HistoryIssueCode.MISSING_RUN_REFERENCE, session=session.id)
                for run in runs:
                    from llm.services.runtime.steering import instruction_targets

                    instruction_targets(run)
                    stale = run.status in Run.ACTIVE_STATES
                    if stale:
                        issue(
                            HistoryIssueCode.STALE_RUN,
                            session=session.id,
                            run=run.id,
                            repairable=RecoveryDiagnostic.REPAIRABLE_EVIDENCE,
                        )
                    source, assistant = messages.get(run.input_message_id), messages.get(
                        run.assistant_message_id
                    )
                    if source is self.ABSENT or run.id not in RequestAdmission.run_ids(source):
                        issue(HistoryIssueCode.INVALID_INPUT_REFERENCE, session=session.id, run=run.id)
                    if assistant is not self.ABSENT and (
                        assistant.role != MessageRole.ASSISTANT or assistant.run_id != run.id
                    ):
                        issue(HistoryIssueCode.INVALID_ASSISTANT_REFERENCE, session=session.id, run=run.id)
                    elif assistant is self.ABSENT and not (
                        stale or run.error_code == RunErrorCode.PROCESS_RESTART.value
                    ):
                        issue(HistoryIssueCode.MISSING_ASSISTANT, session=session.id, run=run.id)
                    if assistant is not self.ABSENT and assistant.status == MessageStatus.STREAMING:
                        issue(
                            HistoryIssueCode.STALE_ASSISTANT,
                            session=session.id,
                            run=run.id,
                            repairable=RecoveryDiagnostic.REPAIRABLE_EVIDENCE,
                        )
                    steps = self.steps.list(run)
                    signatures.extend(record(s) for s in steps)
                    if any(s.status in Step.ACTIVE_STATES for s in steps):
                        issue(
                            HistoryIssueCode.STALE_STEP,
                            session=session.id,
                            run=run.id,
                            repairable=RecoveryDiagnostic.REPAIRABLE_EVIDENCE,
                        )
                    for name in CheckpointDeclarations.read(run.metadata).names:
                        checkpoint = self.runs.checkpoint(run, name)
                        signatures.append(checkpoint)
                        checkpoint_ids = list(CheckpointConversation.from_checkpoint(checkpoint).message_ids)
                        if SteeringChannel.present(run.metadata):
                            from llm.services.runtime.steering import instruction_records

                            checkpoint_ids.extend(
                                mid
                                for _, value in instruction_records(checkpoint)
                                for mid in value.message_ids
                            )
                        if run.status != RunStatus.COMPLETED and any(
                            mid not in messages for mid in checkpoint_ids
                        ):
                            issue(HistoryIssueCode.MISSING_CHECKPOINT_MESSAGE, session=session.id, run=run.id)
                    outputs = self.runs.outputs(run)
                    signatures.append(
                        [o.to_dict() if hasattr(o, JsonValue.to_dict.__name__) else str(o) for o in outputs]
                    )
                    visible = [o for o in outputs if o.visibility == OutputVisibility.USER]
                    final = next(
                        (
                            o
                            for o in visible
                            if o.final and o.step_id is self.ABSENT and (o.text or o.data is self.ABSENT)
                        ),
                        self.ABSENT,
                    )
                    text = final.text if final else self.EMPTY_TEXT.join(o.text for o in visible)
                    if outputs and assistant is not self.ABSENT and text != assistant.content:
                        issue(
                            HistoryIssueCode.OUTPUT_GAP,
                            session=session.id,
                            run=run.id,
                            repairable=RecoveryDiagnostic.REPAIRABLE_EVIDENCE,
                        )
            except InstructionDataError as error:
                issue(error.code, session=session.id, detail=str(error))
            except (ValueError, OSError, KeyError, TypeError) as error:
                issue(HistoryIssueCode.UNREADABLE_HISTORY, session=session.id, detail=str(error))
            found = issues[start:]
            if found and all(RecoveryDiagnostic.repairable(i) for i in found):
                actions.append(session.id)
        for name in project.components:
            try:
                self.manager.components.get(name).validate_backup(project)
            except (ValueError, OSError, KeyError, TypeError) as error:
                issue(
                    HistoryIssueCode.COMPONENT_INTEGRITY,
                    detail=RecoveryDiagnostic.component_detail(name, error),
                )
        result = RecoveryEvidence.result(issues, actions)
        return RecoveryPlan(
            ResourceRef(ResourceKind.PROJECT, project.id, project_id=project.id),
            issues,
            actions,
            revision_token(RecoveryEvidence.sources(record(project), signatures, result)),
        )

    def apply(self, project, expected_version):
        plan = self.plan(project)
        if not expected_version or expected_version != plan.version:
            raise ValueError(self.CONFLICT)
        journal = reject_links(HistoryMaintenanceStorage.recovery_path(project, new_id()))
        state = RecoveryProgress.start(plan.to_dict(), now())
        atomic_json(journal, state.record)
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
            state.complete_session(identifier)
            atomic_json(journal, state.record)
        state.finish(now())
        atomic_json(journal, state.record)
        return RecoveryResult(state.completed_sessions, str(journal), self.plan(project))
