"""Session의 단일 Dispatcher로 같은 Agent는 FIFO, 다른 Agent는 병렬 실행한다. 입력을 영속 접수하고 Run별 이벤트 저장·취소·복구를 소유한다. 중단된 Run은 자동 재실행하지 않는다."""

from llm.services.infrastructure.storage import record_exists, record_children, record_directory

from llm.services.runtime.subscription_state import EventChannel
from .run_values import (
    RuntimeGauges,
    RunRecordIdentity,
    RunInteractionReadScope,
    RunCheckpointLineage,
    RunCheckpointPlanEvidence,
    RunObservedFailure,
    RunLogEvent,
    RuntimeLogEvent,
)
from llm.core.checkpoint_records import CheckpointSnapshot, CheckpointStep
from llm.core.models import Step
from llm.core.results import OutputContract
from llm.services.runtime.output import OutputQueue
from llm.components.base import Component
from llm.components.tools.resolver import CapabilityResolverHooks
from llm.engines.contracts import EngineContextBinding
from llm.core.interactions import InteractionOperation
from llm.services.infrastructure.storage import SnapshotFile

from llm.core.execution_observations import ExecutionObservations
from llm.core.steering_channel import SteeringChannel
from llm.core.instruction_records import InstructionMessageRecord, InstructionReceipt
from llm.core.steering_events import SteeringEventData, SteeringOperation, SteeringNotification
from llm.core.checkpoint_records import CheckpointContent, CheckpointRecords, CheckpointPause

from llm.core.checkpoints import CheckpointConversation
from llm.core.checkpoint_records import (
    CheckpointDeclarations,
    CheckpointDeletion,
    CheckpointInteractions,
    CheckpointWrite,
)

from llm.core.models import StepErrorCode

from llm.core.contracts import ActivityEventType, ProjectActivityEvent

from llm.core.plans import PlanBlockerCode

from llm.core.models import ResourceKind

from llm.core.continuations import (
    RestartRecord,
    ResumeRecord,
    RestartEvidence,
    ResumePlanEvidence,
    RunExecutionEvidence,
)

from llm.core.checkpoints import CheckpointErrorCode
from llm.core.results import OutputOperation, OutputVisibility
from llm.core.models import RunErrorCode, RunTransition

from llm.services.infrastructure.storage import read_domain_record, atomic_domain_json

from llm.services.query import FileCatalog, Query, select, select_ordered
from llm.services.infrastructure.journal import OutputJournal
from llm.providers.calls import ProviderCalls
from llm.services.runtime.output import OutputBuffer, OutputProjection, RunOutputState, consume_events
from typing import Optional, Union
import asyncio
import json
import time
from contextlib import closing, contextmanager
from contextvars import ContextVar
from collections.abc import Callable
from itertools import count
from copy import deepcopy
from dataclasses import asdict, dataclass, replace

from llm.core.models import (
    ConversationStorage,
    Message,
    MessageRole,
    MessageStatus,
    Project,
    Run,
    RunStatus,
    StepStatus,
    Session,
    SessionStatus,
    new_id,
    now,
    validate_run_transition,
)
from llm.core.paths import RunPaths
from llm.core.configuration import UNSET
from llm.core.agents import AgentBinding
from llm.services.runtime.agents import RunAgentResolver
from llm.services.runtime.requests import RequestAdmission
from llm.core.interactions import InteractionActor, InteractionRequest, InteractionResponse, InteractionView
from llm.engines.base import (
    EngineContext,
    EngineEvent,
    EngineEventType,
    SteeringInbox,
    required_capabilities,
    steering_mode,
)
from llm.engines.contracts import EngineHooks
from llm.core.steering import (
    InstructionStatus,
    RunInstruction,
    SteeringMode,
    SteeringRoute,
    is_instruction,
    is_queued_request,
)
from llm.services.runtime.steering import (
    finish_instructions,
    checkpoint_messages,
    steering_boundary,
    close_targets,
    reservation_receipts,
    resume_instructions,
)
from llm.engines.registry import EngineRegistry
from llm.components.tools import ToolRegistry
from llm.services.history.conversation import Conversation
from llm.services.history.context import ConversationContextBuilder
from llm.components.registry import ComponentRegistry
from llm.components.tools.resolver import CapabilityResolver, ComponentToolResolver
from llm.services.lifecycle.steps import StepEventRecorder, StepManager
from llm.services.infrastructure.storage import (
    StorageIO,
    child,
    drain_on_cancel,
    record,
    revision_token,
)
from llm.services.lifecycle.sessions import SessionManager, SessionRuntime, RunRuntime
from llm.services.infrastructure.logging import log_event
from llm.services.infrastructure.activity import activity_event
from llm.services.results import RunResultQuery
from llm.core.contracts import Diagnostic, ResourceRef
from llm.errors import CodedError, stable_error_code
from llm.core.plans import ResumePlan, ResumeState
from llm.core.plans import CheckpointPlan
from llm.core.checkpoints import (
    ResumeAbandonment,
    require_checkpoint_plan,
    CheckpointError,
    CheckpointStatus,
    CheckpointAction,
)
from llm.core.results import CompletionRecords, CompletionResult, EngineOutput, EngineDelta
from enum import StrEnum
from asyncio import timeout
from llm.policies import ExecutionLimitError
from llm.services.runtime.policies import ProjectPolicyResolver, TokenCounters
from llm.services.runtime.tools import ToolRuntime, ToolExecutionScope, ToolUsageError
from llm.services.runtime.operations import OperationRepository, ToolOperations
from llm.services.runtime.events import EventHandlers, EventContext, EventSubscriptions
from llm.services.runtime.checkpoints import CheckpointRepository, checkpoint_digest, checkpoint_message_ids
from llm.services.runtime.interactions import InteractionRepository
from llm.services.runtime.pending import PendingWork
from llm.services.runtime.usage import UsageScope, check_admission, completion_usage, INCLUDE_DELETED_EVIDENCE
from llm.core.policies import (
    UsagePolicy,
    UsageErrorCode,
    ApprovalPolicy,
    PolicySection,
    RunPolicySnapshot,
    RunPolicy,
)
from llm.core.usage import UsageSource, UsageReceipt, UsageCall
from llm.services.infrastructure.observability import ObservationKind, ObservationStatus


