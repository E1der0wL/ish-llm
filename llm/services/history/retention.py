"""History eligibility and explicit deletion; Application owns retention selection."""

from llm.core.history_references import HistoryReferenceSet

from llm.core.paths import SessionPaths
from llm.core.models import ResourceKind

from llm.core.continuations import ResumeRecord

from copy import deepcopy

from datetime import datetime, timezone
from llm.core.contracts import ResourceRef
from llm.core.results import CompletionRecords
from llm.core.steering_channel import SteeringChannel
from llm.core.checkpoint_records import CheckpointDeclarations
from llm.core.checkpoints import CheckpointConversation
from llm.core.plans import RetentionPlan, RetentionUnit
from llm.core.models import Run, Message, ConversationStorage, MessageStatus, now, new_id, RunStatus
from llm.core.json_data import JsonObject
from llm.services.infrastructure.storage import (
    SnapshotFile,
    revision_token,
    atomic_json,
    read_json,
    record,
    reject_links,
    child,
)
from llm.services.runtime.usage import project_usage_policies, protects_project_usage
from llm.services.runtime.requests import RequestAdmission
from .maintenance_records import HistoryMaintenanceStorage
from .retention_records import (
    HistoryMeasurement,
    RetentionCounter,
    RetentionEvidence,
    RetentionIntent,
    RetentionProtection,
    RetentionRow,
    RetentionTarget,
)