# 실행 메타데이터를 저장한다. Facade와 실행 서비스가 같은 인스턴스를 공유한다.
class RunRepository:
    """Run metadata and paths beneath the owning Session's runs root."""

    ABSENT = None
    ENTRY_PATTERN = "*"
    HIDDEN_PREFIX = "."
    EMPTY_ORDERING = ""
    CREATE = True
    UPDATE = False
    RECONCILED = True
    UNCHANGED = False
    CONFIRM_DECISIONS = False
    EXPLICIT_TRUE = True
    ABANDONED = True
    RETAINED = False
    RUNTIME_DETACHED = False
    LINEAGE_EXPANDED = True
    LINEAGE_STABLE = False
    EXCLUDE_RUNNING = False
    LINEAGE_REQUIRED = "Run {identifier} still requires its resume lineage"
    INTERACTION_BINDING = "Interaction checkpoint binding mismatch"
    DUPLICATE_INTERACTION = "Duplicate interaction identity"
    INVALID_LINEAGE = "Invalid resume lineage"
    CYCLIC_LINEAGE = "Cyclic resume lineage"
    CHANGED_CHECKPOINT = "Resume checkpoint changed before initialization"
    CHECKPOINT_NAME = "Resume checkpoint name mismatch"
    INVALID_PARENT = "Invalid resume checkpoint parent"
    OWNER_MISMATCH = "Run ownership mismatch"
    MISSING_RUN = "Missing referenced Run record"
    UNFINISHED_HISTORY = "Only completed Run history can be pruned"
    BACKUP_OWNER = "Backup Run ownership mismatch"
    UNFINISHED_BACKUP = "Backup contains an unfinished Run"
    ALREADY_EXISTS = "Run storage already exists"
    MISSING_RECORD = "Existing Run directory requires its stored record"
    OUTPUT_OWNER = "Output reconciliation ownership mismatch"
    CANCELLED_INTERACTION = "Interaction is cancelled; renew before resuming"
    INVALID_ABANDONMENT = "Invalid resume abandonment record"
    RESUME_ABANDONED = "Resume was abandoned"
    CHECKPOINTS_DELETED = "Checkpoints were explicitly deleted"
    NOT_ABANDONABLE = "Only an inactive resumable Run can be abandoned"
    MAINTENANCE_RUNTIME = "Shut down Session runtime before checkpoint maintenance"
    MAINTENANCE_ACTIVE = "Run is still active; recover stale Runs before maintenance"
    MAINTENANCE_REQUESTS = "Finish or cancel pending requests before checkpoint maintenance"
    MAINTENANCE_RESERVED = "A resume request still references this Run"
    NO_CHECKPOINTS = "Run has no retained checkpoints"
    ABANDON_REQUIRED = "Only an un-abandoned inactive resumable Run can be abandoned"
    DELETE_REQUIRES_ABANDON = "Abandon resume before deleting checkpoints"
    CHANGED_INTERACTION = "Interaction changed; reload before editing"
    UNKNOWN_INTERACTION = "Unknown interaction operation"
    CHECKPOINT_OWNER = "Checkpoint Run ownership mismatch"
    INVALID_QUERY = "Result selection requires Query or None"

    def __init__(
        self,
        *,
        output_index_stride: int = OutputJournal.DEFAULT_INDEX_STRIDE,
        catalog_size: int = FileCatalog.DEFAULT_MAX_ENTRIES,
    ):
        self._interaction_reads = ContextVar(
            RunInteractionReadScope.NAME, default=RunInteractionReadScope.ABSENT
        )
        self.output_journal = OutputJournal(index_stride=output_index_stride)
        self.catalog = FileCatalog(catalog_size)
        self.result_catalog = FileCatalog(catalog_size, projection=Run.index_record)
        self.usage_catalog = FileCatalog(catalog_size, projection=self._usage_record)

    def _usage_record(self, data):
        run = Run.from_record(data, RunRepository.ABSENT)
        return run.session_id, CompletionRecords.usage(run.metadata)

    @staticmethod
    def completion_records(metadata):
        """A missing receipt container is not evidence of zero model usage."""
        return CompletionRecords.read(metadata)

    @contextmanager
    def _interaction_scope(self, session, run, resolve=ABSENT):
        """공개 저장소 확장점을 그대로 호출하면서 같은 잠금 작업의 읽기만 공유한다."""
        key = (session.paths.root, run.id)
        current = self._interaction_reads.get()
        if current is not RunRepository.ABSENT and current.key == key:
            yield current.readers
            return
        resolve = resolve or self._checkpoint_reader(session, [run])
        with InteractionRepository().read_scope() as interactions:
            token = self._interaction_reads.set(
                RunInteractionReadScope(session.paths.root, run.id, interactions, resolve)
            )
            try:
                yield interactions, resolve
            finally:
                self._interaction_reads.reset(token)

    def _checkpoint_reader(self, session, runs):
        """잠금 작업 한 번의 Run 목록/체크포인트만 재사용한다. 영속 캐시는 만들지 않는다."""
        current = self._interaction_reads.get()
        if current is not RunRepository.ABSENT and current.session_root == session.paths.root:
            return current.resolve
        by_id = {run.id: run for run in runs}
        checkpoints, resolved = {}, {}

        def load(run, name):
            key = (run.id, name)
            if key not in checkpoints:
                checkpoints[key] = self.checkpoint(run, name)
            return checkpoints[key]

        def load_run(identifier):
            if identifier not in by_id:
                by_id[identifier] = self.load(session, identifier)
            return by_id[identifier]

        def resolve(run, name):
            key = (run.id, name)
            if key not in resolved:
                resolved[key] = self._resolve_checkpoint(run, name, load_run, load)
            return resolved[key]

        return resolve

    def _interaction_requests(self, run, interactions, resolve):
        deleted = CheckpointRepository().deletion(run)
        if deleted is not RunRepository.ABSENT:
            return list(deleted.interactions)
        names = CheckpointDeclarations.reference_names(run.metadata)
        requests = []
        for name in names:
            values = interactions.requests(
                CheckpointInteractions.from_snapshot(resolve(run, name), name), run
            )
            values = interactions.effective(run, values)
            if any(v.checkpoint_binding.checkpoint != name for v in values):
                raise ValueError(RunRepository.INTERACTION_BINDING)
            requests.extend(values)
        if len({v.id for v in requests}) != len(requests):
            raise ValueError(RunRepository.DUPLICATE_INTERACTION)
        return requests

    def _resume_references(self, records, resolve) -> list[tuple[Run, set[str], str]]:
        """미완료 체인별 (마지막 Run, 보호 메시지 ID, 체크포인트 지문)을 반환한다.

        호출자는 workspace 잠금을 소유해야 한다. 체크포인트가 손상되면
        보호할 참조를 추측하지 않고 거부한다. 초기 복사 전 실패도 resolve_checkpoint로 읽는다.
        """
        from graphlib import TopologicalSorter

        runs = {run.id: run for run in records}
        parents = {
            run.id: ResumeRecord.require(run.metadata).source_run_id
            for run in runs.values()
            if ResumeRecord.present(run.metadata)
        }
        if any(
            parent not in runs or runs[parent].engine != runs[identifier].engine
            for identifier, parent in parents.items()
        ):
            raise ValueError(RunRepository.INVALID_LINEAGE)
        # 순환된 이력에서 모든 Run을 '후속 실행 있음'으로 오인하여 보호를 풀지 않는다.
        tuple(
            TopologicalSorter(
                {identifier: (parents[identifier],) if identifier in parents else () for identifier in runs}
            ).static_order()
        )
        superseded = set(parents.values())
        references = []
        for run in runs.values():
            if run.id in superseded or run.status not in (
                RunStatus.PAUSED,
                RunStatus.FAILED,
                RunStatus.INTERRUPTED,
            ):
                continue
            if self.resume_abandoned(run):
                continue
            names = CheckpointDeclarations.reference_names(run.metadata)
            protected, checkpoints = set(), []
            for name in names:
                checkpoint = resolve(run, name)
                protected.add(run.input_message_id)
                protected.update(checkpoint_message_ids(checkpoint))
                checkpoints.append([name, checkpoint])
            if protected:
                references.append(
                    (run, protected, checkpoint_digest(RunCheckpointLineage(checkpoints).to_dict()))
                )
        return references

    def _resolve_checkpoint(self, run, name, load_run, load_checkpoint) -> dict:
        """초기 복사 전에 끝난 재개 시도만 검증된 원본을 따라 복원한다."""
        selected, visited = run, set()
        chain = []
        while True:
            if selected.id in visited:
                raise ValueError(RunRepository.CYCLIC_LINEAGE)
            visited.add(selected.id)
            try:
                checkpoint = load_checkpoint(selected, name)
                break
            except FileNotFoundError:
                descriptor = ResumeRecord.from_metadata(selected.metadata)
                if (
                    descriptor is RunRepository.ABSENT
                    or name in CheckpointDeclarations.read(selected.metadata).names
                ):
                    raise
                if descriptor.checkpoint != name:
                    raise ValueError(RunRepository.CHECKPOINT_NAME)
                parent = load_run(descriptor.source_run_id)
                if parent.engine != run.engine or parent.status not in (
                    RunStatus.PAUSED,
                    RunStatus.FAILED,
                    RunStatus.INTERRUPTED,
                ):
                    raise ValueError(RunRepository.INVALID_PARENT)
                chain.append((selected, descriptor))
                selected = parent
        for attempt, descriptor in reversed(chain):
            if checkpoint_digest(checkpoint) != descriptor.digest:
                raise ValueError(RunRepository.CHANGED_CHECKPOINT)
            checkpoint = CheckpointSnapshot.for_resume_attempt(checkpoint, attempt)
        return checkpoint

    def completion_history(self, session):
        """프로젝트 한도 검사에는 사용량 투영만 읽고 전체 Run 객체/조회 로그를 만들지 않는다."""
        if type(self).list is not RunRepository.list or type(self).load is not RunRepository.load:
            return [entry for run in self.list(session) for entry in self.completion_records(run.metadata)]
        entries = []
        for path in self.record_paths(session):
            self.paths(session, path.parent.name)
            owner, values = self.usage_catalog.read(path)
            if owner != session.id:
                raise ValueError(RunRepository.OWNER_MISMATCH)
            entries.extend(deepcopy(values))
        return entries

    def record_paths(self, session):
        """Discover owned Run directories, including records missing from damaged trees."""
        from llm.services.infrastructure.storage import reject_links
        from llm.services.history.conversation import ConversationStore

        paths = {}
        for directory in record_children(session.paths.runs) if record_exists(session.paths.runs) else ():
            reject_links(directory)
            if record_directory(directory) and not directory.name.startswith(RunRepository.HIDDEN_PREFIX):
                paths[directory.name] = self.paths(session, directory.name).record
        required = set(session.active_run_ids)
        # File conversations retain independent witnesses even if an entire Run tree is lost.
        # Memory conversations need not have this file; a missing optional file is not created.
        reject_links(session.paths.conversation)
        if record_exists(session.paths.conversation):
            required.update(
                run_id
                for message in ConversationStore(session.paths.conversation).list()
                for run_id in RequestAdmission.run_ids(message)
            )
        if not required <= paths.keys():
            raise ValueError(RunRepository.MISSING_RUN)
        # Directory restoration can change enumeration order without changing
        # records. Reviewed retention/recovery revisions must stay deterministic.
        yield from (paths[identifier] for identifier in sorted(paths))

    def delete_history(self, session, run_id):
        """보관 서비스가 승인한 완료 Run의 기록만 삭제한다. 재실행/상태 전이는 하지 않는다."""
        from llm.services.infrastructure.storage import remove_named_tree

        paths = self.paths(session, run_id)
        if not paths.root.exists():
            return
        run = self.load(session, run_id)
        if run.status != RunStatus.COMPLETED:
            raise ValueError(RunRepository.UNFINISHED_HISTORY)
        remove_named_tree(session.paths.runs, paths.root, run_id)

    def recover_history_deletions(self, session, run_ids):
        from llm.services.infrastructure.storage import recover_deletions

        for identifier in run_ids:
            self.paths(session, identifier)
        return recover_deletions(session.paths.runs, allowed=set(run_ids))

    def read_backup(self, session):
        """자신이 소유하는 Run/출력/체크포인트만 읽어서 검증한다."""
        runs = []
        for path in self.record_paths(session):
            data = read_domain_record(path)
            identity = RunRecordIdentity(data)
            if child(session.paths.runs, identity.id) != path.parent or identity.session_id != session.id:
                raise ValueError(RunRepository.BACKUP_OWNER)
            run = Run.from_record(data, RunPaths(path.parent))
            self.completion_records(run.metadata)
            if run.status in Run.ACTIVE_STATES:
                raise ValueError(RunRepository.UNFINISHED_BACKUP)
            self.output_events(run)
            CheckpointRepository().deletion(run)
            self.resume_abandoned(run)
            for name in CheckpointRepository().names(run):
                self.checkpoint(run, name)
            runs.append(run)
        resolve = self._checkpoint_reader(session, runs)
        for run in runs:
            with self._interaction_scope(session, run, resolve) as (interactions, _):
                interactions.validate_backup(run)
                self.interaction_requests(session, run)
                self.interaction_responses(session, run)
        return runs

    def paths(self, session: Session, run_id: str) -> RunPaths:
        return RunPaths(child(session.paths.runs, run_id))

    def claim_tool_operation(self, session, call):
        return OperationRepository().claim(session, call)

    def has_tool_operations(self, session):
        return OperationRepository().has_records(session)

    def complete_tool_operation(self, session, call, result):
        return OperationRepository().complete(session, call, result)

    def fail_tool_operation(self, session, call, evidence):
        return OperationRepository().not_applied(session, call, evidence)

    def tool_operation(self, session, key):
        return OperationRepository().load(session, key)

    def reconcile_tool_operation(self, session, key, *, result, evidence):
        return OperationRepository().reconcile(session, key, result=result, evidence=evidence)

    def verify_tool_operation(self, session, key, observation, expected_version):
        return OperationRepository().verify(session, key, observation, expected_version)

    def create(self, run: Run) -> None:
        if record_exists(run.paths.root):
            raise FileExistsError(RunRepository.ALREADY_EXISTS)
        CompletionRecords.initialize(run.metadata)
        self._write(run, creating=RunRepository.CREATE)

    def save(self, run: Run) -> None:
        path = run.paths.record
        if not record_exists(path):
            raise ValueError(RunRepository.MISSING_RECORD)
        prior = Run.from_record(read_domain_record(path), run.paths)
        if prior.id != run.id or prior.session_id != run.session_id:
            raise ValueError(RunRepository.OWNER_MISMATCH)
        AgentBinding.validate_run_update(prior, run)
        if prior.status != run.status:
            validate_run_transition(prior.status, run.status)
        CompletionRecords.validate_update(prior.metadata, run.metadata)
        self._write(run, creating=RunRepository.UPDATE)

    def _write(self, run: Run, *, creating: bool) -> None:
        path = run.paths.record
        CompletionRecords.finalize(run)
        data = record(run)
        Run.from_record(data, run.paths)
        CheckpointRepository().names(run)
        if not creating:
            self.output_journal.validate(RunPaths.output_path(run.paths.state))
        atomic_domain_json(path, data)
        if creating:
            self.output_journal.initialize(RunPaths.output_path(run.paths.state))
        log_event(run.paths.logs, RunLogEvent.SAVED, entity_id=run.id, status=run.status)

    def load(self, session: Session, run_id: str) -> Run:
        paths = self.paths(session, run_id)
        data = read_domain_record(paths.record)
        identity = RunRecordIdentity(data)
        if identity.id != run_id or identity.session_id != session.id:
            raise ValueError(RunRepository.OWNER_MISMATCH)
        log_event(paths.logs, RunLogEvent.LOADED, entity_id=run_id)
        run = Run.from_record(data, paths)
        self.completion_records(run.metadata)
        self.resume_abandoned(run)
        CheckpointRepository().deletion(run)
        CheckpointRepository().names(run)
        self.output_journal.validate(RunPaths.output_path(run.paths.state))
        return run

    def record_output(self, run: Run, value: Union[EngineDelta, EngineOutput]) -> None:
        self.output_journal.append(RunPaths.output_path(run.paths.state), [value])

    def record_outputs(self, run: Run, values) -> None:
        """묶음 쓰기 확장점. 단일 쓰기를 재정의한 사용자 저장소의 계약도 유지한다."""
        if type(self).record_output is not RunRepository.record_output:
            for value in values:
                self.record_output(run, value)
        else:
            self.output_journal.append(RunPaths.output_path(run.paths.state), values)

    def output_events(
        self, run: Run, *, after: int = OutputContract.INITIAL_SEQUENCE, limit: Optional[int] = ABSENT
    ):
        return self.output_journal.read(RunPaths.output_path(run.paths.state), after=after, limit=limit)

    def reconcile_output(self, run, store):
        """저널이 Conversation보다 앞선 부분 저장을 복구한다. 실행 효과나 Run 성공 상태는 재현하지 않는다."""
        values = self.outputs(run)
        if not values:
            return RunRepository.UNCHANGED
        visible = [v for v in values if v.visibility == OutputVisibility.USER]
        final = next(
            (
                v
                for v in visible
                if v.final
                and v.step_id is RunRepository.ABSENT
                and (v.text or v.data is RunRepository.ABSENT)
            ),
            RunRepository.ABSENT,
        )
        text = (
            final.text
            if final is not RunRepository.ABSENT
            else OutputContract.EMPTY_TEXT.join(v.text for v in visible)
        )
        message = store.get(run.assistant_message_id)
        if message.run_id != run.id or message.role != MessageRole.ASSISTANT:
            raise ValueError(RunRepository.OUTPUT_OWNER)
        if message.content == text:
            return RunRepository.UNCHANGED
        store.reconcile(message.id, text, run_id=run.id)
        return RunRepository.RECONCILED

    def outputs(self, run: Run) -> list[EngineOutput]:
        """부분/확정 출력을 ID별로 투영한다. Run/Step 상태는 별도로 조회한다."""
        projection = OutputProjection()
        # 사용자 저장소/저널의 기존 읽기 확장점을 우회하지 않는다.
        if (
            type(self).output_events is RunRepository.output_events
            and type(self.output_journal).read is OutputJournal.read
        ):
            with closing(self.output_journal.iter_events(RunPaths.output_path(run.paths.state))) as events:
                for value in events:
                    projection.add(value)
        else:
            for value in self.output_events(run):
                projection.add(value)
        return list(projection.snapshot().values())

    def record_checkpoint(self, run: Run, event: EngineEvent) -> None:
        interactions = InteractionRepository()
        write = CheckpointWrite(event.metadata)
        name, initialize = write.name, write.initialize
        requests = interactions.requests(write.interactions(), run)
        previous = []
        if requests and not initialize:
            previous = interactions.requests(
                CheckpointInteractions.from_snapshot(self.checkpoint(run, name), name), run
            )
        known = {request.id for request in previous}
        for request in requests:
            interactions.publish(
                run,
                request,
                existing=request.id in known or initialize and ResumeRecord.present(run.metadata),
            )
        CheckpointRepository().record(run, event)
        # File, manifest and Run revision commit before the Engine receives ACK.
        # Only a failed initial copy may resolve the verified source checkpoint.
        self.save(run)

    def inherit_interactions(self, session, run, checkpoint):
        """Preserve control evidence even if the later checkpoint copy fails."""
        source = self.load(session, ResumeRecord.require(run.metadata).source_run_id)
        InteractionRepository().inherit(
            source,
            run,
            CheckpointInteractions.from_snapshot(checkpoint, ResumeRecord.checkpoint_name(run.metadata)),
        )

    def instructions(self, session, run, store):
        """본문은 선택한 ConversationStore에서 읽는다. 복제 저장소를 만들지 않는다."""
        inherited = set()
        if SteeringChannel.present(run.metadata):
            for name in CheckpointDeclarations.read(run.metadata).names:
                inherited.update(checkpoint_messages(store, self.checkpoint(run, name)))
        values = [(m.id, RunInstruction.from_message(m)) for m in store.list() if is_instruction(m)]
        return [value for identifier, value in values if identifier in inherited or value.run_id == run.id]

    def interaction_requests(self, session: Session, run: Run) -> list[InteractionRequest]:
        with self._interaction_scope(session, run) as (interactions, resolve):
            return self._interaction_requests(run, interactions, resolve)

    def interaction_responses(self, session: Session, run: Run) -> list[InteractionResponse]:
        with self._interaction_scope(session, run) as (interactions, _):
            requests = self.interaction_requests(session, run)
            return interactions.responses(run, requests)

    def respond(self, session: Session, run: Run, response: InteractionResponse) -> InteractionResponse:
        self.require_resume(run)
        with self._interaction_scope(session, run) as (interactions, _):
            requests = self.interaction_requests(session, run)
            return interactions.respond(run, requests, response)

    def interaction_decisions(
        self,
        session: Session,
        run: Run,
        checkpoint: dict,
        explicit: dict,
        *,
        retry_nodes=(),
        confirm=CONFIRM_DECISIONS,
    ):
        """조회와 실행이 같은 응답 저장소를 사용한다. 주입된 저장소도 이 경로를 공유한다."""
        self.require_resume(run)
        with self._interaction_scope(session, run) as (interactions, _):
            requests = self.interaction_requests(session, run)
            if any(interactions.control(run, r).cancelled for r in requests):
                raise ValueError(RunRepository.CANCELLED_INTERACTION)
            return interactions.decisions(
                requests,
                self.interaction_responses(session, run),
                explicit,
                retry_nodes=retry_nodes,
                confirm=confirm,
            )

    def apply_interaction_policy(self, session, run, policy):
        """정책 응답만 저장한다. 실행 재개와 호스트 허용 여부 검사는 별도다."""
        policy = ApprovalPolicy.from_dict(policy)
        if not policy.enabled:
            return []
        with self._interaction_scope(session, run) as (interactions, _):
            requests = self.interaction_requests(session, run)
            answered = {r.request_id for r in self.interaction_responses(session, run)}
            saved = []
            for request in requests:
                if (
                    request.id in answered
                    or request.expired is RunRepository.EXPLICIT_TRUE
                    or request.risk is RunRepository.ABSENT
                    or request.risk_scheme != policy.risk_scheme
                    or request.category == InteractionRepository.RETRY_CATEGORY
                    or interactions.control(run, request).cancelled is RunRepository.EXPLICIT_TRUE
                ):
                    continue
                for rule in policy.rules:
                    if rule.category == request.category and request.risk <= rule.max_risk:
                        option = request.find_approval()
                        if option is not RunRepository.ABSENT:
                            response = replace(
                                request.respond(option.id), actor=InteractionActor.POLICY, policy_id=rule.id
                            )
                            saved.append(self.respond(session, run, response))
                        break
        return saved

    def resume_link(self, session, run, store, *, messages=ABSENT, records=ABSENT):
        message = next(
            (
                m
                for m in (store.list() if messages is RunRepository.ABSENT else messages)
                if ResumeRecord.source_id(m.metadata) == run.id and m.status != MessageStatus.CANCELLED
            ),
            RunRepository.ABSENT,
        )
        resumed = next(
            (
                r
                for r in (self.list(session) if records is RunRepository.ABSENT else records)
                if ResumeRecord.source_id(r.metadata) == run.id
            ),
            RunRepository.ABSENT,
        )
        return message, resumed

    def resume_abandoned(self, run: Run) -> bool:
        """서비스가 기록한 재개 포기만 해석한다. 손상된 기록은 보호 해제로 추측하지 않는다."""
        if ResumeAbandonment.read(run.metadata) is RunRepository.ABSENT:
            return RunRepository.RETAINED
        ResumeAbandonment.require(run.metadata)
        if run.status not in Run.RESUMABLE_STATES:
            raise ValueError(RunRepository.INVALID_ABANDONMENT)
        return RunRepository.ABANDONED

    def require_resume(self, run: Run) -> None:
        if self.resume_abandoned(run):
            raise ValueError(RunRepository.RESUME_ABANDONED)
        if CheckpointRepository().deletion(run) is not RunRepository.ABSENT:
            raise ValueError(RunRepository.CHECKPOINTS_DELETED)

    def abandon_resume(self, run: Run, request_id: str | None = ABSENT) -> None:
        """잠금/트랜잭션을 소유한 수명 서비스 전용. 실행 결과나 효과 영수증은 변경하지 않는다."""
        self.require_resume(run)
        if run.status not in Run.RESUMABLE_STATES:
            raise ValueError(RunRepository.NOT_ABANDONABLE)
        ResumeAbandonment.for_request(now(), request_id).store(run.metadata)
        self.save(run)

    def checkpoint_plan(self, session, run, store, *, attached=RUNTIME_DETACHED):
        """Run 전체가 정리 단위다. 중첩 실행/승인 레코드를 개별 삭제하지 않는다."""
        from llm.services.infrastructure.storage import tree_snapshot, revision_token

        source = ResourceRef(
            ResourceKind.RUN, run.id, project_id=session.project_id, session_id=session.id, run_id=run.id
        )
        abandoned = self.resume_abandoned(run)
        deleted = CheckpointRepository().deletion(run)
        names = CheckpointDeclarations.reference_names(run.metadata)
        # 손상된 계보/참조는 삭제 허가로 바뀌지 않는다.
        runs = self.list(session)
        resolve = self._checkpoint_reader(session, runs)
        self._resume_references(runs, resolve)
        messages = store.list()
        common = []

        def block(code, message):
            common.append(Diagnostic(code, message, source=source))

        if attached or session.active_run_ids or session.status == SessionStatus.RUNNING:
            block(PlanBlockerCode.RUNTIME_ATTACHED.value, RunRepository.MAINTENANCE_RUNTIME)
        if run.status in Run.ACTIVE_STATES:
            block(PlanBlockerCode.ACTIVE_RUN, RunRepository.MAINTENANCE_ACTIVE)
        if any(m.status in (MessageStatus.QUEUED, MessageStatus.STREAMING) for m in messages):
            block(PlanBlockerCode.PENDING_MESSAGE.value, RunRepository.MAINTENANCE_REQUESTS)
        descendants = {run.id}
        changed = RunRepository.LINEAGE_EXPANDED
        while changed:
            changed = RunRepository.LINEAGE_STABLE
            for other in runs:
                if other.id not in descendants and ResumeRecord.source_id(other.metadata) in descendants:
                    descendants.add(other.id)
                    changed = RunRepository.LINEAGE_EXPANDED
        for other in runs:
            if other.id == run.id or other.id not in descendants:
                continue
            retired = CheckpointRepository().deletion(other) is not RunRepository.ABSENT
            if (
                not retired
                and not CheckpointDeclarations.read(other.metadata).names
                or other.status not in (RunStatus.COMPLETED, RunStatus.CANCELLED)
                and not self.resume_abandoned(other)
            ):
                block(
                    PlanBlockerCode.RESUME_REFERENCE.value,
                    RunRepository.LINEAGE_REQUIRED.format(identifier=other.id),
                )
        if any(
            ResumeRecord.source_id(m.metadata) == run.id
            and m.status != MessageStatus.CANCELLED
            and not RequestAdmission.run_ids(m)
            for m in messages
        ):
            block(PlanBlockerCode.RESUME_RESERVED.value, RunRepository.MAINTENANCE_RESERVED)
        if deleted is not RunRepository.ABSENT or not names:
            block(PlanBlockerCode.NO_CHECKPOINTS.value, RunRepository.NO_CHECKPOINTS)
        # 읽기 자체가 누락/승인 손상을 검증한다. 삭제 후 승인 원본은 감사용으로만 보존한다.
        payloads = [resolve(run, name) for name in names]
        with self._interaction_scope(session, run, resolve):
            self.interaction_requests(session, run)
            responses = self.interaction_responses(session, run)
        files = tree_snapshot(CheckpointRepository().root_path(run))
        interaction_files = InteractionRepository().snapshot(run)
        abandon = list(common)
        if abandoned or run.status not in Run.RESUMABLE_STATES:
            abandon.append(
                Diagnostic(PlanBlockerCode.NOT_RESUMABLE, RunRepository.ABANDON_REQUIRED, source=source)
            )
        delete = list(common)
        if not abandoned and run.status not in (RunStatus.COMPLETED, RunStatus.CANCELLED):
            delete.append(
                Diagnostic(
                    PlanBlockerCode.RESUME_REQUIRED, RunRepository.DELETE_REQUIRES_ABANDON, source=source
                )
            )
        revision = revision_token(
            RunCheckpointPlanEvidence(
                record(run),
                record(session),
                [record(r) for r in runs],
                [record(m) for m in messages],
                payloads,
                files,
                interaction_files,
                [r.to_dict() for r in responses],
                attached,
            ).to_dict()
        )
        return CheckpointPlan(
            source, list(names), SnapshotFile.total_bytes(files), abandoned, abandon, delete, revision
        )

    def delete_checkpoints(self, session, run, store, *, expected_revision):
        with self._interaction_scope(session, run):
            plan = self.checkpoint_plan(session, run, store)
            require_checkpoint_plan(plan, expected_revision, action=CheckpointAction.DELETE)
            requests = self.interaction_requests(session, run)
            CheckpointRepository().delete(
                run, names=plan.checkpoints, interactions=requests, revision=plan.revision
            )
        self.save(run)

    def resume_references(self, session: Session) -> list[tuple[Run, set[str], str]]:
        runs = self.list(session)
        return self._resume_references(runs, self._checkpoint_reader(session, runs))

    def resumable_message_ids(self, session: Session) -> set[str]:
        return {
            identifier for _, identifiers, _ in self.resume_references(session) for identifier in identifiers
        }

    def interaction_views(self, session, run, store, *, link=ABSENT):
        message, resumed = self.resume_link(session, run, store) if link is RunRepository.ABSENT else link
        with self._interaction_scope(session, run) as (interactions, _):
            requests = self.interaction_requests(session, run)
            return interactions.views(
                run,
                requests,
                self.interaction_responses(session, run),
                resume_message=message,
                resumed_run=resumed,
                resume_abandoned=self.resume_abandoned(run),
            )

    def change_interaction(self, session, run, request, *, operation, expires_at=ABSENT):
        self.require_resume(run)
        current = next(
            (r for r in self.interaction_requests(session, run) if r.id == request.id), RunRepository.ABSENT
        )
        if current is RunRepository.ABSENT or current.fingerprint != request.fingerprint:
            raise ValueError(RunRepository.CHANGED_INTERACTION)
        repository = InteractionRepository()
        if operation == InteractionOperation.CANCEL.value:
            repository.cancel(run, current)
            return current
        if operation == InteractionOperation.RENEW.value:
            return repository.renew(run, current, expires_at=expires_at)
        raise ValueError(RunRepository.UNKNOWN_INTERACTION)

    def checkpoint(self, run: Run, name: str | None = ABSENT) -> dict:
        from llm.core.checkpoints import select_checkpoint

        # EngineContext retains a Run-start snapshot. The storage owner reads
        # the current revision witness, including when called from a handler.
        current = Run.from_record(read_domain_record(run.paths.record), run.paths)
        if (current.id, current.session_id, current.engine) != (run.id, run.session_id, run.engine):
            raise ValueError(RunRepository.CHECKPOINT_OWNER)
        return CheckpointRepository().load(
            current, select_checkpoint(CheckpointDeclarations.read(current.metadata).names, name)
        )

    def resolve_checkpoint(self, session: Session, run: Run, name: str | None = ABSENT) -> dict:
        from llm.core.checkpoints import select_checkpoint

        names = CheckpointDeclarations.read(run.metadata).names
        if name is RunRepository.ABSENT and not names and ResumeRecord.present(run.metadata):
            # An explicit durable reference exists even if its initial copy failed.
            # _resolve_checkpoint still verifies lineage, digest and initialization.
            name = ResumeRecord.require(run.metadata).checkpoint
        name = select_checkpoint(names, name)
        current = self._interaction_reads.get()
        if current is not RunRepository.ABSENT and current.session_root == session.paths.root:
            return deepcopy(current.resolve(run, name))
        return self._resolve_checkpoint(
            run, name, lambda identifier: self.load(session, identifier), self.checkpoint
        )

    def list(self, session: Session, *, query=ABSENT) -> list[Run]:
        if type(query) is Query:
            return [
                self.load(session, identifier)
                for identifier in self.catalog.select(self.record_paths(session), query)
            ]
        return select(
            sorted(
                (self.load(session, path.parent.name) for path in self.record_paths(session)),
                key=lambda run: (run.created_at, run.id),
            ),
            query,
        )

    def unfinished(self, session: Session) -> "list[Run]":
        """Load active records through the shared catalog, not completed history."""
        paths = tuple(self.record_paths(session))
        identifiers = {
            identifier
            for status in Run.ACTIVE_STATES
            for identifier in self.catalog.select(paths, Query(status=status))
        }
        return sorted(
            (self.load(session, identifier) for identifier in identifiers),
            key=lambda run: (run.created_at, run.id),
        )

    def list_results(self, sessions, *, include_running=EXCLUDE_RUNNING, query=ABSENT) -> "list[Run]":
        """결과 순서로 선택한 Run만 로드한다. 원본은 run.json이며 별도 결과 파일은 없다."""
        if query is not RunRepository.ABSENT and type(query) is not Query:
            raise TypeError(RunRepository.INVALID_QUERY)
        if (
            query is not RunRepository.ABSENT
            and query.limit == Query.EMPTY_LIMIT
            and query.after is RunRepository.ABSENT
        ):
            return []

        def visible(run):
            return include_running or run.status not in Run.ACTIVE_STATES

        key = lambda run: (run.ended_at or run.started_at or RunRepository.EMPTY_ORDERING, run.id)
        # 현재 저장소 확장점의 소유권을 유지한다. 사용자 list/load 구현을 파일 경로로 우회하지 않는다.
        if (
            query is RunRepository.ABSENT
            or type(self).list is not RunRepository.list
            or type(self).load is not RunRepository.load
        ):
            return select_ordered(
                (r for session in sessions for r in self.list(session) if visible(r)), query, key=key
            )
        owners = {session.id: session for session in sessions}

        def rows():
            for session in sessions:
                for path in self.record_paths(session):
                    self.paths(session, path.parent.name)
                    row = self.result_catalog.read(path)
                    if row.session_id != session.id:
                        raise ValueError(RunRepository.OWNER_MISMATCH)
                    if visible(row):
                        yield row

        selected = select_ordered(rows(), query, key=key)
        return [self.load(owners[row.session_id], row.id) for row in selected]


class RunRequestError(CodedError, ValueError):
    """An actionable request rejection before queue admission."""

    def __init__(self, code: RunErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)

    @property
    def diagnostic(self) -> Diagnostic:
        return Diagnostic.from_exception(self)


class RunEventType(StrEnum):
    STARTED = "started"
    CANCELLED = RunStatus.CANCELLED.value
    COMPLETED = RunStatus.COMPLETED.value
    FAILED = RunStatus.FAILED.value
    INTERRUPTED = RunStatus.INTERRUPTED.value
    PAUSED = RunStatus.PAUSED.value


@dataclass(frozen=True, slots=True)
class RunEvent:
    type: RunEventType
    run: Run


class _RunPaused(Exception):
    """Engine이 저장된 경계에서 실행을 양보했다. 실패나 자동 재개가 아니다."""


@dataclass(frozen=True, slots=True)
class _RunOutcome:
    """한 실행 시도의 종료 판단. 영속 모델이나 새로운 상태 소유자가 아니다."""

    ABSENT = None
    INVALID_ERROR = "Run outcome error and error_code must be text or None"
    INVALID_SUCCESS = "Completed/paused Run outcome cannot contain a failure"

    status: RunStatus
    error: Optional[str] = ABSENT
    error_code: Optional[str] = ABSENT

    def __post_init__(self) -> None:
        validate_run_transition(RunStatus.RUNNING, self.status, reason=RunTransition.FINISH)
        if any(
            value is not _RunOutcome.ABSENT and not isinstance(value, str)
            for value in (self.error, self.error_code)
        ):
            raise TypeError(_RunOutcome.INVALID_ERROR)
        if self.status in (RunStatus.COMPLETED, RunStatus.PAUSED) and (
            self.error is not _RunOutcome.ABSENT or self.error_code is not _RunOutcome.ABSENT
        ):
            raise ValueError(_RunOutcome.INVALID_SUCCESS)