class HistoryRetention:
    ABSENT = None
    ZERO = 0
    INCLUDE_DELETED = True
    PERMANENT = True
    INACTIVE_RECOVERY = "Shut down Session runtime before retention recovery"
    SOURCE_CHANGED = "Retention source changed; manual review is required"
    CONVERSATION_CHANGED = "Retention conversation changed; manual review is required"
    DUPLICATE_TARGETS = "History targets must be unique"
    MISSING_VERSION = "History pruning requires a reviewed plan version"
    CONFLICT = "retention_conflict: review a fresh retention plan"
    PROTECTED = "History pruning cannot delete protected or unknown records"
    INCOMPLETE_GROUP = "History pruning requires the complete recipient Run group"

    def __init__(self, manager, counter=ABSENT, *, counter_params=ABSENT):
        self.manager, self.counter = manager, counter
        self.counter_params = {} if counter_params is self.ABSENT else counter_params
        RetentionCounter.validate_parameters(self.counter_params)
        JsonObject.validate(self.counter_params)
        RetentionCounter.validate_selection(counter, self.counter_params)

    @staticmethod
    def _stamps(root):
        return HistoryMeasurement.stamps(root)

    @staticmethod
    def _result(project, unit, candidates, protected, sources, size, tokens):
        # Token estimates select Application targets, never grant deletion authority.
        proof = RetentionEvidence.create(unit, candidates, protected, sources)
        return RetentionPlan(
            ResourceRef(ResourceKind.PROJECT, project.id, project_id=project.id),
            unit,
            [row.record for row in candidates],
            [row.record for row in protected],
            size,
            tokens,
            revision_token(proof),
            unit == RetentionUnit.RUN,
        )

    def _references(self, project):
        return self.manager.components.history_references(project)

    def _owned_references(self, project):
        refs = HistoryReferenceSet(deepcopy(self._references(project)))
        refs.session_ids.update(self.manager.sessions.transfers.protected(project))
        return refs

    def _history(self, session):
        runs = self.manager.sessions.run_repository.list(session)
        messages = self.manager.sessions.conversations(session).list()
        if not self.manager.ownership.session_attached((session.project_id, session.id)) and not any(
            run.status in Run.ACTIVE_STATES for run in runs
        ):
            _, messages, runs = self.manager.sessions.validate_history(session)
        return runs, messages

    def _run_plan(self, project):
        refs = self._owned_references(project)
        usage_policies = project_usage_policies(project, self.manager.sessions)
        rows, protected, sources = [], [], [record(project)]
        clock = datetime.now(timezone.utc)
        total_bytes = total_tokens = self.ZERO
        for session in self.manager.sessions.list(project, include_deleted=self.INCLUDE_DELETED):
            runs, messages = self._history(session)
            sources.extend([record(session), *[record(m) for m in messages]])
            reasons = []
            if self.manager.ownership.session_attached((project.id, session.id)) or session.active_run_ids:
                reasons.append(RetentionProtection.RUNTIME_ATTACHED)
            if project.conversation_storage != ConversationStorage.FILE:
                reasons.append(RetentionProtection.NON_FILE_CONVERSATION)
            if any(m.status in Message.PENDING_STATES for m in messages):
                reasons.append(RetentionProtection.PENDING_MESSAGE)
            if session.retained or session.id in refs.session_ids:
                reasons.append(RetentionProtection.PINNED_OR_COMPONENT_REFERENCE)
            if HistoryMaintenanceStorage.pending(session):
                reasons.append(RetentionProtection.PENDING_MAINTENANCE)
            # 외부 효과 원장은 Run 제거와 독립적으로 수명을 정해야 하므로 보수적으로 유지한다.
            if self.manager.sessions.run_repository.has_tool_operations(session):
                reasons.append(RetentionProtection.EXTERNAL_OPERATION_HISTORY)
            keep = set(refs.run_ids)
            protected_messages = set(refs.message_ids)
            for run in runs:
                sources.append(record(run))
                source = ResumeRecord.source_id(run.metadata)
                if source:
                    keep.add(source)
                if run.status != RunStatus.COMPLETED:
                    for name in CheckpointDeclarations.read(run.metadata).names:
                        checkpoint = self.manager.sessions.run_repository.checkpoint(run, name)
                        sources.append(checkpoint)
                        protected_messages.update(
                            CheckpointConversation.from_checkpoint(checkpoint).message_ids
                        )
                        if SteeringChannel.present(run.metadata):
                            from llm.services.runtime.steering import instruction_records

                            protected_messages.update(
                                mid
                                for _, value in instruction_records(checkpoint)
                                for mid in value.message_ids
                            )
            keep.update(
                identifier
                for m in messages
                if m.id in protected_messages
                for identifier in RequestAdmission.run_ids(m)
            )
            groups = {}
            for run in sorted(runs, key=lambda run: run.agent_id):
                groups.setdefault(run.input_message_id, []).append(run.id)
            session_rows = []
            for run in runs:
                stamps = self._stamps(run.paths.root)
                group_ids = groups[run.input_message_id]
                # Shared User storage is measured once; deletion still requires
                # all recipients, so the original complete map is never rewritten.
                owned = [
                    m
                    for m in messages
                    if m.run_id == run.id or m.id == run.input_message_id and group_ids[0] == run.id
                ]
                size = SnapshotFile.total_bytes(stamps) + HistoryMeasurement.message_bytes(owned)
                tokens = self.ZERO
                if self.counter is not self.ABSENT:
                    tokens = RetentionCounter.estimate(self.counter, self.counter_params, owned)
                total_bytes += size
                total_tokens += tokens
                row = RetentionRow.run(
                    session_id=session.id,
                    run_id=run.id,
                    status=run.status,
                    group_run_ids=group_ids,
                    message_ids=[m.id for m in owned],
                    message_revisions={m.id: revision_token(record(m)) for m in owned},
                    byte_count=size,
                    tokens=tokens,
                    last_activity=run.ended_at or run.created_at,
                    revision=revision_token(stamps),
                    record_revision=revision_token(record(run)),
                )
                blocked = list(reasons)
                if run.status != RunStatus.COMPLETED:
                    blocked.append(RetentionProtection.UNFINISHED_OR_RESUMABLE_RUN)
                if run.id in keep:
                    blocked.append(RetentionProtection.RECENT_OR_REFERENCED)
                if protects_project_usage(CompletionRecords.read(run.metadata), usage_policies, at=clock):
                    blocked.append(RetentionProtection.ACTIVE_USAGE_ACCOUNTING)
                session_rows.append(row.with_reasons(blocked))
            blocked_ids = {row.run_id for row in session_rows if row.reasons}
            for row in session_rows:
                if not row.reasons and blocked_ids.intersection(row.group_run_ids):
                    row.protect([RetentionProtection.PROTECTED_REQUEST_GROUP])
                (protected if row.reasons else rows).append(row)
        return self._result(project, RetentionUnit.RUN, rows, protected, sources, total_bytes, total_tokens)

    def _finish(self, session, path):
        intent = RetentionIntent(read_json(reject_links(path)))
        intent.validate(session)
        if self.manager.ownership.session_attached((session.project_id, session.id)):
            raise ValueError(self.INACTIVE_RECOVERY)
        repository = self.manager.sessions.run_repository
        # rmtree 도중 종료되어 run.json만 사라진 경우도, 승인된 삭제 저널을 통해 마무리한다.
        repository.recover_history_deletions(session, [r.run_id for r in intent.runs])
        for row in intent.runs:
            try:
                run = repository.load(session, row.run_id)
            except FileNotFoundError:
                continue
            if run.status != RunStatus.COMPLETED or revision_token(record(run)) != row.record_revision:
                raise ValueError(self.SOURCE_CHANGED)
        # 의도를 먼저 저장한 뒤 대화를 정리한다. 중단 시 runtime 재접속을 차단한다.
        store = self.manager.sessions.conversations(session)
        versions = {mid: version for row in intent.runs for mid, version in row.message_revisions.items()}
        for message in store.list():
            if message.id in versions and revision_token(record(message)) != versions[message.id]:
                raise ValueError(self.CONVERSATION_CHANGED)
        store.prune(set(versions))
        for row in intent.runs:
            repository.delete_history(session, row.run_id)
        from llm.services.infrastructure.storage import child

        receipt = child(HistoryMaintenanceStorage.receipt_directory(session), intent.id).with_suffix(
            HistoryMaintenanceStorage.RECORD_SUFFIX
        )
        atomic_json(reject_links(receipt), intent.receipt(now()))
        from llm.services.infrastructure.storage import unlink_file

        unlink_file(path)
        return intent.id

    def plan(self, project, *, unit) -> RetentionPlan:
        unit = RetentionUnit(unit)
        if unit == RetentionUnit.RUN:
            return self._run_plan(project)
        refs = self._owned_references(project)
        usage_policies = project_usage_policies(project, self.manager.sessions)
        items, protected, sources = [], [], [record(project)]
        total_bytes = total_tokens = self.ZERO
        now = datetime.now(timezone.utc)
        for session in self.manager.sessions.list(project, include_deleted=self.INCLUDE_DELETED):
            runs, messages = self._history(session)
            sources.extend([record(session), *[record(run) for run in runs], *[record(m) for m in messages]])
            stamps = self._stamps(session.paths.root)
            size = SnapshotFile.total_bytes(stamps)
            tokens = self.ZERO
            if self.counter is not self.ABSENT:
                tokens = RetentionCounter.estimate(self.counter, self.counter_params, messages)
            total_bytes += size
            total_tokens += tokens
            reasons = []
            if (
                session.id in refs.session_ids
                or any(r.id in refs.run_ids for r in runs)
                or any(m.id in refs.message_ids for m in messages)
            ):
                reasons.append(RetentionProtection.COMPONENT_REFERENCE)
            if HistoryMaintenanceStorage.pending(session):
                reasons.append(RetentionProtection.PENDING_MAINTENANCE)
            if self.manager.ownership.session_attached((project.id, session.id)):
                reasons.append(RetentionProtection.RUNTIME_ATTACHED)
            if session.active_run_ids or any(r.status != RunStatus.COMPLETED for r in runs):
                reasons.append(RetentionProtection.UNFINISHED_OR_RESUMABLE_RUN)
            completed = {r.id for r in runs if r.status == RunStatus.COMPLETED}
            if any(
                m.status in Message.PENDING_STATES
                or m.status == MessageStatus.COMMITTED
                and not set(RequestAdmission.run_ids(m)) <= completed
                for m in messages
            ):
                reasons.append(RetentionProtection.PENDING_MESSAGE)
            if self.manager.sessions.run_repository.has_tool_operations(session):
                reasons.append(RetentionProtection.EXTERNAL_OPERATION_HISTORY)
            if session.retained:
                reasons.append(RetentionProtection.PINNED)
            if project.conversation_storage != ConversationStorage.FILE:
                reasons.append(RetentionProtection.NON_FILE_CONVERSATION)
            if protects_project_usage(
                [e for r in runs for e in CompletionRecords.read(r.metadata)], usage_policies, at=now
            ):
                reasons.append(RetentionProtection.ACTIVE_USAGE_ACCOUNTING)
            end = max([r.ended_at or r.created_at for r in runs] + [session.created_at])
            row = RetentionRow.session(
                session_id=session.id,
                byte_count=size,
                tokens=tokens,
                last_activity=end,
                revision=revision_token(stamps),
            )
            if reasons:
                protected.append(row.with_reasons(reasons))
            else:
                items.append(row)
        return self._result(
            project, RetentionUnit.SESSION, items, protected, sources, total_bytes, total_tokens
        )

    def apply(self, project, *, unit, targets, expected_version):
        unit = RetentionUnit(unit)
        RetentionTarget.validate_collection(targets)
        fields = RetentionTarget.fields(unit)
        keys = []
        for target in targets:
            target = RetentionTarget.read(target, fields)
            session_path = child(project.paths.sessions, target.session_id)
            if unit == RetentionUnit.RUN:
                child(SessionPaths(session_path).runs, target.run_id)
            keys.append(target.key(fields))
        if len(keys) != len(set(keys)):
            raise ValueError(self.DUPLICATE_TARGETS)
        if not isinstance(expected_version, str) or not expected_version:
            raise ValueError(self.MISSING_VERSION)
        plan = self.plan(project, unit=unit)
        if not expected_version or expected_version != plan.version:
            raise ValueError(self.CONFLICT)
        eligible = {row.target_key(fields): row for row in RetentionRow.read_many(plan.candidates)}
        if any(key not in eligible for key in keys):
            raise ValueError(self.PROTECTED)
        selected = [eligible[key] for key in keys]
        if unit == RetentionUnit.RUN:
            selected_ids = {(row.session_id, row.run_id) for row in selected}
            if any(
                (row.session_id, identifier) not in selected_ids
                for row in selected
                for identifier in row.group_run_ids
            ):
                raise ValueError(self.INCOMPLETE_GROUP)
            grouped = {}
            for row in selected:
                grouped.setdefault(row.session_id, []).append(row)
            for identifier, rows in grouped.items():
                session = self.manager.sessions.load(project, identifier)
                self.manager.sessions.require_inactive(session)
                path = reject_links(HistoryMaintenanceStorage.retention_path(session))
                atomic_json(path, RetentionIntent.start(new_id(), session.id, now(), rows).record)
                self._finish(session, path)
            return [row.record for row in selected]
        for row in selected:
            session = self.manager.sessions.load(project, row.session_id)
            self.manager.sessions.delete(session, permanent=self.PERMANENT)
        return [row.record for row in selected]

    def recover(self, project):
        """이미 승인·기록된 삭제 의도만 완료한다. 새 보관 후보는 선택하지 않는다."""
        completed = []
        for session in self.manager.sessions.list(project, include_deleted=self.INCLUDE_DELETED):
            path = HistoryMaintenanceStorage.retention_path(session)
            if path.exists():
                completed.append(self._finish(session, path))
        return completed