# 영속 요청 큐를 실행으로 전환하고 이벤트 저장과 복구를 책임진다.
class RunManager:
    """One Session-bound scheduler; separate instances execute separate Sessions."""

    ABSENT = None
    OPEN = False
    CLOSED = True
    DIRECT_INSTRUCTION = False
    RESERVED_INSTRUCTION = True
    TRANSFER_CREATED = True
    TRANSFER_REUSED = False
    CONTROL_ACCEPTED = True
    CONTROL_REJECTED = False
    PREVIEW = True
    EXECUTE_RESUME = False
    CONFIRM_DECISIONS = True
    RESUMABLE = True
    NOT_RESUMABLE = False
    NOT_PAUSED = False
    PAUSED = True
    CHANNEL_OPEN = True
    SINGLE_BATCH = 1
    FIRST_ITEM = 0
    COLLECT_TASK_ERRORS = True
    ALLOW_NONFINITE_DECISIONS = False
    EMPTY_ASSISTANT_TEXT = ""
    ENGINE_BOUNDARY = "engine"
    SCHEDULING_ATTRIBUTE = "_scheduling"
    SESSION_TASK = "llm-session-{identifier}"
    RUN_TASK = "llm-run-{identifier}"
    INVALID_SESSION = "RunManager requires a Session"
    INVALID_RESOLVER = "Capability resolver must implement resolve(project, names)"
    SHUT_DOWN = "RunManager is shut down"
    NO_LIVE_INSTRUCTIONS = "Run does not accept live instructions"
    FOREIGN_RUNTIME = "Run is stopping or owned by another runtime"
    INVALID_TARGETS = "Choose distinct active instruction targets"
    MISSING_TARGET = "Instruction target unavailable: {identifier}"
    EMPTY_INSTRUCTION = "Live instruction must be nonempty text"
    SESSION_MISMATCH = "Session does not match this RunManager"
    SESSION_DELETED = Session.DELETED_ERROR
    PROVENANCE_COLLISION = "Request provenance cannot replace admission evidence"
    ORIGINAL_AGENT_REQUIRED = "Resume requires the original Agent"
    MISSING_ADMISSION = "Run is absent from its original request admission"
    ADMISSION_MISMATCH = "Run Agent binding or request context differs from its admitted input"
    MISSING_ORIGINAL_CONVERSATION = (
        "Original conversation is unavailable; memory-mode resume requires the original backend"
    )
    STOPPED_WORKER = "Session worker has stopped"
    QUEUE_FULL = "Session request queue is full"
    PENDING_CANCELLED_WORK = "Cancelled work is still finishing"
    INVALID_RESUME_SOURCE = "Resume requires a paused/interrupted/failed Run and its original Engine"
    ALREADY_RESUMED = "This Run already has a resume request; resume its latest attempt instead"
    STALE_RESUME_PLAN = "review a fresh resume plan"
    INVALID_LIFECYCLE = "Run lifecycle proof is invalid"
    UNAVAILABLE_ENGINE = "Original Engine configuration is unavailable"
    RESTART_UNPROVEN = "No durable proof of a pre-execution stop"
    LATER_REQUEST = "A later request already uses this Run"
    MISSING_INPUT = "Original input is unavailable"
    ACTIVE_WORK = "Session still has active work"
    INTERACTION_REQUIRED = "Resolve pending interactions before execution"
    RESTART_NOT_ADMITTED = "Restart has not been admitted"
    RESTART_CHANGED = "Restart source changed after admission"
    REQUEST_CANCELLED = "Request was cancelled before execution"
    QUEUED_AGENT_OWNER = "Queued Agent binding belongs to another Project"
    INVALID_EXECUTION_EVIDENCE = "Original Agent execution evidence is missing or inconsistent"
    ALREADY_DISPATCHED = "Queued request already has dispatched Runs"
    PENDING_CHANGED = "Pending Run changed before execution"
    DELETED_CONVERSATION = "Deleted conversation messages cannot be used for resume; submit a new request"
    INELIGIBLE_SOURCE = "Resume source is no longer eligible"
    CHECKPOINT_CHANGED = "Resume checkpoint changed after admission"
    EXPIRED_INTERACTION = "Interaction expired while resume was queued"
    MISSING_HISTORY = "Run requires its dispatched history snapshot"
    MISSING_CAPABILITY = "Capability resolver omitted a required capability"
    INVALID_CAPABILITY_EXPANSION = "additional_capabilities must return a tuple of distinct names"
    CAPABILITY_LIMIT = "Capability expansion limit exceeded"
    MISSING_EXECUTION_EVIDENCE = "Original Agent execution evidence is missing"
    CHANGED_AGENT_CONFIG = "Agent execution configuration changed since the original Run"
    CAPABILITY_SET_MISMATCH = "Capability resolver must return exactly the requested capabilities"
    IMMUTABLE_EXECUTION = "Agent execution binding is immutable"
    DEADLINE = "Run execution deadline exceeded"
    UNREGISTERED_ENGINE = "Requested Engine is not registered"
    UNFINISHED_STEPS = "Engine ended with unfinished or failed Steps"
    INSTRUCTION_NOT_OPENED = "Instruction consumer did not open its channel"
    INSTRUCTION_NOT_CLOSED = "Engine ended without closing instruction targets"
    EVENT_AFTER_PAUSE = "Engine emitted events after pause"
    MISSING_INSTRUCTION_CHANNEL = "Engine has no live instruction channel"
    TARGET_MISMATCH = "Instruction target does not match its execution channel"
    MISSING_COMPLETION = "Completion event requires a result"
    MISSING_USAGE_READER = "Usage limits require CapabilityResolver.model_usage(project)"
    INVALID_COMPONENT_USAGE = "Component usage must be a list of receipts"
    FINALIZATION_OWNER = "Run finalization requires the Session's current Run"
    FAILED_MESSAGE = "Run failed"
    INVALID_STEERING_TARGETS = "targets must be a list of execution target IDs"
    INVALID_ROUTES = "targets must be a list of SteeringRoute values"
    INVALID_DECISIONS = "Resume decisions must be a JSON object"
    INVALID_PARTIAL_OUTPUT = "partial_output must be a boolean"
    STOPPED_REQUEST = "Session runtime stopped before this request finished"
    UNPROVEN_CANCELLATION = "Pending cancellation requires proven unstarted execution"
    UNDRAINED_QUEUE = "Session worker stopped before draining its queue"

    RESPONSE_WRITE_FAILED = "response_write_failed"

    def __init__(
        self,
        sessions: SessionManager,
        engines: EngineRegistry,
        *,
        session: Session,
        steps: Optional[StepManager] = ABSENT,
        repository: Optional[RunRepository] = ABSENT,
        capabilities: Optional[Union[CapabilityResolver, ComponentRegistry]] = ABSENT,
        conversations: Optional[Callable[[Session], Conversation]] = ABSENT,
        context_builder: Optional[ConversationContextBuilder] = ABSENT,
        event_handlers: Optional[EventHandlers] = ABSENT,
        subscriptions: Optional[EventSubscriptions] = ABSENT,
        tool_runtime: Optional[ToolRuntime] = ABSENT,
        policy_resolver=ABSENT,
        provider_calls=ABSENT,
        output_buffer=ABSENT,
        observability=ABSENT,
        provider_diagnostics=ABSENT,
    ) -> None:
        if not isinstance(session, Session):
            raise TypeError(RunManager.INVALID_SESSION)
        self._session = deepcopy(session)
        self.sessions = sessions
        self.engines = engines
        self.agents = RunAgentResolver(engines, sessions.agents.repository)
        self.repository = repository if repository is not RunManager.ABSENT else sessions.run_repository
        if self.repository is RunManager.ABSENT:
            self.repository = RunRepository()
            sessions.run_repository = self.repository
        self.steps = steps if steps is not RunManager.ABSENT else StepManager()
        self.recorder = StepEventRecorder(self.steps)
        self.results = RunResultQuery(sessions, self.repository)
        self.event_handlers = event_handlers if event_handlers is not RunManager.ABSENT else EventHandlers()
        self.subscriptions = subscriptions if subscriptions is not RunManager.ABSENT else EventSubscriptions()
        self._owns_subscriptions = subscriptions is RunManager.ABSENT
        if capabilities is RunManager.ABSENT:
            capabilities = ComponentRegistry()
        self.capabilities = (
            ComponentToolResolver(
                capabilities, data_factory=self._component_data, agents=sessions.agents, engines=engines
            )
            if isinstance(capabilities, ComponentRegistry)
            else capabilities
        )
        if not callable(CapabilityResolverHooks.resolver(self.capabilities)):
            raise TypeError(RunManager.INVALID_RESOLVER)
        self.conversations = (
            conversations if conversations is not RunManager.ABSENT else sessions.conversations
        )
        self.context_builder = (
            context_builder if context_builder is not RunManager.ABSENT else sessions.context_builder
        )
        self._active: Optional[SessionRuntime] = RunManager.ABSENT
        self._closed = RunManager.OPEN
        self._control = RunManager.ABSENT
        self._io = StorageIO(sessions.ownership)
        self._store_instance: Optional[Conversation] = RunManager.ABSENT
        self._request_changed: Optional[asyncio.Event] = RunManager.ABSENT
        self.policy_resolver = (
            policy_resolver if policy_resolver is not RunManager.ABSENT else ProjectPolicyResolver()
        )
        self.tool_runtime = tool_runtime if tool_runtime is not RunManager.ABSENT else ToolRuntime()
        self.provider_calls = provider_calls if provider_calls is not RunManager.ABSENT else ProviderCalls()
        self.provider_diagnostics = provider_diagnostics
        self.output_buffer = output_buffer or OutputBuffer()
        self.pending_work = PendingWork()
        from llm.services.infrastructure.observability import Observability

        self.observability = observability if observability is not RunManager.ABSENT else Observability()
        if self._owns_subscriptions:
            self.subscriptions.observability = self.observability

    def runtime_gauges(self):
        """Observe the owned live runtime without loading records or starting work."""
        runtime = self._active
        values = RuntimeGauges()
        values.unfinished_work = self.pending_work.active
        if runtime is not self.ABSENT:
            values.active_runs = sum(control.execution is not self.ABSENT and not control.execution.done()
                                     for control in runtime.executions.values())
            values.queued_requests = runtime.queue.qsize()
        return values

    def _check_component_access(self):
        if self._closed:
            raise RuntimeError(RunManager.SHUT_DOWN)

    def _component_data(self, project, name):
        """실행용 capability도 기존 저장소·잠금·수명 검사를 공유한다."""
        from llm.services.lifecycle.components import ComponentData

        registry = self.capabilities.components
        component = registry.get(name)
        data_class = Component.data_type(component) or ComponentData
        return (
            data_class(self.sessions.project_access, registry, project, name)
            .bind_resources(self.sessions, self.steps.repository, agents=self.sessions.agents)
            .bind_model_usage(self.sessions, TokenCounters.from_resolver(self.policy_resolver))
            .bind_runtime(
                runner=self._io.run,
                access_check=self._check_component_access,
                history_reader=lambda session_id, run_id, step_id, **options: self.results.tool_result(
                    project, session_id, run_id, step_id, steps=self.steps, **options
                ),
            )
        )

    def _observer_failed(self, run):
        """실행 루프에서 발생한 관찰자 오류에도 백엔드의 로그 설정을 적용한다."""
        from llm.services.infrastructure.logging import logging_scope

        with logging_scope(self.sessions.ownership.logger):
            log_event(run.paths.logs, RuntimeLogEvent.OBSERVER_FAILED, entity_id=run.id)

    def _control_lock(self):
        if self._control is RunManager.ABSENT:
            self._control = asyncio.Lock()
        return self._control

    def _notify_requests(self):
        if self._request_changed is not RunManager.ABSENT:
            self._request_changed.set()
            self._request_changed = RunManager.ABSENT

    async def _publish_instructions(self, run, values):
        event = EngineEvent(
            EngineEventType.STEERING_CHANGED,
            metadata=SteeringNotification(
                instructions=values, targets=SteeringChannel.require(run).targets.values()
            ).to_dict(),
        )
        await self.subscriptions.publish(
            EventChannel.ENGINE.value, run, event, on_error=lambda: self._observer_failed(run)
        )

    def _accept_instruction(self, run_id, content, targets, reserved=DIRECT_INSTRUCTION):
        _, session = self._prepare()
        run = self.repository.load(session, run_id)
        channel = SteeringChannel.read(run)
        if (
            run.status != RunStatus.RUNNING
            or run.id not in session.active_run_ids
            or channel is RunManager.ABSENT
            or not channel.accepting
        ):
            raise RunRequestError(RunErrorCode.STEERING_UNAVAILABLE, RunManager.NO_LIVE_INSTRUCTIONS)
        control = (
            self._active.executions.get(run.id)
            if self._active is not RunManager.ABSENT
            else RunManager.ABSENT
        )
        if (
            self._active is RunManager.ABSENT
            or self._active.closed
            or control is RunManager.ABSENT
            or control.interrupt_requested
        ):
            raise RunRequestError(RunErrorCode.STEERING_UNAVAILABLE, RunManager.FOREIGN_RUNTIME)
        available = channel.targets
        identifiers = (
            () if reserved else targets if targets is not RunManager.ABSENT else (SteeringChannel.ROOT,)
        )
        if not reserved and (not identifiers or len(set(identifiers)) != len(identifiers)):
            raise ValueError(RunManager.INVALID_TARGETS)
        selected = reservation_receipts(run, targets) if reserved else []
        for identifier in identifiers:
            target = available.get(identifier)
            if target is RunManager.ABSENT or not target.accepting or target.mode != SteeringMode.CONSUME:
                raise RunRequestError(
                    RunErrorCode.STEERING_UNAVAILABLE, RunManager.MISSING_TARGET.format(identifier=identifier)
                )
            selected.append(InstructionReceipt.active(target))
        store = self._store(session)
        message = store.create(
            MessageRole.USER,
            content,
            MessageStatus.QUEUED,
            run_id=run.id,
            metadata=InstructionMessageRecord.create(run.id, channel.input_message_id, selected).metadata(),
        )
        return run, RunInstruction.from_message(message)

    async def _send_instruction(self, run_id, content, targets, *, reserved=DIRECT_INSTRUCTION):
        if not isinstance(content, str) or not content.strip():
            raise ValueError(RunManager.EMPTY_INSTRUCTION)

        async def accept():
            async with self._control_lock():
                if self._closed:
                    raise RuntimeError(RunManager.SHUT_DOWN)
                return await self._io.run(self._accept_instruction, run_id, content, targets, reserved)

        run, value = await drain_on_cancel(accept())
        await self._publish_instructions(run, [value])
        return value

    def _steering_boundary(self, run, store, event, tool_usage):
        return steering_boundary(self.repository, run, store, event, tool_usage=tool_usage)

    def _open_resumed_steering(self, run: Run, checkpoint: dict) -> None:
        """재개 원본 검증 뒤에만 접수한다. 검증 실패도 기존 Run 종료 경계에서 처리한다."""
        SteeringChannel.initial(
            run,
            steering_mode(self.engines.get(run.engine)),
            CheckpointConversation.from_checkpoint(checkpoint).input_message_id,
        ).store(run)
        self.repository.save(run)

    async def _publish_run(self, run: Run) -> None:
        self._notify_requests()
        event = RunEvent(
            RunEventType(run.status.value if run.status != RunStatus.RUNNING else RunEventType.STARTED.value),
            deepcopy(run),
        )
        await self.subscriptions.publish(
            EventChannel.RUN.value, event, on_error=lambda: self._observer_failed(run)
        )

    def _store(self, session: Session) -> Conversation:
        if (session.project_id, session.id) != (self._session.project_id, self._session.id):
            raise ValueError(RunManager.SESSION_MISMATCH)
        if self._store_instance is RunManager.ABSENT:
            self._store_instance = self.conversations(session)
        return self._store_instance

    def _prepare(self):
        project = self.sessions._owner(self._session)
        current = self.sessions.load(project, self._session.id)
        if current.status == SessionStatus.DELETED:
            raise ValueError(RunManager.SESSION_DELETED)
        return project, current

    def _validate_request(self, recipients, request_context=UNSET):
        project, session = self._prepare()
        try:
            selected = RequestAdmission.select(recipients, session.agent_ids)
        except (TypeError, ValueError) as error:
            raise RunRequestError(RunErrorCode.AGENT_REQUIRED, str(error)) from error
        bindings = {}
        for identifier in selected:
            binding = self.agents.selected(project, identifier)
            self.agents.strategy(binding, request_context=request_context)
            bindings[identifier] = binding
        self._validate_project_request(project)
        return bindings

    def _admit(self, runtime, content, bindings, request_context=UNSET, *, metadata=ABSENT):
        self._history(runtime.project, runtime.session)
        message_id = new_id()
        admission = RequestAdmission.create(
            project_id=runtime.project.id,
            session_id=runtime.session.id,
            message_id=message_id,
            bindings={identifier: binding.to_dict() for identifier, binding in bindings.items()},
        )
        values = admission.metadata()
        if request_context is not UNSET:
            AgentBinding.store_request_context(values, request_context)
        if metadata is not RunManager.ABSENT:
            if values.keys() & metadata.keys():
                raise ValueError(RunManager.PROVENANCE_COLLISION)
            values.update(deepcopy(metadata))
        return self._store(runtime.session).create(
            MessageRole.USER, content, MessageStatus.QUEUED, message_id=message_id, metadata=values
        )

    def _history(self, project, session):
        """Require complete history both before ACK and before frozen dispatch."""
        messages, records = self._store(session).list(), self.repository.list(session)
        RequestAdmission.history(
            messages,
            records,
            project_id=project.id,
            session_id=session.id,
            persistent=project.conversation_storage == ConversationStorage.FILE,
        )
        self.sessions.transfers.validate_session(project, session, messages)
        return messages, records

    def _validate_project_request(self, project):
        try:
            self.policy_resolver.resolve(project.config.policies)
        except (ValueError, TypeError, ExecutionLimitError) as error:
            raise RunRequestError(RunErrorCode.POLICY_UNAVAILABLE, str(error)) from error
        validate = CapabilityResolverHooks.validator(self.capabilities)
        if callable(validate):
            try:
                validate(project)
            except ValueError as error:
                raise RunRequestError(RunErrorCode.COMPONENT_NOT_REGISTERED, str(error)) from error

    def _source_agent(self, project, session, run, agent_id=ABSENT, *, records=ABSENT):
        binding = self.agents.source(project, run)
        if agent_id is not RunManager.ABSENT and binding.agent_id != agent_id:
            raise ValueError(RunManager.ORIGINAL_AGENT_REQUIRED)
        try:
            original = self._store(session).get(run.input_message_id)
        except KeyError as error:
            raise ValueError(RunManager.MISSING_ORIGINAL_CONVERSATION) from error
        selected = RequestAdmission.runs(
            original,
            self.repository.list(session) if records is RunManager.ABSENT else records,
            project_id=project.id,
            session_id=session.id,
        )
        if not any(candidate.id == run.id for candidate in selected):
            raise ValueError(RunManager.MISSING_ADMISSION)
        admission = RequestAdmission.read(original, project_id=project.id, session_id=session.id)
        if AgentBinding.metadata(
            binding, AgentBinding.request_context(run.metadata)
        ) != AgentBinding.metadata(
            admission.binding(run.agent_id), AgentBinding.request_context(original.metadata)
        ):
            raise ValueError(RunManager.ADMISSION_MISMATCH)
        return binding

    def _source_strategy(self, project, session, run, agent_id=ABSENT, *, records=ABSENT):
        binding = self._source_agent(project, session, run, agent_id, records=records)
        return self.agents.strategy(binding, request_context=AgentBinding.request_context(run.metadata))

    async def _runtime(self) -> SessionRuntime:
        # Caller holds _control. OS ownership is retained before recovery; the
        # worker and all pending storage finish before that ownership is released.
        if self._closed:
            raise RuntimeError(RunManager.SHUT_DOWN)
        project, current = await self._io.run(self._prepare)
        if self._active is not RunManager.ABSENT:
            runtime = self._active
            if runtime.worker is not RunManager.ABSENT and runtime.worker.done():
                runtime.worker.result()
                raise RuntimeError(RunManager.STOPPED_WORKER)
            return runtime

        def recover():
            # Revalidate under the same ownership scope as attachment/recovery.
            owner, fresh = self._prepare()
            # Missing admission evidence is corruption, not a request that can be
            # converted into a Run with a guessed identity. Check before recovery
            # changes any stale Run, conversation record or ownership attachment.
            self._history(owner, fresh)
            self.sessions.attach_runtime(fresh)
            try:
                recovered = self._recover(fresh)
                queued = [message.id for message in self._store(fresh).list() if is_queued_request(message)]
                log_event(fresh.paths.logs, RuntimeLogEvent.STARTED, entity_id=fresh.id, count=len(queued))
                return owner, fresh, queued, recovered
            except BaseException:
                self.sessions.detach_runtime(fresh)
                raise

        project, current, queued, recovered = await self._io.run(recover)
        for run in recovered:
            await self._publish_run(run)
        runtime = SessionRuntime(deepcopy(project), current)
        for message_id in queued:
            runtime.queue.put_nowait(message_id)
        self._active = runtime
        runtime.worker = asyncio.create_task(
            self._worker(runtime), name=RunManager.SESSION_TASK.format(identifier=current.id)
        )
        return runtime

    async def _start(self) -> SessionRuntime:
        async with self._control_lock():
            return await self._runtime()

    async def _submit(self, content: str, recipients, request_context) -> Message:
        async with self._control_lock():
            if self._closed:
                raise RuntimeError(RunManager.SHUT_DOWN)
            await self._io.run(self._validate_request, recipients, request_context)
            runtime = await self._runtime()

            def persist():
                bindings = self._validate_request(recipients, request_context)
                self._check_queue(runtime.session)
                message = self._admit(runtime, content, bindings, request_context)
                log_event(
                    runtime.session.paths.logs,
                    RuntimeLogEvent.QUEUED,
                    entity_id=message.id,
                    related_id=runtime.session.id,
                )
                return message

            message = await self._io.run_records(persist)
            # Accepted submission is shielded through this insertion, including
            # caller cancellation and concurrent shutdown. The selected store is
            # updated first (fsync for the default file store).
            runtime.queue.put_nowait(message.id)
            return message

    async def submit_transfer(self, admission):
        async def apply():
            async with self._control_lock():
                existing = await self._io.run(admission.existing)
                if existing is not RunManager.ABSENT:
                    return existing
                runtime = await self._runtime()

                def persist():
                    existing = admission.existing()
                    if existing is not RunManager.ABSENT:
                        return existing, RunManager.TRANSFER_REUSED
                    bindings = self._validate_request(admission.recipients, UNSET)
                    self._check_queue(runtime.session)
                    message = self._admit(runtime, admission.content, bindings)
                    return admission.persist(message), RunManager.TRANSFER_CREATED

                message, created = await self._io.run(persist)
                if created:
                    runtime.queue.put_nowait(message.id)
                return message

        return await drain_on_cancel(apply())

    def _check_queue(self, session):
        project = self.sessions._owner(session)
        maximum = RunPolicy.from_policies(project.config.policies).max_queued
        if maximum is not RunManager.ABSENT:
            waiting = {
                message.id
                for message in self._store(session).list(query=Query(status=MessageStatus.QUEUED))
                if is_queued_request(message)
            }
            waiting.update(
                run.input_message_id
                for run in self.repository.unfinished(session)
                if run.status == RunStatus.PENDING
            )
            if len(waiting) >= maximum:
                raise RunRequestError(RunErrorCode.QUEUE_FULL, RunManager.QUEUE_FULL)

    def _cancel_queued(self, message_id):
        project, session = self._prepare()
        store = self._store(session)
        message = store.get(message_id)
        RequestAdmission.runs(
            message, self.repository.list(session), project_id=project.id, session_id=session.id
        )
        if message.status != MessageStatus.QUEUED or message.run_id is not RunManager.ABSENT:
            return RunManager.CONTROL_REJECTED
        # 새 백엔드의 복구 이전에도 이미 Run으로 청구된 요청은 취소하지 않는다.
        if self._active is RunManager.ABSENT and any(
            run.input_message_id == message_id for run in self.repository.list(session)
        ):
            return RunManager.CONTROL_REJECTED
        store.set_status(message_id, MessageStatus.CANCELLED)
        return RunManager.CONTROL_ACCEPTED

    async def _cancel_request(self, message_id):
        async with self._control_lock():
            if self._closed:
                raise RuntimeError(RunManager.SHUT_DOWN)
            cancelled = await self._io.run(self._cancel_queued, message_id)
            if cancelled:
                self._notify_requests()
            return cancelled

    def _resume_request(
        self,
        runtime,
        run_id,
        agent_id,
        retry_nodes,
        decisions,
        *,
        preview=EXECUTE_RESUME,
        partial_output=ResumeRecord.DEFAULT_PARTIAL_OUTPUT,
        source=ABSENT,
        expected_revision=ABSENT,
        link=ABSENT,
        records=ABSENT,
    ):
        """소유권 잠금 안에서 중복 재개를 거부하고 요청을 먼저 영속 큐에 저장한다."""
        if self.pending_work.active:
            raise RunRequestError(RunErrorCode.RESUME_REJECTED, RunManager.PENDING_CANCELLED_WORK)
        self._check_queue(runtime.session)
        if expected_revision is not RunManager.ABSENT:
            if expected_revision != self._resume_plan(run_id, agent_id).revision:
                raise CheckpointError(CheckpointErrorCode.CONFLICT, RunManager.STALE_RESUME_PLAN)
        source = source or self.repository.load(runtime.session, run_id)
        self.repository.require_resume(source)
        project = self.sessions.require_project(runtime.project)
        binding = self._source_agent(project, runtime.session, source, agent_id, records=records)
        self._validate_project_request(project)
        transport = self.sessions.transfers.continuation(project, runtime.session, source)
        if source.agent_id != agent_id or source.status not in (
            RunStatus.PAUSED,
            RunStatus.INTERRUPTED,
            RunStatus.FAILED,
        ):
            raise ValueError(RunManager.INVALID_RESUME_SOURCE)
        strategy = self.agents.strategy(
            binding, request_context=AgentBinding.request_context(source.metadata)
        )
        name = EngineHooks.checkpoint_name(strategy)
        if not name or not callable(EngineHooks.resume_validator(strategy)):
            raise ValueError(EngineHooks.UNSUPPORTED_RESUME)
        checkpoint = self.repository.resolve_checkpoint(runtime.session, source, name)
        ToolExecutionScope.checkpoint_usage(CheckpointRecords.from_checkpoint(checkpoint).records)
        decisions, receipts, retry_nodes = self.repository.interaction_decisions(
            runtime.session,
            source,
            checkpoint,
            decisions,
            retry_nodes=retry_nodes,
            confirm=RunManager.CONFIRM_DECISIONS,
        )
        strategy.validate_resume(
            checkpoint, retry_nodes=retry_nodes, **EngineHooks.decision_arguments(decisions)
        )
        # 파일 대화는 대기 요청도 재시작 뒤 유지한다. 메모리 큐가 없어져도 이미 시작한
        # 재개 Run의 연결은 남으므로 같은 원본에서 부작용을 두 번 이어가지 않는다.
        store = self._store(runtime.session)
        if any(
            self.repository.resume_link(runtime.session, source, store) if link is RunManager.ABSENT else link
        ):
            raise ValueError(RunManager.ALREADY_RESUMED)
        descriptor = ResumeRecord.create(
            source.id,
            name,
            checkpoint_digest(checkpoint),
            retry_nodes,
            decisions,
            partial_output=partial_output,
        )
        project = self.sessions.require_project(runtime.project)
        candidate = deepcopy(source)
        descriptor.store(candidate.metadata)
        RunPolicySnapshot.store(candidate.metadata, project.config.policies)
        strategy.validate_resume(
            checkpoint,
            retry_nodes=retry_nodes,
            context=self._context(runtime, candidate, project=project, engine=strategy),
            **EngineHooks.decision_arguments(decisions),
        )
        if preview:
            return descriptor
        for response in receipts:
            self.repository.respond(runtime.session, source, response)
        return self._admit(
            runtime,
            store.get(source.input_message_id).content,
            {agent_id: binding},
            AgentBinding.request_context(source.metadata),
            metadata={**descriptor.metadata(), **transport},
        )

    def _resume_plan(self, run_id, agent_id) -> ResumePlan:
        project, session = self._prepare()
        try:
            source = self.repository.load(session, run_id)
        except (ValueError, KeyError) as error:
            raise CheckpointError(CheckpointErrorCode.CORRUPT, RunManager.INVALID_LIFECYCLE) from error
        ref = ResourceRef(
            ResourceKind.RUN, run_id, project_id=project.id, session_id=session.id, run_id=run_id
        )
        reused, retries, blockers, views, can_resume = [], [], [], [], RunManager.NOT_RESUMABLE
        state, checkpoint, strategy = ResumeState.UNKNOWN, RunManager.ABSENT, RunManager.ABSENT
        store = self._store(session)
        messages, runs = store.list(), self.repository.list(session)
        link = self.repository.resume_link(session, source, store, messages=messages, records=runs)
        try:
            strategy = self._source_strategy(project, session, source, agent_id, records=runs)
            if CheckpointRepository().deletion(source) is not RunManager.ABSENT:
                state = ResumeState.DELETED
                raise ValueError(RunRepository.CHECKPOINTS_DELETED)
            if not EngineHooks.checkpoint_name(strategy) or not callable(
                EngineHooks.resume_validator(strategy)
            ):
                state = ResumeState.UNSUPPORTED
                raise ValueError(EngineHooks.UNSUPPORTED_RESUME)
            state = ResumeState.CORRUPT
            with self.repository._interaction_scope(session, source):
                checkpoint = self.repository.resolve_checkpoint(session, source, strategy.checkpoint_name)
                ToolExecutionScope.checkpoint_usage(CheckpointRecords.from_checkpoint(checkpoint).records)
                reused = [
                    k
                    for k, v in CheckpointRecords.from_checkpoint(checkpoint).records.items()
                    if CheckpointStep.status_from_record(v) == CheckpointStatus.COMPLETED.value
                ]
                state = ResumeState.VALID
                self.repository.require_resume(source)
                state = ResumeState.MISSING
                self._validate_checkpoint_input(store, checkpoint, source.input_message_id, messages=messages)
                state = ResumeState.VALID
                views = self.repository.interaction_views(session, source, store, link=link)
                retries = [
                    v.request.checkpoint_binding.required_key
                    for v in views
                    if v.request.checkpoint_binding.target == InteractionRepository.RETRY_NODES_TARGET
                ]
                if any(
                    v.status
                    in (
                        InteractionView.PENDING,
                        InteractionView.DENIED,
                        InteractionView.EXPIRED,
                        InteractionView.CANCELLED,
                        InteractionView.SUBMITTED,
                        InteractionView.UNAVAILABLE,
                    )
                    for v in views
                ):
                    blockers.append(
                        Diagnostic(
                            PlanBlockerCode.INTERACTION_REQUIRED, RunManager.INTERACTION_REQUIRED, source=ref
                        )
                    )
                else:
                    state = ResumeState.INCOMPATIBLE
                    self._resume_request(
                        SessionRuntime(project, session),
                        run_id,
                        agent_id,
                        (),
                        {},
                        preview=RunManager.PREVIEW,
                        source=source,
                        link=link,
                        records=runs,
                    )
                    state, can_resume = ResumeState.VALID, RunManager.RESUMABLE
        except ToolUsageError as error:
            state = ResumeState.CORRUPT
            blockers.append(Diagnostic.from_exception(error, code=error.code, source=ref))
        except FileNotFoundError as error:
            state = (
                ResumeState.MISSING
                if CheckpointDeclarations.read(source.metadata).names or ResumeRecord.present(source.metadata)
                else (
                    ResumeState.NOT_CREATED
                    if RunExecutionEvidence.never_started(source.metadata)
                    else ResumeState.UNKNOWN
                )
            )
            blockers.append(
                Diagnostic.from_exception(error, code=ResumePlan.checkpoint_code(state), source=ref)
            )
        except (ValueError, KeyError, RuntimeError, AttributeError) as error:
            blockers.append(Diagnostic.from_exception(error, code=RunErrorCode.RESUME_REJECTED, source=ref))
        restart_blockers = []
        if strategy is RunManager.ABSENT:
            restart_blockers.append(
                Diagnostic(PlanBlockerCode.ENGINE_UNAVAILABLE, RunManager.UNAVAILABLE_ENGINE, source=ref)
            )
        # Only a service-persisted pre-dispatch state proves an invocation never
        # started. Missing Steps/checkpoints, including custom Engines, prove nothing.
        if (
            not RunExecutionEvidence.never_started(source.metadata)
            or ResumeRecord.from_metadata(source.metadata)
            or CheckpointDeclarations.read(source.metadata).names
            or CheckpointDeletion.read(source.metadata)
            or self.repository.resume_abandoned(source)
            or source.agent_id != agent_id
            or source.status not in (RunStatus.FAILED, RunStatus.INTERRUPTED)
            or self.steps.list(source)
        ):
            restart_blockers.append(
                Diagnostic(PlanBlockerCode.RESTART_UNPROVEN, RunManager.RESTART_UNPROVEN, source=ref)
            )
        if any(
            RestartRecord.source_id(r.metadata) == run_id or ResumeRecord.source_id(r.metadata) == run_id
            for r in runs
        ) or any(
            any(owner.source_id(m.metadata) == run_id for owner in (ResumeRecord, RestartRecord))
            and m.status != MessageStatus.CANCELLED
            for m in messages
        ):
            restart_blockers.append(
                Diagnostic(PlanBlockerCode.RESTART_RESERVED, RunManager.LATER_REQUEST, source=ref)
            )
        original = next((m for m in messages if m.id == source.input_message_id), RunManager.ABSENT)
        if original is RunManager.ABSENT or original.conversation_deleted:
            restart_blockers.append(
                Diagnostic(PlanBlockerCode.INPUT_UNAVAILABLE, RunManager.MISSING_INPUT, source=ref)
            )
        if (
            any(
                self.repository.load(session, identifier).agent_id == source.agent_id
                for identifier in session.active_run_ids
            )
            or self.pending_work.active
        ):
            restart_blockers.append(
                Diagnostic(PlanBlockerCode.ACTIVE_RUN, RunManager.ACTIVE_WORK, source=ref)
            )
        revision = revision_token(
            ResumePlanEvidence(
                record(source),
                checkpoint,
                tuple(v.to_dict() for v in views),
                project.config.to_dict(),
                record(session),
                tuple(record(m) for m in messages),
                tuple(record(r) for r in runs),
            ).to_dict()
        )
        return ResumePlan(
            ref,
            source.engine,
            can_resume,
            views,
            reused,
            retries,
            blockers,
            state=state,
            revision=revision,
            can_restart=not restart_blockers,
            restart_blockers=restart_blockers,
            active=source.status in Run.ACTIVE_STATES,
            abandoned=self.repository.resume_abandoned(source),
        )

    async def _resume(self, run_id, agent_id, retry_nodes, decisions, partial_output, expected_revision):
        async with self._control_lock():
            runtime = await self._runtime()
            try:
                message = await self._io.run(
                    self._resume_request,
                    runtime,
                    run_id,
                    agent_id,
                    retry_nodes,
                    decisions,
                    partial_output=partial_output,
                    expected_revision=expected_revision,
                )
            except (RunRequestError, CheckpointError):
                raise
            except (ValueError, KeyError, FileNotFoundError, TypeError, RuntimeError) as error:
                raise RunRequestError(RunErrorCode.RESUME_REJECTED, str(error)) from error
            runtime.queue.put_nowait(message.id)
            return message

    def _restart_request(self, runtime, run_id, agent_id, expected_revision):
        plan = self._resume_plan(run_id, agent_id)
        if not expected_revision or expected_revision != plan.revision:
            raise CheckpointError(CheckpointErrorCode.CONFLICT, RunManager.STALE_RESUME_PLAN)
        if not plan.can_restart:
            raise CheckpointError(
                CheckpointErrorCode.PROTECTED, RunManager.RESTART_NOT_ADMITTED, blockers=plan.restart_blockers
            )
        source = self.repository.load(runtime.session, run_id)
        project = self.sessions.require_project(runtime.project)
        binding = self._source_agent(project, runtime.session, source, agent_id)
        self._validate_project_request(project)
        self._check_queue(runtime.session)
        store = self._store(runtime.session)
        original = store.get(source.input_message_id)
        transport = self.sessions.transfers.continuation(project, runtime.session, source)
        descriptor = RestartRecord.create(
            run_id, revision_token(RestartEvidence(record(source), record(original)).to_dict())
        )
        return self._admit(
            runtime,
            original.content,
            {agent_id: binding},
            AgentBinding.request_context(source.metadata),
            metadata={**transport, **descriptor.metadata()},
        )

    def _restart_source(self, session, run):
        descriptor = RestartRecord.require(run.metadata)
        source = self.repository.load(session, descriptor.source_run_id)
        original = self._store(session).get(source.input_message_id)
        if (
            not RunExecutionEvidence.never_started(source.metadata)
            or source.engine != run.engine
            or source.agent_id != run.agent_id
            or not AgentBinding.same_request(source.metadata, run.metadata)
            or source.status not in (RunStatus.FAILED, RunStatus.INTERRUPTED)
            or self.steps.list(source)
            or self.repository.resume_abandoned(source)
            or original.conversation_deleted
            or revision_token(RestartEvidence(record(source), record(original)).to_dict())
            != descriptor.digest
        ):
            raise CheckpointError(CheckpointErrorCode.CONFLICT, RunManager.RESTART_CHANGED)

    def _request_runs(self, message_id: str) -> list[Run]:
        project, session = self._prepare()
        message = self._store(session).get(message_id)
        runs = RequestAdmission.runs(
            message, self.repository.list(session), project_id=project.id, session_id=session.id
        )
        if message.status == MessageStatus.CANCELLED:
            raise RunRequestError(RunErrorCode.REQUEST_CANCELLED, RunManager.REQUEST_CANCELLED)
        return runs

    async def _shutdown(self) -> None:
        async with self._control_lock():
            try:
                await self._shutdown_locked()
            finally:
                if self._owns_subscriptions:
                    await self.subscriptions.close()

    async def _shutdown_locked(self) -> None:
        """Stop accepting input, interrupt active work, and preserve the durable queue."""
        self._closed = RunManager.CLOSED
        runtime = self._active
        if runtime is RunManager.ABSENT:
            return
        runtime.closed = RunManager.CLOSED
        self.pending_work.wake()
        for control in runtime.executions.values():
            control.interrupt()
        runtime.changed.set()
        runtime.queue.put_nowait(RunManager.ABSENT)
        try:
            if runtime.worker is not RunManager.ABSENT:
                await runtime.worker
        finally:
            if runtime.worker is RunManager.ABSENT or runtime.worker.done():
                await self._io.run(self._detach, runtime.session)
                self._store_instance = RunManager.ABSENT
                self._active = RunManager.ABSENT

    def _detach(self, session: Session) -> None:
        log_event(session.paths.logs, RuntimeLogEvent.STOPPED, entity_id=session.id)
        # 스토리지 스레드와 이벤트 루프 사이 완료 경쟁에서도 소유권을 정확히 해제한다.
        self.pending_work.when_idle(lambda: self.sessions.detach_runtime(session))

    def _recover(self, session: Session) -> list[Run]:
        from llm.services.history.recovery import recover_session

        return recover_session(self.sessions, self.repository, self.steps, session, self._store(session))

    def _new_run(self, runtime, message, binding: AgentBinding):
        session = runtime.session
        run_id = new_id()
        if binding.project_id != runtime.project.id:
            raise ValueError(RunManager.QUEUED_AGENT_OWNER)
        run = Run(
            run_id,
            session.id,
            message.id,
            new_id(),
            binding.engine,
            self.repository.paths(session, run_id),
            agent_id=binding.agent_id,
        )
        RunPolicySnapshot.store(run.metadata, runtime.project.config.policies)
        run.metadata.update(AgentBinding.metadata(binding, AgentBinding.request_context(message.metadata)))
        # Service dispatch fact, not an effects guarantee after execution.
        # Records without this field remain unknown; never backfill them.
        run.metadata.update(RunExecutionEvidence.admission_metadata())
        from llm.services.runtime.transfer_values import TransferReceipt

        if TransferReceipt.present(message.metadata):
            run.metadata.update(TransferReceipt.require(message.metadata).metadata())
        if RestartRecord.present(message.metadata):
            RestartRecord.require(message.metadata).store(run.metadata)
        if ResumeRecord.present(message.metadata):
            ResumeRecord.require(message.metadata).store(run.metadata)
            source = self.repository.load(session, ResumeRecord.require(run.metadata).source_run_id)
            if AgentBinding.from_run(source) != binding or not AgentBinding.has_execution(source.metadata):
                raise ValueError(RunManager.INVALID_EXECUTION_EVIDENCE)
            # Preserve the already-admitted evidence in the creation transaction,
            # even if later checkpoint copying or capability preparation fails.
            # Existing records are never repaired from another Run or current config.
            AgentBinding.store_execution(run.metadata, AgentBinding.execution(source.metadata))
        strategy = self.engines.get(run.engine) if run.engine in self.engines.names() else RunManager.ABSENT
        if steering_mode(strategy) != SteeringMode.UNSUPPORTED:
            channel = (
                SteeringChannel.suspended(run)
                if ResumeRecord.present(run.metadata)
                else SteeringChannel.initial(run, steering_mode(strategy), message.id)
            )
            channel.store(run)
        self.repository.create(run)
        return run

    def _dispatch_request(self, runtime: SessionRuntime, message: Message) -> list[Run]:
        from llm.services.infrastructure.transactions import watch

        watch(runtime.session)
        runtime.project = self.sessions.require_project(runtime.project)
        session, store = runtime.session, self._store(runtime.session)
        message = store.get(message.id)
        messages, records = self._history(runtime.project, session)
        existing = RequestAdmission.runs(
            message, records, project_id=runtime.project.id, session_id=session.id
        )
        if not is_queued_request(message):
            return []
        if existing:
            raise ValueError(RunManager.ALREADY_DISPATCHED)
        admission = RequestAdmission.read(message, project_id=runtime.project.id, session_id=session.id)
        runs = [
            self._new_run(runtime, message, admission.binding(identifier))
            for identifier in admission.recipients
        ]
        # One transaction owns the whole group and its shared history. No child
        # task or Provider can start before every recipient is durably linked.
        history = self.context_builder.snapshot_for_run(messages, message.id)
        history = tuple(
            replace(item, status=MessageStatus.COMMITTED) if item.id == message.id else item
            for item in history
        )
        dispatched = RequestAdmission.dispatch(
            admission, run_ids={run.agent_id: run.id for run in runs}, history=history
        )
        store.update_metadata(message.id, dispatched.metadata())
        store.set_status(message.id, MessageStatus.COMMITTED)
        for run in runs:
            store.create(
                MessageRole.ASSISTANT,
                RunManager.EMPTY_ASSISTANT_TEXT,
                MessageStatus.QUEUED,
                message_id=run.assistant_message_id,
                run_id=run.id,
            )
        return runs

    def _begin(self, runtime: SessionRuntime, run: Run) -> Run:
        from llm.services.infrastructure.transactions import watch

        watch(runtime.session)
        session, store = runtime.session, self._store(runtime.session)
        current = self.repository.load(session, run.id)
        validate_run_transition(current.status, RunStatus.RUNNING, reason=RunTransition.START)
        if current.agent_id != run.agent_id or current.metadata != run.metadata:
            raise ValueError(RunManager.PENDING_CHANGED)
        store.set_status(run.assistant_message_id, MessageStatus.STREAMING)
        session.active_run_ids = sorted([*session.active_run_ids, run.id])
        session.status = SessionStatus.RUNNING
        self.sessions._save_runtime(session)
        run.status, run.started_at = RunStatus.RUNNING, now()
        self.repository.save(run)
        log_event(
            run.paths.logs,
            ActivityEventType.RUN_STARTED,
            entity_id=run.id,
            related_id=session.id,
            status=run.status,
        )
        activity_event(
            runtime.project, run, ActivityEventType.RUN_STARTED, time=run.started_at, status=run.status
        )
        return run

    def _validate_checkpoint_input(self, store, checkpoint, input_message_id, *, messages=ABSENT):
        identifiers = checkpoint_message_ids(checkpoint) | {input_message_id}
        get = (
            store.get
            if messages is RunManager.ABSENT
            else {message.id: message for message in messages}.__getitem__
        )
        try:
            if any(get(identifier).conversation_deleted for identifier in identifiers):
                raise ValueError(RunManager.DELETED_CONVERSATION)
        except KeyError as error:
            raise ValueError(RunManager.MISSING_ORIGINAL_CONVERSATION) from error

    def _resume_checkpoint(self, session, run):
        descriptor = ResumeRecord.require(run.metadata)
        source = self.repository.load(session, descriptor.source_run_id)
        self.repository.require_resume(source)
        if (
            source.engine != run.engine
            or source.agent_id != run.agent_id
            or not AgentBinding.same_request(source.metadata, run.metadata)
            or source.status not in (RunStatus.PAUSED, RunStatus.INTERRUPTED, RunStatus.FAILED)
        ):
            raise ValueError(RunManager.INELIGIBLE_SOURCE)
        checkpoint = self.repository.resolve_checkpoint(session, source, descriptor.checkpoint)
        if checkpoint_digest(checkpoint) != descriptor.digest:
            raise ValueError(RunManager.CHECKPOINT_CHANGED)
        if any(request.expired for request in self.repository.interaction_requests(session, source)):
            raise ValueError(RunManager.EXPIRED_INTERACTION)
        store = self._store(session)
        self._validate_checkpoint_input(store, checkpoint, source.input_message_id)
        return resume_instructions(store, checkpoint)

    def _partial_outputs(self, session, run):
        """Provide unfinished source observations; only Engines interpret Step metadata."""
        from llm.core.results import ResumeOutput

        descriptor = ResumeRecord.from_metadata(run.metadata)
        if descriptor is RunManager.ABSENT or not descriptor.partial_output:
            return ()
        source = self.repository.load(session, descriptor.source_run_id)
        steps = {step.id: step for step in self.steps.list(source) if step.status != StepStatus.COMPLETED}
        return tuple(
            ResumeOutput(source.id, output, steps.get(output.step_id))
            for output in self.repository.outputs(source)
            if not output.final and (output.step_id is RunManager.ABSENT or output.step_id in steps)
        )

    def _context(
        self, runtime: SessionRuntime, run: Run, policies=ABSENT, *, project=ABSENT, engine=ABSENT
    ) -> EngineContext:
        context_policy, limits = (
            policies
            if policies is not RunManager.ABSENT
            else self.policy_resolver.resolve(RunPolicySnapshot.read(run.metadata))
        )
        checkpoint = RunManager.ABSENT
        store = self._store(runtime.session)
        if RestartRecord.present(run.metadata):
            self._restart_source(runtime.session, run)
        if ResumeRecord.present(run.metadata):
            checkpoint = self._resume_checkpoint(runtime.session, run)
            references = CheckpointConversation.from_checkpoint(checkpoint)
            try:
                history = tuple(store.get(identifier) for identifier in references.message_ids)
            except KeyError as error:
                raise ValueError(RunManager.MISSING_ORIGINAL_CONVERSATION) from error
            history = tuple(
                (
                    replace(item, id=run.input_message_id, run_id=run.id)
                    if item.id == references.input_message_id
                    else item
                )
                for item in history
            )
        else:
            message = store.get(run.input_message_id)
            admission = RequestAdmission.read(
                message, project_id=runtime.project.id, session_id=runtime.session.id
            )
            if admission.dispatched is RunManager.ABSENT:
                raise ValueError(RunManager.MISSING_HISTORY)
            history = self.context_builder.select_snapshot(
                admission.dispatched.history, policy=context_policy
            )
        # Copy domain state, but do not deepcopy Python handler closures or live
        # capabilities. The resolver returns a fresh registry for this Run.
        if engine is RunManager.ABSENT:
            engine = self._source_strategy(
                project if project is not RunManager.ABSENT else runtime.project, runtime.session, run
            )
        names = required_capabilities(engine)
        project = deepcopy(project if project is not RunManager.ABSENT else runtime.project)
        project.config.bind_policy_snapshot(RunPolicySnapshot.read(run.metadata))
        values = {}
        if names:
            values = self.capabilities.resolve(project, names)
            if not isinstance(values, dict) or any(name not in values for name in names):
                raise ValueError(RunManager.MISSING_CAPABILITY)
        request = EngineHooks.discoverer(engine)
        for expansion in count() if request is not RunManager.ABSENT else ():
            extra = request(values)
            if (
                not isinstance(extra, tuple)
                or any(not isinstance(name, str) or not name.strip() for name in extra)
                or len(set(extra)) != len(extra)
            ):
                raise ValueError(RunManager.INVALID_CAPABILITY_EXPANSION)
            missing = tuple(name for name in extra if name not in values)
            if not missing:
                break
            if expansion == limits.max_capability_rounds:
                raise ValueError(RunManager.CAPABILITY_LIMIT)
            resolved = self.capabilities.resolve(project, missing)
            if not isinstance(resolved, dict) or any(name not in resolved for name in missing):
                raise ValueError(RunManager.MISSING_CAPABILITY)
            values.update(resolved)
        admitted_policies = RunPolicySnapshot.read(run.metadata)
        retries = admitted_policies.get(PolicySection.TOOL_RETRY, {})
        tool_scope = ToolExecutionScope(
            self.tool_runtime,
            policy=admitted_policies.get(PolicySection.TOOLS, {}),
            retry=retries,
            operations=ToolOperations(deepcopy(runtime.session), self._io, self.repository, steps=self.steps),
        )
        if checkpoint:
            tool_scope.restore_checkpoint(CheckpointRecords.from_checkpoint(checkpoint).records)
        for name in values.get(ToolRegistry.CAPABILITY, ToolRegistry()).names():
            tool_scope.validate(values[ToolRegistry.CAPABILITY].get(name))
        inbox = (
            SteeringInbox(history=checkpoint_messages(store, checkpoint) if checkpoint else {})
            if SteeringChannel.present(run.metadata)
            else RunManager.ABSENT
        )
        if inbox is not RunManager.ABSENT:
            inbox.engine, inbox.mode = run.engine, steering_mode(engine)
            inbox.channels[inbox.target_id] = inbox
        # One Run-local capability snapshot per name. Selected child inspection
        # and execution share it without repeatedly rebuilding provider resources.
        capability_cache = dict(values)

        def resolve_selected(names, *, expansion=RunManager.ABSENT):
            if (
                expansion is not RunManager.ABSENT
                and limits.max_capability_rounds is not RunManager.ABSENT
                and expansion >= limits.max_capability_rounds
            ):
                raise ValueError(RunManager.CAPABILITY_LIMIT)
            missing = tuple(name for name in names if name not in capability_cache)
            if missing:
                resolved = self.capabilities.resolve(deepcopy(project), missing)
                if not isinstance(resolved, dict) or set(resolved) != set(missing):
                    raise ValueError(RunManager.CAPABILITY_SET_MISMATCH)
                capability_cache.update(resolved)
            return {name: capability_cache[name] for name in names}

        context = EngineContext(
            project,
            deepcopy(runtime.session),
            deepcopy(run),
            history,
            tools=values.get(ToolRegistry.CAPABILITY, ToolRegistry()),
            capabilities=values,
            checkpoint=checkpoint,
            resume_outputs=self._partial_outputs(runtime.session, run),
            tool_scope=tool_scope,
            pending_work=self.pending_work,
            token_counters=dict(TokenCounters.from_resolver(self.policy_resolver)),
            steering=inbox,
            capability_resolver=resolve_selected,
        )
        context = engine.prepare_context(context)
        if ResumeRecord.present(run.metadata):
            source = self.repository.load(runtime.session, ResumeRecord.require(run.metadata).source_run_id)
            if not AgentBinding.has_execution(source.metadata):
                raise ValueError(RunManager.MISSING_EXECUTION_EVIDENCE)
            if AgentBinding.execution(source.metadata) != engine.runtime_binding:
                raise ValueError(RunManager.CHANGED_AGENT_CONFIG)
        return context

    def _bind_execution(self, run, engine):
        if (
            AgentBinding.has_execution(run.metadata)
            and AgentBinding.execution(run.metadata) != engine.runtime_binding
        ):
            raise ValueError(RunManager.IMMUTABLE_EXECUTION)
        AgentBinding.store_execution(run.metadata, engine.runtime_binding)
        self.repository.save(run)

    def _dispatch(self, run):
        RunExecutionEvidence.mark_started(run.metadata)
        self.repository.save(run)

    async def _consume_limited(self, runtime, run):
        # Revalidate transport authority and absolute deadline before execution.
        transport_seconds = await self._io.run(
            self.sessions.transfers.execution, runtime.project, runtime.session, run
        )
        # ACK before resolving capabilities or invoking an Engine implementation.
        await self._io.run_records(self._dispatch, run)
        from llm.providers.runtime import logging_scope as provider_logging_scope

        policies = self.policy_resolver.resolve(RunPolicySnapshot.read(run.metadata))
        _, run_policy = policies
        seconds = run_policy.timeout_seconds
        if transport_seconds is not RunManager.ABSENT:
            seconds = transport_seconds if seconds is RunManager.ABSENT else min(seconds, transport_seconds)
        guard = timeout(seconds)
        try:
            async with guard:
                usage_policy = UsagePolicy.from_run(run)
                counter = TokenCounters.from_resolver(self.policy_resolver).get(usage_policy.counter)
                source = UsageSource(runtime.project.id, runtime.session.id, run.id)
                with (
                    self.observability.scope(),
                    provider_logging_scope(
                        self.sessions.ownership.path.parent,
                        **source.to_dict(),
                        engine=run.engine,
                        diagnostics=self.provider_diagnostics,
                    ),
                    self.provider_calls.scope(),
                    UsageScope(usage_policy, counter, source).scope(),
                ):
                    await self._consume(runtime, run, policies)
        except asyncio.TimeoutError as error:
            expired = guard.expired() if callable(guard.expired) else guard.expired
            if expired:
                raise ExecutionLimitError(RunErrorCode.RUN_TIMEOUT, RunManager.DEADLINE) from error
            raise

    async def _consume(self, runtime: SessionRuntime, run: Run, policies) -> None:
        if run.engine not in self.engines.names():
            raise RunRequestError(RunErrorCode.ENGINE_NOT_REGISTERED, RunManager.UNREGISTERED_ENGINE)
        try:
            engine = await self._io.run(self._source_strategy, runtime.project, runtime.session, run)
        except KeyError as error:
            raise RunRequestError(
                RunErrorCode.ENGINE_NOT_REGISTERED, RunManager.UNREGISTERED_ENGINE
            ) from error
        store = self._store(runtime.session)
        try:
            if ResumeRecord.present(run.metadata):
                checkpoint = await self._io.run(self._resume_checkpoint, runtime.session, run)
                await self._io.run(self.repository.inherit_interactions, runtime.session, run, checkpoint)
                # 실행 설정 검증이 실패해도 재개 시도의 원본 체크포인트는 남긴다.
                await self._io.run(
                    self.repository.record_checkpoint,
                    run,
                    EngineEvent(
                        EngineEventType.CHECKPOINT,
                        metadata=CheckpointWrite.initialize_event(
                            ResumeRecord.require(run.metadata).checkpoint,
                            CheckpointContent.from_checkpoint(checkpoint).header,
                            CheckpointRecords.from_checkpoint(checkpoint).records,
                        ).to_dict(),
                    ),
                )
            context = await self._io.run(self._context, runtime, run, policies, engine=engine)
            await self._io.run_records(self._bind_execution, run, engine)
            if context.checkpoint is not RunManager.ABSENT and context.steering is not RunManager.ABSENT:
                await self._io.run(self._open_resumed_steering, run, context.checkpoint)
        except ExecutionLimitError:
            raise
        except Exception as error:
            raise RunRequestError(RunErrorCode.CAPABILITY_FAILED, str(error)) from error
        events = engine.execute(context)
        paused = RunManager.NOT_PAUSED
        outputs = RunOutputState()
        active_steps = set()

        async def handle(batch):
            nonlocal paused
            pending_outputs, pending_deltas, notifications = [], [], []
            for event in batch:
                if paused:
                    raise ValueError(RunManager.EVENT_AFTER_PAUSE)
                event, change = outputs.accept(
                    event, active_steps, has_result=ExecutionObservations.has_output(run.metadata)
                )
                if event.type == EngineEventType.CHECKPOINT and CheckpointWrite(event.metadata).is_record:
                    # Run 전체 예산은 서비스 스코프가 소유한다. Engine payload를 해석하지 않고
                    # 같은 durable 저장 경계에 스냅샷을 붙여 명시적 재개가 예산을 초기화하지 않게 한다.
                    write = CheckpointWrite(event.metadata)
                    event = replace(
                        event,
                        metadata=write.with_record(
                            context.tool_scope.checkpoint_record(write.record.value)
                        ).to_dict(),
                    )
                if event.checkpoint is not RunManager.ABSENT:
                    receipt = event.checkpoint
                    write = CheckpointWrite(receipt.metadata)
                    event = replace(
                        event,
                        checkpoint=replace(
                            receipt,
                            metadata=write.with_record(
                                context.tool_scope.checkpoint_record(write.record.value)
                            ).to_dict(),
                        ),
                    )
                value = event.delta if event.type == EngineEventType.TEXT_DELTA else event.output
                if event.type == EngineEventType.STEERING:
                    if context.steering is RunManager.ABSENT:
                        raise ValueError(RunManager.MISSING_INSTRUCTION_CHANNEL)
                    directive = SteeringEventData(event.metadata)
                    channel = context.steering.channels[directive.target_id]
                    if directive.operation == SteeringOperation.OPEN:
                        target = directive.target
                        if (
                            target.scope != channel.scope
                            or target.mode != channel.mode
                            or target.engine != channel.engine
                        ):
                            raise ValueError(RunManager.TARGET_MISMATCH)
                    usage = (
                        context.tool_scope.checkpoint()
                        if directive.operation == SteeringOperation.SELECT
                        else RunManager.ABSENT
                    )
                    messages, changed = await self._io.run_records(self._steering_boundary, run, store, event, usage)
                    if directive.operation in (SteeringOperation.ROUTES, SteeringOperation.BIND):
                        channel.acknowledged = directive.request_id
                        if directive.operation == SteeringOperation.ROUTES:
                            notifications.append(
                                EngineEvent(
                                    EngineEventType.STEERING_CHANGED,
                                    metadata=SteeringNotification(
                                        routes=SteeringChannel.require(run).routes.values()
                                    ).to_dict(),
                                )
                            )
                        if changed:
                            notifications.append(
                                EngineEvent(
                                    EngineEventType.STEERING_CHANGED,
                                    metadata=SteeringNotification(instructions=changed).to_dict(),
                                )
                            )
                        continue
                    channel.messages = messages
                    channel.history.update((m.id, m) for m in messages)
                    channel.acknowledged = directive.request_id
                    channel.opened = RunManager.CHANNEL_OPEN
                    channel.closed = (
                        SteeringChannel.require(run).targets[channel.target_id].reason
                        is not RunManager.ABSENT
                    )
                    if channel.closed and channel.target_id != SteeringChannel.ROOT:
                        # UI 이력은 저장된 descriptor가 소유한다. 완료한 자식 전달함은 회수한다.
                        context.steering.channels.pop(channel.target_id)
                    if (
                        directive.operation in (SteeringOperation.OPEN, SteeringOperation.CLOSE)
                        or channel.closed
                    ):
                        notifications.append(
                            EngineEvent(
                                EngineEventType.STEERING_CHANGED,
                                metadata=SteeringNotification(
                                    targets=SteeringChannel.require(run).targets.values()
                                ).to_dict(),
                            )
                        )
                    if changed:
                        notifications.append(
                            EngineEvent(
                                EngineEventType.STEERING_CHANGED,
                                metadata=SteeringNotification(instructions=changed).to_dict(),
                            )
                        )
                    continue
                if event.type == EngineEventType.TEXT_DELTA:
                    pending_outputs.append(value)
                    if change is not RunManager.ABSENT:
                        pending_deltas.append(change)
                elif event.type in (
                    EngineEventType.OUTPUT,
                    EngineEventType.COMPLETION,
                    EngineEventType.CHECKPOINT,
                    EngineEventType.PAUSED,
                    EngineEventType.STEP_STARTED,
                    EngineEventType.STEP_UPDATED,
                    EngineEventType.STEP_COMPLETED,
                    EngineEventType.STEP_FAILED,
                    EngineEventType.STEP_INTERRUPTED,
                    EngineEventType.STEP_CANCELLED,
                ):
                    # 한 이벤트의 쓰기를 한 저장 경계에서 끝낸 뒤 런타임 상태를 진행한다.
                    await self._io.run_records(self._record_engine_event, runtime.project, run, store, event)
                    if event.type == EngineEventType.STEP_STARTED:
                        active_steps.add(event.step_id)
                    elif event.type in (
                        EngineEventType.STEP_COMPLETED,
                        EngineEventType.STEP_FAILED,
                        EngineEventType.STEP_INTERRUPTED,
                        EngineEventType.STEP_CANCELLED,
                    ):
                        active_steps.discard(event.step_id)
                    elif event.type == EngineEventType.PAUSED:
                        paused = RunManager.PAUSED
                else:
                    await self.event_handlers.handle(
                        EventContext(run, context, self._io, self.repository), event
                    )
                # Keep checkpoint observers independent of event composition.
                # Both observations are published only after the atomic commit.
                if event.checkpoint is not RunManager.ABSENT:
                    notifications.append(event.checkpoint)
                notifications.append(event)
            if pending_outputs:
                await drain_on_cancel(
                    self._io.run_records(self._record_deltas, run, store, pending_outputs, pending_deltas)
                )
            for event in notifications:
                await self.subscriptions.publish(
                    EventChannel.ENGINE.value, run, event, on_error=lambda: self._observer_failed(run)
                )

        try:
            output_buffer = self.output_buffer.for_policies(RunPolicySnapshot.read(run.metadata))
            await consume_events(events, output_buffer, handle)

        finally:
            close = getattr(events, OutputQueue.CLOSE_METHOD, RunManager.ABSENT)
            if close is not RunManager.ABSENT:
                await close()
        if paused:
            raise _RunPaused()
        if context.steering is not RunManager.ABSENT:
            if steering_mode(engine) == SteeringMode.CONSUME and not context.steering.opened:
                raise ValueError(RunManager.INSTRUCTION_NOT_OPENED)
            if any(t.accepting for t in SteeringChannel.require(run).targets.values()):
                raise ValueError(RunManager.INSTRUCTION_NOT_CLOSED)
        if any(
            step.status in (StepStatus.PENDING, StepStatus.RUNNING, StepStatus.FAILED)
            for step in await self._io.run(self.steps.list, run)
        ):
            raise RuntimeError(RunManager.UNFINISHED_STEPS)

    def _record_engine_event(
        self, project: Project, run: Run, store: Conversation, event: EngineEvent
    ) -> None:
        """표준 이벤트 하나의 저장 단위. IO 실행기가 잠금·트랜잭션을 제공한다."""
        if event.type == EngineEventType.COMPLETION:
            self._record_completion(run, event)
        elif event.type == EngineEventType.CHECKPOINT:
            self.repository.record_checkpoint(run, event)
        elif event.type == EngineEventType.PAUSED:
            # 일시정지를 알리기 전에 체크포인트가 실제로 존재해야 한다.
            self.repository.checkpoint(run, CheckpointPause.read(event.metadata).checkpoint)
        else:
            if event.output is not RunManager.ABSENT:
                self.repository.record_output(run, event.output)
            if event.type == EngineEventType.OUTPUT and event.output.step_id is RunManager.ABSENT:
                self._record_result(run, store, event.output)
            else:
                if event.type == EngineEventType.OUTPUT:
                    event = replace(event, type=EngineEventType.STEP_UPDATED)
                self.recorder.record(run, event)
                if event.checkpoint is not RunManager.ABSENT:
                    self.repository.record_checkpoint(run, event.checkpoint)
                if event.type == EngineEventType.STEP_FAILED:
                    activity_event(
                        project,
                        run,
                        ActivityEventType.STEP_FAILED,
                        time=now(),
                        status=StepStatus.FAILED.value,
                        step_id=event.step_id,
                        code=event.failure_code or StepErrorCode.FAILED,
                    )

    def _record_deltas(self, run: Run, store: Conversation, outputs, deltas) -> None:
        """출력 저널과 대화 델타를 같은 트랜잭션에 저장한다. 알림은 호출자가 보낸다."""
        if len(outputs) == RunManager.SINGLE_BATCH:
            self.repository.record_output(run, outputs[RunManager.FIRST_ITEM])
        else:
            self.repository.record_outputs(run, outputs)
        if len(deltas) == RunManager.SINGLE_BATCH:
            text, operation = deltas[RunManager.FIRST_ITEM]
            if operation == OutputOperation.APPEND:
                store.delta(run.assistant_message_id, text)
            else:
                store.delta(run.assistant_message_id, text, operation=operation)
        elif deltas:
            store.deltas(run.assistant_message_id, deltas)

    def _record_result(self, run: Run, store: Conversation, value: EngineOutput) -> None:
        from llm.services.infrastructure.transactions import watch

        watch(run)
        # 최종 텍스트만 다음 대화에 전달한다. 구조화 Graph 결과를 문자열로 바꾸지 않는다.
        # 같은 본문은 재저장하지 않아 일반적인 단일 응답의 중복 쓰기를 피한다.
        if (
            value.visibility == OutputVisibility.USER
            and (value.text or value.data is RunManager.ABSENT)
            and store.get(run.assistant_message_id).content != value.text
        ):
            store.delta(run.assistant_message_id, value.text, operation=OutputOperation.REPLACE)
        ExecutionObservations.store_output(run.metadata, value)
        self.repository.save(run)

    def _record_completion(self, run: Run, event: EngineEvent) -> None:
        if event.completion is RunManager.ABSENT:
            raise ValueError(RunManager.MISSING_COMPLETION)
        child(run.paths.state, event.completion.id)
        if not CompletionRecords.contains(run.metadata, event.completion.id):
            policy = UsagePolicy.from_run(run)
            project_entries = []
            auxiliary = []
            project_limited = policy.project.active
            if project_limited or policy.run.active:
                project = self.sessions.project_access.load(self._session.project_id)
                reader = CapabilityResolverHooks.usage_reader(self.capabilities)
                if not callable(reader):
                    raise ExecutionLimitError(UsageErrorCode.CONFIGURATION, RunManager.MISSING_USAGE_READER)
                auxiliary = reader(project)
                if not isinstance(auxiliary, list) or any(not isinstance(entry, dict) for entry in auxiliary):
                    raise ExecutionLimitError(
                        UsageErrorCode.CONFIGURATION, RunManager.INVALID_COMPONENT_USAGE
                    )
            if project_limited:
                project_entries = completion_usage(
                    self.repository, self.sessions.list(project, include_deleted=INCLUDE_DELETED_EVIDENCE)
                )
            run_entries = [
                UsageReceipt.from_result(result) for result in CompletionRecords.results(run.metadata)
            ]
            run_entries.extend(
                UsageReceipt.from_record(e) for e in auxiliary if UsageCall.belongs_to_run(e, run.id)
            )
            check_admission(
                policy,
                event.completion.reserved_tokens,
                [*project_entries, *[UsageReceipt.from_record(e) for e in auxiliary]],
                run_entries,
            )
        CompletionRecords.upsert(run.metadata, event.completion)
        self.repository.save(run)

    def _finish(
        self,
        runtime: SessionRuntime,
        run: Run,
        status: RunStatus,
        error: Optional[str] = ABSENT,
        error_code: Optional[str] = ABSENT,
    ) -> list[RunInstruction]:
        # 변경 전 검증한다. 중복 종료로 시각/오류/Step을 덮거나 다음 Run의 Session을 해제하지 않는다.
        validate_run_transition(run.status, status, reason=RunTransition.FINISH)
        _RunOutcome(status, error, error_code)
        if run.session_id != runtime.session.id or run.id not in runtime.session.active_run_ids:
            raise ValueError(RunManager.FINALIZATION_OWNER)
        from llm.services.infrastructure.transactions import watch

        watch(runtime.session)
        for step in self.steps.list(run):
            if step.status in Step.ACTIVE_STATES:
                if status == RunStatus.FAILED:
                    self.steps.fail(step, error or RunManager.FAILED_MESSAGE)
                else:
                    self.steps.interrupt(step)
        store = self._store(runtime.session)
        changed = []
        if SteeringChannel.present(run.metadata):
            changed = finish_instructions(
                store, run.id, SteeringChannel.require(run).unapplied_reason or error_code or status.value
            )
            close_targets(run, error_code or status.value)
        store.set_status(run.assistant_message_id, MessageStatus(status.value))
        run.status = status
        run.error = error
        run.error_code = error_code
        run.ended_at = now()
        self.repository.save(run)
        log_event(run.paths.logs, ProjectActivityEvent.run_event(status), entity_id=run.id, status=status)
        runtime.session.active_run_ids.remove(run.id)
        runtime.session.status = (
            SessionStatus.RUNNING if runtime.session.active_run_ids else SessionStatus.IDLE
        )
        self.sessions._save_runtime(runtime.session)
        activity_event(
            runtime.project,
            run,
            ProjectActivityEvent.run_event(status),
            time=run.ended_at,
            status=run.status,
            code=run.error_code,
        )
        return changed

    async def _run_one(self, runtime: SessionRuntime, control: RunRuntime) -> None:
        run = control.run
        started = time.monotonic()
        try:
            await self._io.run_records(self._begin, runtime, run)
            self.observability.record(
                ObservationKind.RUNS.value,
                ObservationStatus.STARTED.value,
                run_id=run.id,
                project_id=runtime.project.id,
                session_id=runtime.session.id,
            )
            await self._publish_run(run)
            control.execution = asyncio.create_task(self._consume_limited(runtime, run))
            if runtime.closed or control.interrupt_requested:
                control.execution.cancel()
            outcome = _RunOutcome(RunStatus.COMPLETED)
            try:
                await control.execution
            except _RunPaused:
                outcome = _RunOutcome(RunStatus.PAUSED)
            except asyncio.CancelledError:
                outcome = _RunOutcome(RunStatus.INTERRUPTED, error_code=RunErrorCode.INTERRUPTED)
            except Exception as failure:
                outcome = _RunOutcome(
                    RunStatus.FAILED, str(failure), stable_error_code(failure) or RunErrorCode.ENGINE_FAILED
                )
                issue = Diagnostic.from_exception(
                    failure,
                    code=outcome.error_code,
                    boundary=RunManager.ENGINE_BOUNDARY,
                    source=ResourceRef(
                        ResourceKind.RUN,
                        run.id,
                        project_id=runtime.project.id,
                        session_id=runtime.session.id,
                        run_id=run.id,
                    ),
                )
                if issue.code != outcome.error_code:
                    issue = replace(
                        issue,
                        code=outcome.error_code,
                        details=RunObservedFailure(issue.code).details(issue.details),
                    )
                ExecutionObservations.store_diagnostic(run.metadata, issue)
            finally:
                changed = await self._io.run(
                    self._finish, runtime, run, outcome.status, outcome.error, outcome.error_code
                )
                self.observability.record(
                    ObservationKind.RUNS.value,
                    outcome.status.value,
                    code=outcome.error_code,
                    duration_seconds=time.monotonic() - started,
                    run_id=run.id,
                    project_id=runtime.project.id,
                    session_id=runtime.session.id,
                )
                if outcome.status == RunStatus.PAUSED:
                    try:
                        await self._io.run(
                            self.repository.apply_interaction_policy,
                            runtime.session,
                            run,
                            RunPolicySnapshot.read(run.metadata).get(PolicySection.APPROVAL, {}),
                        )
                    except Exception:
                        log_event(
                            run.paths.logs,
                            RuntimeLogEvent.INTERACTION_POLICY_FAILED,
                            entity_id=run.id,
                            status=RunManager.RESPONSE_WRITE_FAILED,
                        )
                await self._publish_run(run)
                if SteeringChannel.present(run.metadata):
                    await self._publish_instructions(run, changed)
        finally:
            control.execution = RunManager.ABSENT
            control.finished.set()
            runtime.changed.set()

    def _scheduling_lock(self):
        if not hasattr(self, RunManager.SCHEDULING_ATTRIBUTE):
            self._scheduling = asyncio.Lock()
        return self._scheduling

    async def _pump(self, runtime: SessionRuntime) -> None:
        async with self._scheduling_lock():
            await self._pump_scheduled(runtime)

    async def _pump_scheduled(self, runtime: SessionRuntime) -> None:
        for identifier, control in tuple(runtime.executions.items()):
            if control.task.done():
                control.task.result()
                del runtime.executions[identifier]
        occupied = {control.run.agent_id for control in runtime.executions.values()}
        for message_id in tuple(runtime.waiting):
            runs = runtime.groups.get(message_id)
            if runs is not RunManager.ABSENT and all(run.status not in Run.ACTIVE_STATES for run in runs):
                # Finalizers, including publication, must finish before the group
                # releases its queue accounting or the next request's history.
                if any(run.id in runtime.executions for run in runs):
                    continue
                runtime.waiting.remove(message_id)
                del runtime.groups[message_id]
                runtime.queue.task_done()
                continue
            if runs is RunManager.ABSENT:
                message = await self._io.run(self._store(runtime.session).get, message_id)
                admission = RequestAdmission.read(
                    message, project_id=runtime.project.id, session_id=runtime.session.id
                )
                if message.status == MessageStatus.CANCELLED:
                    runtime.waiting.remove(message_id)
                    runtime.queue.task_done()
                    continue
                recipients = set(admission.recipients)
                if not runtime.closed and not self.pending_work.active and recipients - occupied:
                    runs = await self._io.run_records(self._dispatch_request, runtime, message)
                    runtime.groups[message_id] = runs
                else:
                    occupied.update(recipients)
                    continue
            for run in runs:
                if run.status != RunStatus.PENDING:
                    continue
                if not runtime.closed and not self.pending_work.active and run.agent_id not in occupied:
                    control = RunRuntime(run)
                    # Register before any preparation await or observer can request
                    # cancellation; the finalizer owns even a pre-consumer cancel.
                    runtime.executions[run.id] = control
                    control.task = asyncio.create_task(
                        self._run_one(runtime, control), name=RunManager.RUN_TASK.format(identifier=run.id)
                    )
                # Older pending recipients reserve FIFO position even when another
                # recipient in this group can already execute in parallel.
                occupied.add(run.agent_id)

    async def _worker(self, runtime: SessionRuntime) -> None:
        failure = RunManager.ABSENT
        try:
            while not runtime.closed:
                runtime.changed.clear()
                await self._pump(runtime)
                if runtime.closed:
                    break
                reader = asyncio.create_task(runtime.queue.get())
                changed = asyncio.create_task(runtime.changed.wait())
                quarantine = (
                    asyncio.create_task(self.pending_work.wait())
                    if self.pending_work.active
                    else RunManager.ABSENT
                )
                waiters = [reader, changed]
                if quarantine is not RunManager.ABSENT:
                    waiters.append(quarantine)
                try:
                    await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                    if reader.done():
                        identifier = reader.result()
                        if identifier is RunManager.ABSENT:
                            runtime.queue.task_done()
                        else:
                            runtime.waiting.append(identifier)
                        while not runtime.queue.empty():
                            identifier = runtime.queue.get_nowait()
                            if identifier is RunManager.ABSENT:
                                runtime.queue.task_done()
                            else:
                                runtime.waiting.append(identifier)
                finally:
                    for waiter in waiters:
                        waiter.cancel()
                    await asyncio.gather(*waiters, return_exceptions=RunManager.COLLECT_TASK_ERRORS)
        except BaseException as error:
            failure = error
            raise
        finally:
            controls = tuple(runtime.executions.values())
            for control in controls:
                control.interrupt()
            results = await asyncio.gather(
                *(control.task for control in controls), return_exceptions=RunManager.COLLECT_TASK_ERRORS
            )
            if failure is RunManager.ABSENT:
                for result in results:
                    if isinstance(result, BaseException):
                        raise result

    # 공개 API
    @property
    def session(self) -> Session:
        return deepcopy(self._session)

    async def start(self) -> None:
        """Session을 복구하고 선택한 대화 저장소에 남아 있는 대기 요청만 예약한다."""
        await drain_on_cancel(self._start())

    async def submit(self, content: str, *, recipients, request_context=UNSET) -> Message:
        """Persist the selected Project Agent and the separate request context."""
        if request_context is not UNSET:
            from llm.core.json_data import JsonObject

            if not isinstance(request_context, dict):
                raise TypeError(EngineContextBinding.INVALID_CONTEXT)
            JsonObject.validate(request_context)
            request_context = deepcopy(request_context)
        return await drain_on_cancel(self._submit(content, recipients, request_context))

    async def steer(self, run_id: str, content: str, *, targets=ABSENT) -> RunInstruction:
        """현재 Run에 추가 지시를 저장한다. 일반 요청 큐나 새 Run을 만들지 않는다."""
        if targets is not RunManager.ABSENT:
            if not isinstance(targets, (list, tuple)) or any(
                not isinstance(v, str) or not v for v in targets
            ):
                raise ValueError(RunManager.INVALID_STEERING_TARGETS)
            targets = tuple(targets)
        return await self._send_instruction(run_id, content, targets)

    async def reserve_instruction(self, run_id: str, content: str, *, targets) -> RunInstruction:
        """경로별 다음 Agent 실행 한 번에 예약한다. 실행 중 대상에는 소급 적용하지 않는다."""
        if not isinstance(targets, (list, tuple)) or any(not isinstance(v, SteeringRoute) for v in targets):
            raise ValueError(RunManager.INVALID_ROUTES)
        return await self._send_instruction(
            run_id, content, tuple(targets), reserved=RunManager.RESERVED_INSTRUCTION
        )

    async def resume_plan(self, run_id: str, *, agent_id: str) -> ResumePlan:
        """큐 등록이나 모델 호출 없이 재사용·승인·재개 차단 사유를 조회한다."""
        return await self._io.run(self._resume_plan, run_id, agent_id)

    async def restart(self, run_id: str, *, agent_id: str, expected_revision: str) -> Message:
        """Only a durably proven pre-dispatch stop can become a new request."""

        async def apply():
            async with self._control_lock():
                runtime = await self._runtime()
                message = await self._io.run(
                    self._restart_request, runtime, run_id, agent_id, expected_revision
                )
                runtime.queue.put_nowait(message.id)
                return message

        return await drain_on_cancel(apply())

    async def resume(
        self,
        run_id: str,
        *,
        agent_id: str,
        retry_nodes=(),
        decisions=ABSENT,
        partial_output=ResumeRecord.DEFAULT_PARTIAL_OUTPUT,
        expected_revision=ABSENT,
    ) -> Message:
        """체크포인트에서 새 Run을 예약한다. 불확실한 노드의 키는 명시적으로 승인한다."""
        if decisions is not RunManager.ABSENT and not isinstance(decisions, dict):
            raise TypeError(RunManager.INVALID_DECISIONS)
        if type(partial_output) is not bool:
            raise TypeError(RunManager.INVALID_PARTIAL_OUTPUT)
        decisions = json.loads(
            json.dumps(
                decisions if decisions is not RunManager.ABSENT else {},
                allow_nan=RunManager.ALLOW_NONFINITE_DECISIONS,
            )
        )
        return await drain_on_cancel(
            self._resume(run_id, agent_id, tuple(retry_nodes), decisions, partial_output, expected_revision)
        )

    async def cancel_request(self, message_id: str) -> bool:
        """대기 요청만 취소한다. 실행 중이면 False이며 interrupt로 명시적으로 중단한다."""
        return await drain_on_cancel(self._cancel_request(message_id))

    async def wait_request(self, message_id: str, *, timeout: Optional[float] = ABSENT) -> list[Run]:
        """Wait for one stored request; timeout/caller cancellation never stops it.

        Completed history is read without attaching a runtime. Pending requests
        start recovery/scheduling. Shutdown before this request finishes raises
        RuntimeError; queued messages can be waited on again while their store
        survives (file queues are durable; owned memory ends at backend shutdown).
        """

        async def wait():
            runs = await self._io.run(self._request_runs, message_id)
            if runs and all(run.status not in Run.ACTIVE_STATES for run in runs):
                return runs
            runtime = await drain_on_cancel(self._start())
            while True:
                if self._request_changed is RunManager.ABSENT:
                    self._request_changed = asyncio.Event()
                changed = self._request_changed
                runs = await self._io.run(self._request_runs, message_id)
                if runs and all(run.status not in Run.ACTIVE_STATES for run in runs):
                    return runs
                if runtime.worker.done():
                    runtime.worker.result()
                    raise RuntimeError(RunManager.STOPPED_REQUEST)
                notification = asyncio.create_task(changed.wait())
                try:
                    await asyncio.wait((notification, runtime.worker), return_when=asyncio.FIRST_COMPLETED)
                finally:
                    notification.cancel()
                    await asyncio.gather(notification, return_exceptions=RunManager.COLLECT_TASK_ERRORS)

        return await asyncio.wait_for(wait(), timeout=timeout)

    async def interrupt(self, run_id=ABSENT) -> bool:
        runtime = self._active
        if runtime is RunManager.ABSENT:
            return RunManager.CONTROL_REJECTED
        controls = [
            control
            for identifier, control in runtime.executions.items()
            if (run_id is RunManager.ABSENT or identifier == run_id) and not control.finished.is_set()
        ]
        if not controls:
            return RunManager.CONTROL_REJECTED
        for control in controls:
            control.interrupt()
        await asyncio.gather(*(control.finished.wait() for control in controls))
        await self._io.run(
            log_event, runtime.session.paths.logs, RuntimeLogEvent.INTERRUPTED, entity_id=runtime.session.id
        )
        return RunManager.CONTROL_ACCEPTED

    async def cancel_run(self, run_id):
        """Cancel a dispatched recipient without starting it or affecting siblings."""

        async def apply():
            async with self._scheduling_lock():
                runtime = self._active
                if runtime is not RunManager.ABSENT and run_id in runtime.executions:
                    control = runtime.executions[run_id]
                    if control.finished.is_set():
                        return RunManager.CONTROL_REJECTED
                else:
                    control = RunManager.ABSENT

                    def persist():
                        project, session = self._prepare()
                        run = self.repository.load(session, run_id)
                        self._history(project, session)
                        if run.status != RunStatus.PENDING:
                            return RunManager.CONTROL_REJECTED
                        if (
                            not RunExecutionEvidence.never_started(run.metadata)
                            or self.steps.list(run)
                            or run.id in session.active_run_ids
                        ):
                            raise ValueError(RunManager.UNPROVEN_CANCELLATION)
                        validate_run_transition(
                            run.status, RunStatus.CANCELLED, reason=RunTransition.PENDING_CANCEL
                        )
                        run.status, run.ended_at, run.error_code = (
                            RunStatus.CANCELLED,
                            now(),
                            RunErrorCode.REQUEST_CANCELLED,
                        )
                        if SteeringChannel.present(run.metadata):
                            close_targets(run, RunErrorCode.REQUEST_CANCELLED)
                        self._store(session).set_status(run.assistant_message_id, MessageStatus.CANCELLED)
                        self.repository.save(run)
                        return run

                    changed = await self._io.run(persist)
                    if changed and runtime is not RunManager.ABSENT:
                        for group in runtime.groups.values():
                            for index, current in enumerate(group):
                                if current.id == run_id:
                                    group[index] = changed
                        runtime.changed.set()
                    if changed:
                        self._notify_requests()
                        await self._publish_run(changed)
                    return bool(changed)
            control.interrupt()
            await control.finished.wait()
            return RunManager.CONTROL_ACCEPTED

        return await drain_on_cancel(apply())

    async def wait_idle(self) -> None:
        runtime = await drain_on_cancel(self._start())
        assert runtime.worker is not RunManager.ABSENT
        joined = asyncio.create_task(runtime.queue.join())
        try:
            done, _ = await asyncio.wait((joined, runtime.worker), return_when=asyncio.FIRST_COMPLETED)
            if runtime.worker in done:
                runtime.worker.result()
                if not joined.done():
                    raise RuntimeError(RunManager.UNDRAINED_QUEUE)
            await joined
        finally:
            joined.cancel()
            await asyncio.gather(joined, return_exceptions=RunManager.COLLECT_TASK_ERRORS)

    async def shutdown(self) -> None:
        await drain_on_cancel(self._shutdown())
