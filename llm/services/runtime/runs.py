"""Session의 단일 Dispatcher로 같은 Agent는 FIFO, 다른 Agent는 병렬 실행한다. 입력을 영속 접수하고 Run별 이벤트 저장·취소·복구를 소유한다. 중단된 Run은 자동 재실행하지 않는다."""

from llm.services.infrastructure.storage import read_domain_record, atomic_domain_json

from llm.services.query import FileCatalog, Query, select, select_ordered
from llm.services.infrastructure.journal import OutputJournal
from llm.providers.calls import ProviderCalls
from llm.services.runtime.output import OutputBuffer, OutputProjection, RunOutputState, consume_events
from typing import Optional, Union
from types import SimpleNamespace
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
    Message, MessageRole, MessageStatus, Project, Run, RunStatus, StepStatus,
    Session, SessionStatus, new_id, now, validate_run_transition,
)
from llm.core.paths import RunPaths
from llm.core.configuration import UNSET
from llm.core.agents import AgentBinding
from llm.services.runtime.agents import RunAgentResolver
from llm.services.runtime.requests import RequestAdmission
from llm.core.interactions import InteractionActor, InteractionRequest, InteractionResponse, InteractionView
from llm.engines.base import EngineContext, EngineEvent, EngineEventType, SteeringInbox, required_capabilities, steering_mode
from llm.core.steering import InstructionStatus, RunInstruction, SteeringMode, SteeringRoute, is_instruction, is_queued_request
from llm.services.runtime.steering import (finish_instructions, checkpoint_messages, steering_boundary,
    close_targets, initial_channel, reservation_receipts, resume_instructions)
from llm.engines.registry import EngineRegistry
from llm.components.tools import ToolRegistry
from llm.services.history.conversation import Conversation
from llm.services.history.context import ConversationContextBuilder
from llm.components.registry import ComponentRegistry
from llm.components.tools.resolver import CapabilityResolver, ComponentToolResolver
from llm.services.lifecycle.steps import StepEventRecorder, StepManager
from llm.services.infrastructure.storage import (
    StorageIO, child, drain_on_cancel, record, revision_token,
)
from llm.services.lifecycle.sessions import SessionManager, SessionRuntime, RunRuntime
from llm.services.infrastructure.logging import log_event
from llm.services.infrastructure.activity import activity_event
from llm.services.results import RunResultQuery
from llm.core.contracts import Diagnostic, ResourceRef
from llm.errors import CodedError, stable_error_code
from llm.core.plans import ResumePlan, ResumeState
from llm.core.plans import CheckpointPlan
from llm.core.checkpoints import ResumeAbandonment, require_checkpoint_plan, CheckpointError, CheckpointStatus
from llm.core.results import CompletionResult, EngineOutput, EngineDelta
from enum import StrEnum
from asyncio import timeout
from llm.policies import ExecutionLimitError
from llm.services.runtime.policies import ProjectPolicyResolver
from llm.services.runtime.tools import ToolRuntime, ToolExecutionScope, ToolUsageError
from llm.services.runtime.operations import OperationRepository, ToolOperations
from llm.services.runtime.events import EventHandlers, EventContext, EventSubscriptions
from llm.services.runtime.checkpoints import CheckpointRepository, checkpoint_digest, checkpoint_message_ids
from llm.services.runtime.interactions import InteractionRepository
from llm.services.runtime.pending import PendingWork
from llm.services.runtime.usage import UsageScope, check_admission, has_limit
from llm.services.infrastructure.observability import ObservationKind, ObservationStatus


# 실행 메타데이터를 저장한다. Facade와 실행 서비스가 같은 인스턴스를 공유한다.
class RunRepository:
    """Run metadata and paths beneath the owning Session's runs root."""

    def __init__(self, *, output_index_stride: int = 128, catalog_size: int = FileCatalog.DEFAULT_MAX_ENTRIES):
        self._interaction_reads = ContextVar("run_interaction_reads", default=None)
        self.output_journal = OutputJournal(index_stride=output_index_stride)
        self.catalog = FileCatalog(catalog_size)
        self.result_catalog = FileCatalog(catalog_size, projection=lambda r: SimpleNamespace(
            id=r["id"], session_id=r["session_id"], status=RunStatus(r["status"]),
            started_at=r["started_at"], ended_at=r["ended_at"]))
        self.usage_catalog = FileCatalog(catalog_size, projection=self._usage_record)

    def _usage_record(self, data):
        run = Run.from_record(data, None)
        return run.session_id, [{key: entry[key] for key in (
            "started_at", "usage", "usage_complete", "reserved_tokens")}
            for entry in self.completion_records(run.metadata)]

    @staticmethod
    def completion_records(metadata):
        """A missing receipt container is not evidence of zero model usage."""
        if not isinstance(metadata, dict) or not isinstance(metadata.get("completions"), list):
            raise ValueError("Run requires a stored completions container")
        identifiers = set()
        for entry in metadata["completions"]:
            result = CompletionResult.from_record(entry)
            if result.id in identifiers:
                raise ValueError("Duplicate Run completion record")
            identifiers.add(result.id)
        return metadata["completions"]

    @contextmanager
    def _interaction_scope(self, session, run, resolve=None):
        """공개 저장소 확장점을 그대로 호출하면서 같은 잠금 작업의 읽기만 공유한다."""
        key = (session.paths.root, run.id)
        current = self._interaction_reads.get()
        if current is not None and current[0] == key:
            yield current[1:]
            return
        resolve = resolve or self._checkpoint_reader(session, [run])
        with InteractionRepository().read_scope() as interactions:
            token = self._interaction_reads.set((key, interactions, resolve))
            try:
                yield interactions, resolve
            finally:
                self._interaction_reads.reset(token)

    def _checkpoint_reader(self, session, runs):
        """잠금 작업 한 번의 Run 목록/체크포인트만 재사용한다. 영속 캐시는 만들지 않는다."""
        current = self._interaction_reads.get()
        if current is not None and current[0][0] == session.paths.root:
            return current[2]
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
        if deleted is not None:
            return [InteractionRequest.from_dict(raw) for raw in deleted["interactions"]]
        names = run.metadata.get("checkpoints", []) or ([run.metadata["resume"]["checkpoint"]] if "resume" in run.metadata else [])
        requests = []
        for name in names:
            values = interactions.requests({**resolve(run, name), "name": name}, run)
            values = interactions.effective(run, values)
            if any(v.binding.get("checkpoint") != name for v in values):
                raise ValueError("Interaction checkpoint binding mismatch")
            requests.extend(values)
        if len({v.id for v in requests}) != len(requests):
            raise ValueError("Duplicate interaction identity")
        return requests

    def _resume_references(self, records, resolve) -> list[tuple[Run, set[str], str]]:
        """미완료 체인별 (마지막 Run, 보호 메시지 ID, 체크포인트 지문)을 반환한다.

        호출자는 workspace 잠금을 소유해야 한다. 체크포인트가 손상되면
        보호할 참조를 추측하지 않고 거부한다. 초기 복사 전 실패도 resolve_checkpoint로 읽는다.
        """
        from graphlib import TopologicalSorter
        runs = {run.id: run for run in records}
        parents = {run.id: run.metadata["resume"]["run_id"] for run in runs.values() if "resume" in run.metadata}
        if any(parent not in runs or runs[parent].engine != runs[identifier].engine
               for identifier, parent in parents.items()):
            raise ValueError("Invalid resume lineage")
        # 순환된 이력에서 모든 Run을 '후속 실행 있음'으로 오인하여 보호를 풀지 않는다.
        tuple(TopologicalSorter({identifier: (parents[identifier],) if identifier in parents else ()
                                for identifier in runs}).static_order())
        superseded = set(parents.values())
        references = []
        for run in runs.values():
            if run.id in superseded or run.status not in (RunStatus.PAUSED, RunStatus.FAILED, RunStatus.INTERRUPTED):
                continue
            if self.resume_abandoned(run):
                continue
            names = run.metadata.get("checkpoints", ()) or (
                [run.metadata["resume"]["checkpoint"]] if "resume" in run.metadata else ())
            protected, checkpoints = set(), []
            for name in names:
                checkpoint = resolve(run, name)
                protected.add(run.input_message_id)
                protected.update(checkpoint_message_ids(checkpoint))
                checkpoints.append([name, checkpoint])
            if protected:
                references.append((run, protected, checkpoint_digest({"checkpoints": checkpoints})))
        return references

    def _resolve_checkpoint(self, run, name, load_run, load_checkpoint) -> dict:
        """초기 복사 전에 끝난 재개 시도만 검증된 원본을 따라 복원한다."""
        selected, visited = run, set()
        chain = []
        while True:
            if selected.id in visited:
                raise ValueError("Cyclic resume lineage")
            visited.add(selected.id)
            try:
                checkpoint = load_checkpoint(selected, name)
                break
            except FileNotFoundError:
                descriptor = selected.metadata.get("resume")
                if not descriptor or name in selected.metadata.get("checkpoints", ()):
                    raise
                if descriptor["checkpoint"] != name:
                    raise ValueError("Resume checkpoint name mismatch")
                parent = load_run(descriptor["run_id"])
                if parent.engine != run.engine or parent.status not in (
                        RunStatus.PAUSED, RunStatus.FAILED, RunStatus.INTERRUPTED):
                    raise ValueError("Invalid resume checkpoint parent")
                chain.append((selected, descriptor))
                selected = parent
        for attempt, descriptor in reversed(chain):
            if checkpoint_digest(checkpoint) != descriptor["digest"]:
                raise ValueError("Resume checkpoint changed before initialization")
            checkpoint = {**checkpoint, "run_id": attempt.id, "engine": attempt.engine}
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
                raise ValueError("Run ownership mismatch")
            entries.extend(deepcopy(values))
        return entries

    def record_paths(self, session):
        """Discover owned Run directories, including records missing from damaged trees."""
        from llm.services.infrastructure.storage import reject_links
        from llm.services.history.conversation import ConversationStore
        paths = {}
        for directory in session.paths.runs.glob("*"):
            reject_links(directory)
            if directory.is_dir() and not directory.name.startswith("."):
                paths[directory.name] = self.paths(session, directory.name).root / "run.json"
        required = set(session.active_run_ids)
        # File conversations retain independent witnesses even if an entire Run tree is lost.
        # Memory conversations need not have this file; a missing optional file is not created.
        reject_links(session.paths.conversation)
        if session.paths.conversation.exists():
            required.update(run_id for message in ConversationStore(session.paths.conversation).list()
                            for run_id in RequestAdmission.run_ids(message))
        if not required <= paths.keys():
            raise ValueError("Missing referenced Run record")
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
            raise ValueError("Only completed Run history can be pruned")
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
            if child(session.paths.runs, data["id"]) != path.parent or data["session_id"] != session.id:
                raise ValueError("Backup Run ownership mismatch")
            run = Run.from_record(data, RunPaths(path.parent))
            self.completion_records(run.metadata)
            if run.status in (RunStatus.PENDING, RunStatus.RUNNING):
                raise ValueError("Backup contains an unfinished Run")
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
        if run.paths.root.exists():
            raise FileExistsError("Run storage already exists")
        run.metadata.setdefault("completions", [])
        self._write(run, creating=True)

    def save(self, run: Run) -> None:
        path = run.paths.root / "run.json"
        if not path.exists():
            raise ValueError("Existing Run directory requires its stored record")
        prior = Run.from_record(read_domain_record(path), run.paths)
        if prior.id != run.id or prior.session_id != run.session_id:
            raise ValueError("Run ownership mismatch")
        if (prior.agent_id != run.agent_id or prior.engine != run.engine
                or RunAgentResolver.metadata(prior.metadata["agent_binding"], prior.metadata.get("request_context", UNSET))
                != RunAgentResolver.metadata(run.metadata["agent_binding"], run.metadata.get("request_context", UNSET))
                or ("agent_execution" in prior.metadata
                    and prior.metadata["agent_execution"] != run.metadata.get("agent_execution"))):
            raise ValueError("Run Agent evidence is immutable")
        if prior.status != run.status:
            validate_run_transition(prior.status, run.status)
        previous = {entry["id"]: entry for entry in self.completion_records(prior.metadata)}
        current = {entry["id"]: entry for entry in self.completion_records(run.metadata)}
        if not previous.keys() <= current.keys():
            raise ValueError("Run update cannot remove stored completion receipts")
        for identifier, entry in previous.items():
            CompletionResult.validate_update(entry, current[identifier])
        self._write(run, creating=False)

    def _write(self, run: Run, *, creating: bool) -> None:
        path = run.paths.root / "run.json"
        self.completion_records(run.metadata)
        if run.status not in (RunStatus.PENDING, RunStatus.RUNNING) and "completions" in run.metadata:
            run.metadata["completions"] = [asdict(CompletionResult.for_run(data, run))
                                           for data in run.metadata["completions"]]
        data = record(run)
        Run.from_record(data, run.paths)
        CheckpointRepository().names(run)
        if not creating:
            self.output_journal.validate(run.paths.state / "outputs.jsonl")
        atomic_domain_json(path, data)
        if creating:
            self.output_journal.initialize(run.paths.state / "outputs.jsonl")
        log_event(run.paths.logs, "run.saved", entity_id=run.id, status=run.status)

    def load(self, session: Session, run_id: str) -> Run:
        paths = self.paths(session, run_id)
        data = read_domain_record(paths.root / "run.json")
        if data["id"] != run_id or data["session_id"] != session.id:
            raise ValueError("Run ownership mismatch")
        log_event(paths.logs, "run.loaded", entity_id=run_id)
        run = Run.from_record(data, paths)
        self.completion_records(run.metadata)
        self.resume_abandoned(run)
        CheckpointRepository().deletion(run)
        CheckpointRepository().names(run)
        self.output_journal.validate(run.paths.state / "outputs.jsonl")
        return run

    def record_output(self, run: Run, value: Union[EngineDelta, EngineOutput]) -> None:
        self.output_journal.append(run.paths.state / "outputs.jsonl", [value])

    def record_outputs(self, run: Run, values) -> None:
        """묶음 쓰기 확장점. 단일 쓰기를 재정의한 사용자 저장소의 계약도 유지한다."""
        if type(self).record_output is not RunRepository.record_output:
            for value in values:
                self.record_output(run, value)
        else:
            self.output_journal.append(run.paths.state / "outputs.jsonl", values)

    def output_events(self, run: Run, *, after: int = 0, limit: Optional[int] = None):
        return self.output_journal.read(run.paths.state / "outputs.jsonl", after=after, limit=limit)

    def reconcile_output(self, run, store):
        """저널이 Conversation보다 앞선 부분 저장을 복구한다. 실행 효과나 Run 성공 상태는 재현하지 않는다."""
        values = self.outputs(run)
        if not values:
            return False
        visible = [v for v in values if v.visibility == "user"]
        final = next((v for v in visible if v.final and v.step_id is None and (v.text or v.data is None)), None)
        text = final.text if final is not None else "".join(v.text for v in visible)
        message = store.get(run.assistant_message_id)
        if message.run_id != run.id or message.role != MessageRole.ASSISTANT:
            raise ValueError("Output reconciliation ownership mismatch")
        if message.content == text:
            return False
        store.reconcile(message.id, text, run_id=run.id)
        return True

    def outputs(self, run: Run) -> list[EngineOutput]:
        """부분/확정 출력을 ID별로 투영한다. Run/Step 상태는 별도로 조회한다."""
        projection = OutputProjection()
        # 사용자 저장소/저널의 기존 읽기 확장점을 우회하지 않는다.
        if (type(self).output_events is RunRepository.output_events
                and type(self.output_journal).read is OutputJournal.read):
            with closing(self.output_journal.iter_events(run.paths.state / "outputs.jsonl")) as events:
                for value in events:
                    projection.add(value)
        else:
            for value in self.output_events(run):
                projection.add(value)
        return list(projection.snapshot().values())

    def record_checkpoint(self, run: Run, event: EngineEvent) -> None:
        interactions = InteractionRepository()
        name = event.metadata["name"]
        initialize = event.metadata["operation"] == "initialize"
        records = (event.metadata.get("records", {}) if initialize else
                   {event.metadata["key"]: event.metadata["value"]})
        requests = interactions.requests({"name": name, "records": records}, run)
        previous = []
        if requests and not initialize:
            previous = interactions.requests({**self.checkpoint(run, name), "name": name}, run)
        known = {request.id for request in previous}
        for request in requests:
            interactions.publish(run, request,
                                 existing=request.id in known or initialize and "resume" in run.metadata)
        CheckpointRepository().record(run, event)
        # File, manifest and Run revision commit before the Engine receives ACK.
        # Only a failed initial copy may resolve the verified source checkpoint.
        self.save(run)

    def inherit_interactions(self, session, run, checkpoint):
        """Preserve control evidence even if the later checkpoint copy fails."""
        source = self.load(session, run.metadata["resume"]["run_id"])
        InteractionRepository().inherit(source, run,
            {**checkpoint, "name": run.metadata["resume"]["checkpoint"]})

    def instructions(self, session, run, store):
        """본문은 선택한 ConversationStore에서 읽는다. 복제 저장소를 만들지 않는다."""
        inherited = set()
        if "steering" in run.metadata:
            for name in run.metadata.get("checkpoints", ()):
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

    def interaction_decisions(self, session: Session, run: Run, checkpoint: dict, explicit: dict, *, retry_nodes=(), confirm=False):
        """조회와 실행이 같은 응답 저장소를 사용한다. 주입된 저장소도 이 경로를 공유한다."""
        self.require_resume(run)
        with self._interaction_scope(session, run) as (interactions, _):
            requests = self.interaction_requests(session, run)
            if any(interactions.envelope(run, r)["cancelled"] for r in requests):
                raise ValueError("Interaction is cancelled; renew before resuming")
            return interactions.decisions(requests, self.interaction_responses(session, run), explicit,
                                          retry_nodes=retry_nodes, confirm=confirm)

    def apply_interaction_policy(self, session, run, policy):
        """정책 응답만 저장한다. 실행 재개와 호스트 허용 여부 검사는 별도다."""
        if not policy.get("enabled", False):
            return []
        with self._interaction_scope(session, run) as (interactions, _):
            requests = self.interaction_requests(session, run)
            answered = {r.request_id for r in self.interaction_responses(session, run)}
            saved = []
            for request in requests:
                if (request.id in answered or request.expired is True or request.risk is None
                        or request.risk_scheme != policy.get("risk_scheme")
                        or request.category == InteractionRepository.RETRY_CATEGORY
                        or interactions.envelope(run, request)["cancelled"] is True):
                    continue
                for rule in policy.get("rules", []):
                    if rule["category"] == request.category and request.risk <= rule["max_risk"]:
                        option = request.find_approval()
                        if option is not None:
                            response = replace(request.respond(option.id), actor=InteractionActor.POLICY, policy_id=rule["id"])
                            saved.append(self.respond(session, run, response))
                        break
        return saved

    def resume_link(self, session, run, store, *, messages=None, records=None):
        message = next((m for m in (store.list() if messages is None else messages) if m.metadata.get("resume", {}).get("run_id") == run.id
                        and m.status != MessageStatus.CANCELLED), None)
        resumed = next((r for r in (self.list(session) if records is None else records) if r.metadata.get("resume", {}).get("run_id") == run.id), None)
        return message, resumed

    def resume_abandoned(self, run: Run) -> bool:
        """서비스가 기록한 재개 포기만 해석한다. 손상된 기록은 보호 해제로 추측하지 않는다."""
        if "resume_abandonment" not in run.metadata:
            return False
        ResumeAbandonment.from_dict(run.metadata["resume_abandonment"])
        if run.status not in (RunStatus.PAUSED, RunStatus.FAILED, RunStatus.INTERRUPTED):
            raise ValueError("Invalid resume abandonment record")
        return True

    def require_resume(self, run: Run) -> None:
        if self.resume_abandoned(run):
            raise ValueError("Resume was abandoned")
        if CheckpointRepository().deletion(run) is not None:
            raise ValueError("Checkpoints were explicitly deleted")

    def abandon_resume(self, run: Run, request_id: str | None = None) -> None:
        """잠금/트랜잭션을 소유한 수명 서비스 전용. 실행 결과나 효과 영수증은 변경하지 않는다."""
        self.require_resume(run)
        if run.status not in (RunStatus.PAUSED, RunStatus.FAILED, RunStatus.INTERRUPTED):
            raise ValueError("Only an inactive resumable Run can be abandoned")
        run.metadata["resume_abandonment"] = ResumeAbandonment(
            now(), "conversation_deleted" if request_id is not None else "explicit", request_id).to_dict()
        self.save(run)

    def checkpoint_plan(self, session, run, store, *, attached=False):
        """Run 전체가 정리 단위다. 중첩 실행/승인 레코드를 개별 삭제하지 않는다."""
        from llm.services.infrastructure.storage import tree_snapshot, revision_token
        source = ResourceRef("run", run.id, project_id=session.project_id, session_id=session.id, run_id=run.id)
        abandoned = self.resume_abandoned(run)
        deleted = CheckpointRepository().deletion(run)
        names = run.metadata.get("checkpoints", []) or (
            [run.metadata["resume"]["checkpoint"]] if "resume" in run.metadata and deleted is None else [])
        # 손상된 계보/참조는 삭제 허가로 바뀌지 않는다.
        runs = self.list(session)
        resolve = self._checkpoint_reader(session, runs)
        self._resume_references(runs, resolve)
        messages = store.list()
        common = []
        def block(code, message):
            common.append(Diagnostic(code, message, source=source))
        if attached or session.active_run_ids or session.status == SessionStatus.RUNNING:
            block("runtime_attached", "Shut down Session runtime before checkpoint maintenance")
        if run.status in (RunStatus.PENDING, RunStatus.RUNNING):
            block("active_run", "Run is still active; recover stale Runs before maintenance")
        if any(m.status in (MessageStatus.QUEUED, MessageStatus.STREAMING) for m in messages):
            block("pending_message", "Finish or cancel pending requests before checkpoint maintenance")
        descendants = {run.id}
        changed = True
        while changed:
            changed = False
            for other in runs:
                if other.id not in descendants and other.metadata.get("resume", {}).get("run_id") in descendants:
                    descendants.add(other.id)
                    changed = True
        for other in runs:
            if other.id == run.id or other.id not in descendants:
                continue
            retired = CheckpointRepository().deletion(other) is not None
            if (not retired and not other.metadata.get("checkpoints")
                    or other.status not in (RunStatus.COMPLETED, RunStatus.CANCELLED) and not self.resume_abandoned(other)):
                block("resume_reference", f"Run {other.id} still requires its resume lineage")
        if any(m.metadata.get("resume", {}).get("run_id") == run.id
               and m.status != MessageStatus.CANCELLED and not RequestAdmission.run_ids(m) for m in messages):
            block("resume_reserved", "A resume request still references this Run")
        if deleted is not None or not names:
            block("no_checkpoints", "Run has no retained checkpoints")
        # 읽기 자체가 누락/승인 손상을 검증한다. 삭제 후 승인 원본은 감사용으로만 보존한다.
        payloads = [resolve(run, name) for name in names]
        with self._interaction_scope(session, run, resolve):
            self.interaction_requests(session, run)
            responses = self.interaction_responses(session, run)
        files = tree_snapshot(run.paths.state / "checkpoints")
        interaction_files = tree_snapshot(run.paths.state / "interactions")
        abandon = list(common)
        if abandoned or run.status not in (RunStatus.PAUSED, RunStatus.FAILED, RunStatus.INTERRUPTED):
            abandon.append(Diagnostic("not_resumable", "Only an un-abandoned inactive resumable Run can be abandoned", source=source))
        delete = list(common)
        if not abandoned and run.status not in (RunStatus.COMPLETED, RunStatus.CANCELLED):
            delete.append(Diagnostic("resume_required", "Abandon resume before deleting checkpoints", source=source))
        revision = revision_token({"run": record(run), "session": record(session),
            "runs": [record(r) for r in runs], "messages": [record(m) for m in messages],
            "payloads": payloads, "files": files, "interaction_files": interaction_files,
            "responses": [r.to_dict() for r in responses], "attached": attached})
        return CheckpointPlan(source, list(names), sum(v["bytes"] for v in files.values()),
                              abandoned, abandon, delete, revision)

    def delete_checkpoints(self, session, run, store, *, expected_revision):
        with self._interaction_scope(session, run):
            plan = self.checkpoint_plan(session, run, store)
            require_checkpoint_plan(plan, expected_revision, action="delete")
            requests = self.interaction_requests(session, run)
            CheckpointRepository().delete(run, names=plan.checkpoints,
                interactions=requests, revision=plan.revision)
        self.save(run)

    def resume_references(self, session: Session) -> list[tuple[Run, set[str], str]]:
        runs = self.list(session)
        return self._resume_references(runs, self._checkpoint_reader(session, runs))

    def resumable_message_ids(self, session: Session) -> set[str]:
        return {identifier for _, identifiers, _ in self.resume_references(session) for identifier in identifiers}

    def interaction_views(self, session, run, store, *, link=None):
        message, resumed = self.resume_link(session, run, store) if link is None else link
        with self._interaction_scope(session, run) as (interactions, _):
            requests = self.interaction_requests(session, run)
            return interactions.views(run, requests,
                self.interaction_responses(session, run), resume_message=message, resumed_run=resumed,
                resume_abandoned=self.resume_abandoned(run))

    def change_interaction(self, session, run, request, *, operation, expires_at=None):
        self.require_resume(run)
        current = next((r for r in self.interaction_requests(session, run) if r.id == request.id), None)
        if current is None or current.fingerprint != request.fingerprint:
            raise ValueError("Interaction changed; reload before editing")
        repository = InteractionRepository()
        if operation == "cancel":
            repository.cancel(run, current)
            return current
        if operation == "renew":
            return repository.renew(run, current, expires_at=expires_at)
        raise ValueError("Unknown interaction operation")

    def checkpoint(self, run: Run, name: str | None = None) -> dict:
        from llm.core.checkpoints import select_checkpoint
        # EngineContext retains a Run-start snapshot. The storage owner reads
        # the current revision witness, including when called from a handler.
        current = Run.from_record(read_domain_record(run.paths.root / "run.json"), run.paths)
        if (current.id, current.session_id, current.engine) != (run.id, run.session_id, run.engine):
            raise ValueError("Checkpoint Run ownership mismatch")
        return CheckpointRepository().load(current, select_checkpoint(current.metadata.get("checkpoints", []), name))

    def resolve_checkpoint(self, session: Session, run: Run, name: str | None = None) -> dict:
        from llm.core.checkpoints import select_checkpoint
        names = run.metadata.get("checkpoints", [])
        if name is None and not names and "resume" in run.metadata:
            # An explicit durable reference exists even if its initial copy failed.
            # _resolve_checkpoint still verifies lineage, digest and initialization.
            name = run.metadata["resume"]["checkpoint"]
        name = select_checkpoint(names, name)
        current = self._interaction_reads.get()
        if current is not None and current[0][0] == session.paths.root:
            return deepcopy(current[2](run, name))
        return self._resolve_checkpoint(run, name, lambda identifier: self.load(session, identifier), self.checkpoint)

    def list(self, session: Session, *, query=None) -> list[Run]:
        if type(query) is Query:
            return [self.load(session, identifier) for identifier in self.catalog.select(self.record_paths(session), query)]
        return select(sorted((self.load(session, path.parent.name)
                       for path in self.record_paths(session)),
                      key=lambda run: (run.created_at, run.id)), query)

    def unfinished(self, session: Session) -> "list[Run]":
        """Load active records through the shared catalog, not completed history."""
        paths = tuple(self.record_paths(session))
        identifiers = {identifier for status in Run.ACTIVE_STATES
                       for identifier in self.catalog.select(paths, Query(status=status))}
        return sorted((self.load(session, identifier) for identifier in identifiers),
                      key=lambda run: (run.created_at, run.id))

    def list_results(self, sessions, *, include_running=False, query=None) -> "list[Run]":
        """결과 순서로 선택한 Run만 로드한다. 원본은 run.json이며 별도 결과 파일은 없다."""
        if query is not None and type(query) is not Query:
            raise TypeError("Result selection requires Query or None")
        if query is not None and query.limit == 0 and query.after is None:
            return []
        def visible(run):
            return include_running or run.status not in (RunStatus.PENDING, RunStatus.RUNNING)
        key = lambda run: (run.ended_at or run.started_at or "", run.id)
        # 현재 저장소 확장점의 소유권을 유지한다. 사용자 list/load 구현을 파일 경로로 우회하지 않는다.
        if query is None or type(self).list is not RunRepository.list or type(self).load is not RunRepository.load:
            return select_ordered((r for session in sessions for r in self.list(session) if visible(r)), query, key=key)
        owners = {session.id: session for session in sessions}
        def rows():
            for session in sessions:
                for path in self.record_paths(session):
                    self.paths(session, path.parent.name)
                    row = self.result_catalog.read(path)
                    if row.session_id != session.id:
                        raise ValueError("Run ownership mismatch")
                    if visible(row):
                        yield row
        selected = select_ordered(rows(), query, key=key)
        return [self.load(owners[row.session_id], row.id) for row in selected]


class RunErrorCode(StrEnum):
    AGENT_REQUIRED = "agent_required"
    ENGINE_REQUIRED = "engine_required"
    ENGINE_NOT_REGISTERED = "engine_not_registered"
    COMPONENT_NOT_REGISTERED = "component_not_registered"
    CAPABILITY_FAILED = "capability_failed"
    ENGINE_FAILED = "engine_failed"
    PROVIDER_CAPACITY = "provider_capacity"
    INTERRUPTED = "interrupted"
    PROCESS_RESTART = "process_restart"
    RESUME_REJECTED = "resume_rejected"
    QUEUE_FULL = "queue_full"
    POLICY_UNAVAILABLE = "policy_unavailable"
    REQUEST_CANCELLED = "request_cancelled"
    STEERING_UNAVAILABLE = "steering_unavailable"
    RUN_TIMEOUT = "run_timeout"
    TOOL_DENIED = "tool_denied"
    TOOL_CONTRACT = "tool_contract"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_BUDGET_EXCEEDED = "tool_budget_exceeded"
    CONTEXT_BUDGET_EXCEEDED = "context_budget_exceeded"
    OPERATION_CONFLICT = "operation_conflict"
    OPERATION_UNCERTAIN = "operation_uncertain"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
    PROCESS_FAILED = "process_failed"


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
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class RunEvent:
    type: RunEventType
    run: Run


class _RunPaused(Exception):
    """Engine이 저장된 경계에서 실행을 양보했다. 실패나 자동 재개가 아니다."""


@dataclass(frozen=True, slots=True)
class _RunOutcome:
    """한 실행 시도의 종료 판단. 영속 모델이나 새로운 상태 소유자가 아니다."""

    status: RunStatus
    error: Optional[str] = None
    error_code: Optional[str] = None

    def __post_init__(self) -> None:
        validate_run_transition(RunStatus.RUNNING, self.status, reason="finish")
        if any(value is not None and not isinstance(value, str) for value in (self.error, self.error_code)):
            raise TypeError("Run outcome error and error_code must be text or None")
        if self.status in (RunStatus.COMPLETED, RunStatus.PAUSED) and (
                self.error is not None or self.error_code is not None):
            raise ValueError("Completed/paused Run outcome cannot contain a failure")


# 영속 요청 큐를 실행으로 전환하고 이벤트 저장과 복구를 책임진다.
class RunManager:
    """One Session-bound scheduler; separate instances execute separate Sessions."""

    RESPONSE_WRITE_FAILED = "response_write_failed"


    def __init__(self, sessions: SessionManager, engines: EngineRegistry, *, session: Session,
                 steps: Optional[StepManager] = None,
                 repository: Optional[RunRepository] = None,
                 capabilities: Optional[Union[CapabilityResolver, ComponentRegistry]] = None,
                 conversations: Optional[Callable[[Session], Conversation]] = None,
                 context_builder: Optional[ConversationContextBuilder] = None,
                 event_handlers: Optional[EventHandlers] = None,
                 subscriptions: Optional[EventSubscriptions] = None,
                 tool_runtime: Optional[ToolRuntime] = None,
                 policy_resolver=None, provider_calls=None, output_buffer=None, observability=None,
                 provider_diagnostics=None) -> None:
        if not isinstance(session, Session):
            raise TypeError("RunManager requires a Session")
        self._session = deepcopy(session)
        self.sessions = sessions
        self.engines = engines
        self.agents = RunAgentResolver(engines, sessions.agents.repository)
        self.repository = repository if repository is not None else sessions.run_repository
        if self.repository is None:
            self.repository = RunRepository()
            sessions.run_repository = self.repository
        self.steps = steps if steps is not None else StepManager()
        self.recorder = StepEventRecorder(self.steps)
        self.results = RunResultQuery(sessions, self.repository)
        self.event_handlers = event_handlers if event_handlers is not None else EventHandlers()
        self.subscriptions = subscriptions if subscriptions is not None else EventSubscriptions()
        self._owns_subscriptions = subscriptions is None
        if capabilities is None:
            capabilities = ComponentRegistry()
        self.capabilities = (ComponentToolResolver(capabilities, data_factory=self._component_data,
                             agents=sessions.agents, engines=engines)
                             if isinstance(capabilities, ComponentRegistry) else capabilities)
        if not callable(getattr(self.capabilities, "resolve", None)):
            raise TypeError("Capability resolver must implement resolve(project, names)")
        self.conversations = conversations if conversations is not None else sessions.conversations
        self.context_builder = context_builder if context_builder is not None else sessions.context_builder
        self._active: Optional[SessionRuntime] = None
        self._closed = False
        self._control = None
        self._io = StorageIO(sessions.ownership)
        self._store_instance: Optional[Conversation] = None
        self._request_changed: Optional[asyncio.Event] = None
        self.policy_resolver = policy_resolver if policy_resolver is not None else ProjectPolicyResolver()
        self.tool_runtime = tool_runtime if tool_runtime is not None else ToolRuntime()
        self.provider_calls = provider_calls if provider_calls is not None else ProviderCalls()
        self.provider_diagnostics = provider_diagnostics
        self.output_buffer = output_buffer or OutputBuffer()
        self.pending_work = PendingWork()
        from llm.services.infrastructure.observability import Observability
        self.observability = observability if observability is not None else Observability()
        if self._owns_subscriptions:
            self.subscriptions.observability = self.observability

    def _check_component_access(self):
        if self._closed:
            raise RuntimeError("RunManager is shut down")

    def _component_data(self, project, name):
        """실행용 capability도 기존 저장소·잠금·수명 검사를 공유한다."""
        from llm.services.lifecycle.components import ComponentData
        registry = self.capabilities.components
        component = registry.get(name)
        data_class = getattr(component, "data_class", None) or ComponentData
        return data_class(self.sessions.project_access, registry, project, name).bind_resources(
            self.sessions, self.steps.repository, agents=self.sessions.agents).bind_model_usage(
            self.sessions, getattr(self.policy_resolver, "token_counters", {})).bind_runtime(
            runner=self._io.run, access_check=self._check_component_access,
            history_reader=lambda session_id, run_id, step_id, **options: self.results.tool_result(
                project, session_id, run_id, step_id, steps=self.steps, **options))

    def _observer_failed(self, run):
        """실행 루프에서 발생한 관찰자 오류에도 백엔드의 로그 설정을 적용한다."""
        from llm.services.infrastructure.logging import logging_scope
        with logging_scope(self.sessions.ownership.logger):
            log_event(run.paths.logs, "observer.failed", entity_id=run.id)

    def _control_lock(self):
        if self._control is None:
            self._control = asyncio.Lock()
        return self._control

    def _notify_requests(self):
        if self._request_changed is not None:
            self._request_changed.set()
            self._request_changed = None

    async def _publish_instructions(self, run, values):
        event = EngineEvent(EngineEventType.STEERING_CHANGED,
                           metadata={"instructions": [v.to_dict() for v in values],
                                     "targets": deepcopy(list(run.metadata["steering"].get("targets", {}).values()))})
        await self.subscriptions.publish("engine", run, event, on_error=lambda: self._observer_failed(run))

    def _accept_instruction(self, run_id, content, targets, reserved=False):
        _, session = self._prepare()
        run = self.repository.load(session, run_id)
        if (run.status != RunStatus.RUNNING or run.id not in session.active_run_ids
                or not run.metadata.get("steering", {}).get("accepting")):
            raise RunRequestError(RunErrorCode.STEERING_UNAVAILABLE,
                                  "Run does not accept live instructions")
        control = self._active.executions.get(run.id) if self._active is not None else None
        if self._active is None or self._active.closed or control is None or control.interrupt_requested:
            raise RunRequestError(RunErrorCode.STEERING_UNAVAILABLE, "Run is stopping or owned by another runtime")
        available = run.metadata["steering"]["targets"]
        identifiers = () if reserved else targets if targets is not None else ("root",)
        if not reserved and (not identifiers or len(set(identifiers)) != len(identifiers)):
            raise ValueError("Choose distinct active instruction targets")
        selected = reservation_receipts(run, targets) if reserved else []
        for identifier in identifiers:
            target = available.get(identifier)
            if target is None or not target["accepting"] or target["mode"] != SteeringMode.CONSUME:
                raise RunRequestError(RunErrorCode.STEERING_UNAVAILABLE, f"Instruction target unavailable: {identifier}")
            selected.append({"id": identifier, "scope": target["scope"], "status": InstructionStatus.PENDING, "applications": []})
        store = self._store(session)
        message = store.create(MessageRole.USER, content, MessageStatus.QUEUED, run_id=run.id,
            metadata={"steering": {"run_id": run.id, "status": InstructionStatus.PENDING,
                       "input_message_id": run.metadata["steering"]["input_message_id"], "targets": selected}})
        return run, RunInstruction.from_message(message)

    async def _send_instruction(self, run_id, content, targets, *, reserved=False):
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Live instruction must be nonempty text")
        async def accept():
            async with self._control_lock():
                if self._closed:
                    raise RuntimeError("RunManager is shut down")
                return await self._io.run(self._accept_instruction, run_id, content, targets, reserved)
        run, value = await drain_on_cancel(accept())
        await self._publish_instructions(run, [value])
        return value

    def _steering_boundary(self, run, store, event, tool_usage):
        return steering_boundary(self.repository, run, store, event, tool_usage=tool_usage)

    def _open_resumed_steering(self, run: Run, checkpoint: dict) -> None:
        """재개 원본 검증 뒤에만 접수한다. 검증 실패도 기존 Run 종료 경계에서 처리한다."""
        run.metadata["steering"] = initial_channel(run, steering_mode(self.engines.get(run.engine)),
                                                  checkpoint["header"]["input_message_id"])
        self.repository.save(run)

    async def _publish_run(self, run: Run) -> None:
        self._notify_requests()
        event = RunEvent(RunEventType(run.status.value if run.status != RunStatus.RUNNING else "started"),
                         deepcopy(run))
        await self.subscriptions.publish("run", event, on_error=lambda: self._observer_failed(run))

    def _store(self, session: Session) -> Conversation:
        if (session.project_id, session.id) != (self._session.project_id, self._session.id):
            raise ValueError("Session does not match this RunManager")
        if self._store_instance is None:
            self._store_instance = self.conversations(session)
        return self._store_instance

    def _prepare(self):
        project = self.sessions._owner(self._session)
        current = self.sessions.load(project, self._session.id)
        if current.status == SessionStatus.DELETED:
            raise ValueError("Session is deleted")
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

    def _admit(self, runtime, content, bindings, request_context=UNSET, *, metadata=None):
        self._history(runtime.project, runtime.session)
        message_id = new_id()
        admission = RequestAdmission.create(project_id=runtime.project.id,
            session_id=runtime.session.id, message_id=message_id, bindings=bindings)
        values = {"admission": admission}
        if request_context is not UNSET:
            values["request_context"] = deepcopy(request_context)
        if metadata is not None:
            if values.keys() & metadata.keys():
                raise ValueError("Request provenance cannot replace admission evidence")
            values.update(deepcopy(metadata))
        return self._store(runtime.session).create(MessageRole.USER, content, MessageStatus.QUEUED,
            message_id=message_id, metadata=values)

    def _history(self, project, session):
        """Require complete history both before ACK and before frozen dispatch."""
        messages, records = self._store(session).list(), self.repository.list(session)
        RequestAdmission.history(messages, records, project_id=project.id,
            session_id=session.id, persistent=project.conversation_storage == "file")
        return messages, records

    def _validate_project_request(self, project):
        try:
            self.policy_resolver.resolve(project.config.policies)
        except (ValueError, TypeError, ExecutionLimitError) as error:
            raise RunRequestError(RunErrorCode.POLICY_UNAVAILABLE, str(error)) from error
        validate = getattr(self.capabilities, "validate", None)
        if callable(validate):
            try:
                validate(project)
            except ValueError as error:
                raise RunRequestError(RunErrorCode.COMPONENT_NOT_REGISTERED, str(error)) from error

    def _source_agent(self, project, session, run, agent_id=None, *, records=None):
        binding = self.agents.source(project, run)
        if agent_id is not None and binding["agent_id"] != agent_id:
            raise ValueError("Resume requires the original Agent")
        try:
            original = self._store(session).get(run.input_message_id)
        except KeyError as error:
            raise ValueError("Original conversation is unavailable; memory-mode resume requires the original backend") from error
        selected = RequestAdmission.runs(original, self.repository.list(session) if records is None else records,
            project_id=project.id, session_id=session.id)
        if not any(candidate.id == run.id for candidate in selected):
            raise ValueError("Run is absent from its original request admission")
        admission = RequestAdmission.read(original, project_id=project.id, session_id=session.id)
        if self.agents.metadata(binding, run.metadata.get("request_context", UNSET)) != self.agents.metadata(
                admission["bindings"][run.agent_id], original.metadata.get("request_context", UNSET)):
            raise ValueError("Run Agent binding or request context differs from its admitted input")
        return binding

    def _source_strategy(self, project, session, run, agent_id=None, *, records=None):
        binding = self._source_agent(project, session, run, agent_id, records=records)
        return self.agents.strategy(binding, request_context=run.metadata.get("request_context", UNSET))

    async def _runtime(self) -> SessionRuntime:
        # Caller holds _control. OS ownership is retained before recovery; the
        # worker and all pending storage finish before that ownership is released.
        if self._closed:
            raise RuntimeError("RunManager is shut down")
        project, current = await self._io.run(self._prepare)
        if self._active is not None:
            runtime = self._active
            if runtime.worker is not None and runtime.worker.done():
                runtime.worker.result()
                raise RuntimeError("Session worker has stopped")
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
                queued = [message.id for message in self._store(fresh).list()
                          if is_queued_request(message)]
                log_event(fresh.paths.logs, "runtime.started", entity_id=fresh.id,
                          count=len(queued))
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
        runtime.worker = asyncio.create_task(self._worker(runtime), name=f"llm-session-{current.id}")
        return runtime

    async def _start(self) -> SessionRuntime:
        async with self._control_lock():
            return await self._runtime()

    async def _submit(self, content: str, recipients, request_context) -> Message:
        async with self._control_lock():
            if self._closed:
                raise RuntimeError("RunManager is shut down")
            await self._io.run(self._validate_request, recipients, request_context)
            runtime = await self._runtime()
            def persist():
                bindings = self._validate_request(recipients, request_context)
                self._check_queue(runtime.session)
                message = self._admit(runtime, content, bindings, request_context)
                log_event(runtime.session.paths.logs, "request.queued", entity_id=message.id,
                          related_id=runtime.session.id)
                return message

            message = await self._io.run(persist)
            # Accepted submission is shielded through this insertion, including
            # caller cancellation and concurrent shutdown. The selected store is
            # updated first (fsync for the default file store).
            runtime.queue.put_nowait(message.id)
            return message

    def _check_queue(self, session):
        project = self.sessions._owner(session)
        maximum = project.config.policies.get("run", {}).get("max_queued")
        if maximum is not None:
            waiting = {message.id for message in self._store(session).list(
                query=Query(status=MessageStatus.QUEUED)) if is_queued_request(message)}
            waiting.update(run.input_message_id for run in self.repository.unfinished(session)
                           if run.status == RunStatus.PENDING)
            if len(waiting) >= maximum:
                raise RunRequestError(RunErrorCode.QUEUE_FULL, "Session request queue is full")

    def _cancel_queued(self, message_id):
        project, session = self._prepare()
        store = self._store(session)
        message = store.get(message_id)
        RequestAdmission.runs(message, self.repository.list(session),
                              project_id=project.id, session_id=session.id)
        if message.status != MessageStatus.QUEUED or message.run_id is not None:
            return False
        # 새 백엔드의 복구 이전에도 이미 Run으로 청구된 요청은 취소하지 않는다.
        if self._active is None and any(run.input_message_id == message_id for run in self.repository.list(session)):
            return False
        store.set_status(message_id, MessageStatus.CANCELLED)
        return True

    async def _cancel_request(self, message_id):
        async with self._control_lock():
            if self._closed:
                raise RuntimeError("RunManager is shut down")
            cancelled = await self._io.run(self._cancel_queued, message_id)
            if cancelled:
                self._notify_requests()
            return cancelled

    def _resume_request(self, runtime, run_id, agent_id, retry_nodes, decisions, *, preview=False, partial_output=False,
                        source=None, expected_revision=None, link=None, records=None):
        """소유권 잠금 안에서 중복 재개를 거부하고 요청을 먼저 영속 큐에 저장한다."""
        if self.pending_work.active:
            raise RunRequestError(RunErrorCode.RESUME_REJECTED, "Cancelled work is still finishing")
        self._check_queue(runtime.session)
        if expected_revision is not None:
            if expected_revision != self._resume_plan(run_id, agent_id).revision:
                raise CheckpointError("checkpoint_conflict", "review a fresh resume plan")
        source = source or self.repository.load(runtime.session, run_id)
        self.repository.require_resume(source)
        project = self.sessions.require_project(runtime.project)
        binding = self._source_agent(project, runtime.session, source, agent_id, records=records)
        self._validate_project_request(project)
        if source.agent_id != agent_id or source.status not in (
                RunStatus.PAUSED, RunStatus.INTERRUPTED, RunStatus.FAILED):
            raise ValueError("Resume requires a paused/interrupted/failed Run and its original Engine")
        strategy = self.agents.strategy(binding, request_context=source.metadata.get("request_context", UNSET))
        name = getattr(strategy, "checkpoint_name", None)
        if not name or not callable(getattr(strategy, "validate_resume", None)):
            raise ValueError("Engine does not support checkpoint resume")
        checkpoint = self.repository.resolve_checkpoint(runtime.session, source, name)
        ToolExecutionScope.checkpoint_usage(checkpoint["records"])
        decisions, receipts, retry_nodes = self.repository.interaction_decisions(
            runtime.session, source, checkpoint, decisions, retry_nodes=retry_nodes, confirm=True)
        strategy.validate_resume(checkpoint, retry_nodes=retry_nodes, **({"decisions": decisions} if decisions else {}))
        # 파일 대화는 대기 요청도 재시작 뒤 유지한다. 메모리 큐가 없어져도 이미 시작한
        # 재개 Run의 연결은 남으므로 같은 원본에서 부작용을 두 번 이어가지 않는다.
        store = self._store(runtime.session)
        if any(self.repository.resume_link(runtime.session, source, store) if link is None else link):
            raise ValueError("This Run already has a resume request; resume its latest attempt instead")
        descriptor = {"run_id": source.id, "checkpoint": name,
                      "digest": checkpoint_digest(checkpoint), "retry_nodes": list(retry_nodes), "decisions": deepcopy(decisions)}
        if partial_output:
            descriptor["partial_output"] = True
        project = self.sessions.require_project(runtime.project)
        candidate = deepcopy(source)
        candidate.metadata["resume"] = descriptor
        candidate.metadata["policies"] = deepcopy(project.config.policies)
        strategy.validate_resume(checkpoint, retry_nodes=retry_nodes,
                                 context=self._context(runtime, candidate, project=project, engine=strategy), **({"decisions": decisions} if decisions else {}))
        if preview:
            return descriptor
        for response in receipts:
            self.repository.respond(runtime.session, source, response)
        return self._admit(runtime, store.get(source.input_message_id).content, {agent_id: binding},
                           source.metadata.get("request_context", UNSET), metadata={"resume": descriptor})

    def _resume_plan(self, run_id, agent_id) -> ResumePlan:
        project, session = self._prepare()
        try:
            source = self.repository.load(session, run_id)
        except (ValueError, KeyError) as error:
            raise CheckpointError("checkpoint_corrupt", "Run lifecycle proof is invalid") from error
        ref = ResourceRef("run", run_id, project_id=project.id, session_id=session.id, run_id=run_id)
        reused, retries, blockers, views, can_resume = [], [], [], [], False
        state, checkpoint, strategy = ResumeState.UNKNOWN, None, None
        store = self._store(session)
        messages, runs = store.list(), self.repository.list(session)
        link = self.repository.resume_link(session, source, store, messages=messages, records=runs)
        try:
            strategy = self._source_strategy(project, session, source, agent_id, records=runs)
            if CheckpointRepository().deletion(source) is not None:
                state = ResumeState.DELETED
                raise ValueError("Checkpoints were explicitly deleted")
            if not getattr(strategy, "checkpoint_name", None) or not callable(getattr(strategy, "validate_resume", None)):
                state = ResumeState.UNSUPPORTED
                raise ValueError("Engine does not support checkpoint resume")
            state = ResumeState.CORRUPT
            with self.repository._interaction_scope(session, source):
                checkpoint = self.repository.resolve_checkpoint(session, source, strategy.checkpoint_name)
                ToolExecutionScope.checkpoint_usage(checkpoint["records"])
                reused = [k for k, v in checkpoint["records"].items() if v.get("status") == CheckpointStatus.COMPLETED.value]
                state = ResumeState.VALID
                self.repository.require_resume(source)
                state = ResumeState.MISSING
                self._validate_checkpoint_input(store, checkpoint, source.input_message_id, messages=messages)
                state = ResumeState.VALID
                views = self.repository.interaction_views(session, source, store, link=link)
                retries = [v.request.binding["key"] for v in views if v.request.binding.get("target") == InteractionRepository.RETRY_NODES_TARGET]
                if any(v.status in (InteractionView.PENDING, InteractionView.DENIED, InteractionView.EXPIRED,
                                    InteractionView.CANCELLED, InteractionView.SUBMITTED, InteractionView.UNAVAILABLE) for v in views):
                    blockers.append(Diagnostic("interaction_required", "Resolve pending interactions before execution", source=ref))
                else:
                    state = ResumeState.INCOMPATIBLE
                    self._resume_request(SessionRuntime(project, session), run_id, agent_id, (), {}, preview=True, source=source, link=link, records=runs)
                    state, can_resume = ResumeState.VALID, True
        except ToolUsageError as error:
            state = ResumeState.CORRUPT
            blockers.append(Diagnostic.from_exception(error, code=error.code, source=ref))
        except FileNotFoundError as error:
            state = (ResumeState.MISSING if source.metadata.get("checkpoints") or "resume" in source.metadata else
                     ResumeState.NOT_CREATED if source.metadata.get("execution_started") is False else ResumeState.UNKNOWN)
            blockers.append(Diagnostic.from_exception(error, code="checkpoint_" + state, source=ref))
        except (ValueError, KeyError, RuntimeError, AttributeError) as error:
            blockers.append(Diagnostic.from_exception(error, code="resume_rejected", source=ref))
        restart_blockers = []
        if strategy is None:
            restart_blockers.append(Diagnostic("engine_unavailable", "Original Engine configuration is unavailable", source=ref))
        # Only a service-persisted pre-dispatch state proves an invocation never
        # started. Missing Steps/checkpoints, including custom Engines, prove nothing.
        if (source.metadata.get("execution_started") is not False or source.metadata.get("resume")
                or source.metadata.get("checkpoints") or source.metadata.get("checkpoint_deletion")
                or self.repository.resume_abandoned(source) or source.agent_id != agent_id
                or source.status not in (RunStatus.FAILED, RunStatus.INTERRUPTED)
                or self.steps.list(source)):
            restart_blockers.append(Diagnostic("restart_unproven", "No durable proof of a pre-execution stop", source=ref))
        if any(r.metadata.get("restart", {}).get("run_id") == run_id or
               r.metadata.get("resume", {}).get("run_id") == run_id for r in runs) or any(
               any(m.metadata.get(key, {}).get("run_id") == run_id for key in ("resume", "restart"))
               and m.status != MessageStatus.CANCELLED for m in messages):
            restart_blockers.append(Diagnostic("restart_reserved", "A later request already uses this Run", source=ref))
        original = next((m for m in messages if m.id == source.input_message_id), None)
        if original is None or original.metadata.get("conversation_deleted"):
            restart_blockers.append(Diagnostic("input_unavailable", "Original input is unavailable", source=ref))
        if any(self.repository.load(session, identifier).agent_id == source.agent_id
               for identifier in session.active_run_ids) or self.pending_work.active:
            restart_blockers.append(Diagnostic("active_run", "Session still has active work", source=ref))
        revision = revision_token({"run": record(source), "checkpoint": checkpoint, "views": [v.to_dict() for v in views],
            "project": project.config.to_dict(), "session": record(session), "messages": [record(m) for m in messages],
            "runs": [record(r) for r in runs]})
        return ResumePlan(ref, source.engine, can_resume, views, reused, retries, blockers,
            state=state, revision=revision, can_restart=not restart_blockers, restart_blockers=restart_blockers,
            active=source.status in (RunStatus.PENDING, RunStatus.RUNNING), abandoned=self.repository.resume_abandoned(source))

    async def _resume(self, run_id, agent_id, retry_nodes, decisions, partial_output, expected_revision):
        async with self._control_lock():
            runtime = await self._runtime()
            try:
                message = await self._io.run(self._resume_request, runtime, run_id, agent_id, retry_nodes, decisions,
                                             partial_output=partial_output, expected_revision=expected_revision)
            except (RunRequestError, CheckpointError):
                raise
            except (ValueError, KeyError, FileNotFoundError, TypeError, RuntimeError) as error:
                raise RunRequestError(RunErrorCode.RESUME_REJECTED, str(error)) from error
            runtime.queue.put_nowait(message.id)
            return message

    def _restart_request(self, runtime, run_id, agent_id, expected_revision):
        plan = self._resume_plan(run_id, agent_id)
        if not expected_revision or expected_revision != plan.revision:
            raise CheckpointError("checkpoint_conflict", "review a fresh resume plan")
        if not plan.can_restart:
            raise CheckpointError("checkpoint_protected", "Restart has not been admitted", blockers=plan.restart_blockers)
        source = self.repository.load(runtime.session, run_id)
        project = self.sessions.require_project(runtime.project)
        binding = self._source_agent(project, runtime.session, source, agent_id)
        self._validate_project_request(project)
        self._check_queue(runtime.session)
        store = self._store(runtime.session)
        original = store.get(source.input_message_id)
        return self._admit(runtime, original.content, {agent_id: binding},
            source.metadata.get("request_context", UNSET), metadata={"restart": {
                "run_id": run_id, "digest": revision_token({"run": record(source), "input": record(original)})}})

    def _restart_source(self, session, run):
        descriptor = run.metadata["restart"]
        source = self.repository.load(session, descriptor["run_id"])
        original = self._store(session).get(source.input_message_id)
        if (source.metadata.get("execution_started") is not False or source.engine != run.engine
                or source.agent_id != run.agent_id or source.metadata["agent_binding"] != run.metadata["agent_binding"]
                or self.agents.metadata(source.metadata["agent_binding"], source.metadata.get("request_context", UNSET))
                   != self.agents.metadata(run.metadata["agent_binding"], run.metadata.get("request_context", UNSET))
                or source.status not in (RunStatus.FAILED, RunStatus.INTERRUPTED)
                or self.steps.list(source) or self.repository.resume_abandoned(source)
                or original.metadata.get("conversation_deleted")
                or revision_token({"run": record(source), "input": record(original)}) != descriptor["digest"]):
            raise CheckpointError("checkpoint_conflict", "Restart source changed after admission")

    def _request_runs(self, message_id: str) -> list[Run]:
        project, session = self._prepare()
        message = self._store(session).get(message_id)
        runs = RequestAdmission.runs(message, self.repository.list(session),
            project_id=project.id, session_id=session.id)
        if message.status == MessageStatus.CANCELLED:
            raise RunRequestError(RunErrorCode.REQUEST_CANCELLED, "Request was cancelled before execution")
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
        self._closed = True
        runtime = self._active
        if runtime is None:
            return
        runtime.closed = True
        self.pending_work.wake()
        for control in runtime.executions.values():
            control.interrupt()
        runtime.changed.set()
        runtime.queue.put_nowait(None)
        try:
            if runtime.worker is not None:
                await runtime.worker
        finally:
            if runtime.worker is None or runtime.worker.done():
                await self._io.run(self._detach, runtime.session)
                self._store_instance = None
                self._active = None

    def _detach(self, session: Session) -> None:
        log_event(session.paths.logs, "runtime.stopped", entity_id=session.id)
        # 스토리지 스레드와 이벤트 루프 사이 완료 경쟁에서도 소유권을 정확히 해제한다.
        self.pending_work.when_idle(lambda: self.sessions.detach_runtime(session))

    def _recover(self, session: Session) -> list[Run]:
        from llm.services.history.recovery import recover_session
        return recover_session(self.sessions, self.repository, self.steps, session, self._store(session))

    def _new_run(self, runtime, message, binding):
        session = runtime.session
        run_id = new_id()
        binding = AgentBinding.read(binding)
        if binding["project_id"] != runtime.project.id:
            raise ValueError("Queued Agent binding belongs to another Project")
        run = Run(run_id, session.id, message.id, new_id(), binding["engine"],
                  self.repository.paths(session, run_id), agent_id=binding["agent_id"])
        run.metadata["policies"] = deepcopy(runtime.project.config.policies)
        run.metadata.update(self.agents.metadata(binding, message.metadata.get("request_context", UNSET)))
        # Service dispatch fact, not an effects guarantee after execution.
        # Records without this field remain unknown; never backfill them.
        run.metadata["execution_started"] = False
        if "restart" in message.metadata:
            run.metadata["restart"] = deepcopy(message.metadata["restart"])
        if "resume" in message.metadata:
            run.metadata["resume"] = deepcopy(message.metadata["resume"])
            source = self.repository.load(session, run.metadata["resume"]["run_id"])
            if (source.metadata["agent_binding"] != binding
                    or "agent_execution" not in source.metadata):
                raise ValueError("Original Agent execution evidence is missing or inconsistent")
            # Preserve the already-admitted evidence in the creation transaction,
            # even if later checkpoint copying or capability preparation fails.
            # Existing records are never repaired from another Run or current config.
            run.metadata["agent_execution"] = deepcopy(source.metadata["agent_execution"])
        strategy = self.engines.get(run.engine) if run.engine in self.engines.names() else None
        if steering_mode(strategy) != SteeringMode.UNSUPPORTED:
            run.metadata["steering"] = ({"accepting": False} if "resume" in run.metadata
                                        else initial_channel(run, steering_mode(strategy), message.id))
        self.repository.create(run)
        return run

    def _dispatch_request(self, runtime: SessionRuntime, message: Message) -> list[Run]:
        from llm.services.infrastructure.transactions import watch
        watch(runtime.session)
        runtime.project = self.sessions.require_project(runtime.project)
        session, store = runtime.session, self._store(runtime.session)
        message = store.get(message.id)
        messages, records = self._history(runtime.project, session)
        existing = RequestAdmission.runs(message, records,
            project_id=runtime.project.id, session_id=session.id)
        if not is_queued_request(message):
            return []
        if existing:
            raise ValueError("Queued request already has dispatched Runs")
        admission = RequestAdmission.read(message, project_id=runtime.project.id, session_id=session.id)
        runs = [self._new_run(runtime, message, admission["bindings"][identifier])
                for identifier in admission["recipients"]]
        # One transaction owns the whole group and its shared history. No child
        # task or Provider can start before every recipient is durably linked.
        history = self.context_builder.snapshot_for_run(messages, message.id)
        history = tuple(replace(item, status=MessageStatus.COMMITTED)
                        if item.id == message.id else item for item in history)
        dispatched = RequestAdmission.dispatch(admission,
            run_ids={run.agent_id: run.id for run in runs}, history=history)
        store.update_metadata(message.id, {"admission": dispatched})
        store.set_status(message.id, MessageStatus.COMMITTED)
        for run in runs:
            store.create(MessageRole.ASSISTANT, "", MessageStatus.QUEUED,
                         message_id=run.assistant_message_id, run_id=run.id)
        return runs

    def _begin(self, runtime: SessionRuntime, run: Run) -> Run:
        from llm.services.infrastructure.transactions import watch
        watch(runtime.session)
        session, store = runtime.session, self._store(runtime.session)
        current = self.repository.load(session, run.id)
        validate_run_transition(current.status, RunStatus.RUNNING, reason="start")
        if current.agent_id != run.agent_id or current.metadata != run.metadata:
            raise ValueError("Pending Run changed before execution")
        store.set_status(run.assistant_message_id, MessageStatus.STREAMING)
        session.active_run_ids = sorted([*session.active_run_ids, run.id])
        session.status = SessionStatus.RUNNING
        self.sessions._save_runtime(session)
        run.status, run.started_at = RunStatus.RUNNING, now()
        self.repository.save(run)
        log_event(run.paths.logs, "run.started", entity_id=run.id,
                  related_id=session.id, status=run.status)
        activity_event(runtime.project, run, "run.started", time=run.started_at, status=run.status)
        return run

    def _validate_checkpoint_input(self, store, checkpoint, input_message_id, *, messages=None):
        identifiers = checkpoint_message_ids(checkpoint) | {input_message_id}
        get = store.get if messages is None else {message.id: message for message in messages}.__getitem__
        try:
            if any(get(identifier).metadata.get("conversation_deleted") for identifier in identifiers):
                raise ValueError("Deleted conversation messages cannot be used for resume; submit a new request")
        except KeyError as error:
            raise ValueError("Original conversation is unavailable; memory-mode resume requires the original backend") from error

    def _resume_checkpoint(self, session, run):
        descriptor = run.metadata["resume"]
        source = self.repository.load(session, descriptor["run_id"])
        self.repository.require_resume(source)
        if (source.engine != run.engine or source.agent_id != run.agent_id
                or source.metadata["agent_binding"] != run.metadata["agent_binding"]
                or self.agents.metadata(source.metadata["agent_binding"], source.metadata.get("request_context", UNSET))
                   != self.agents.metadata(run.metadata["agent_binding"], run.metadata.get("request_context", UNSET))
                or source.status not in (
                RunStatus.PAUSED, RunStatus.INTERRUPTED, RunStatus.FAILED)):
            raise ValueError("Resume source is no longer eligible")
        checkpoint = self.repository.resolve_checkpoint(session, source, descriptor["checkpoint"])
        if checkpoint_digest(checkpoint) != descriptor["digest"]:
            raise ValueError("Resume checkpoint changed after admission")
        if any(request.expired for request in self.repository.interaction_requests(session, source)):
            raise ValueError("Interaction expired while resume was queued")
        store = self._store(session)
        self._validate_checkpoint_input(store, checkpoint, source.input_message_id)
        return resume_instructions(store, checkpoint)

    def _partial_outputs(self, session, run):
        """Provide unfinished source observations; only Engines interpret Step metadata."""
        from llm.core.results import ResumeOutput
        descriptor = run.metadata.get("resume", {})
        if not descriptor.get("partial_output"):
            return ()
        source = self.repository.load(session, descriptor["run_id"])
        steps = {step.id: step for step in self.steps.list(source)
                 if step.status != StepStatus.COMPLETED}
        return tuple(ResumeOutput(source.id, output, steps.get(output.step_id))
                     for output in self.repository.outputs(source)
                     if not output.final and (output.step_id is None or output.step_id in steps))

    def _context(self, runtime: SessionRuntime, run: Run, policies=None, *, project=None, engine=None) -> EngineContext:
        context_policy, limits = (policies if policies is not None
                                                   else self.policy_resolver.resolve(run.metadata["policies"]))
        checkpoint = None
        store = self._store(runtime.session)
        if "restart" in run.metadata:
            self._restart_source(runtime.session, run)
        if "resume" in run.metadata:
            checkpoint = self._resume_checkpoint(runtime.session, run)
            header = checkpoint["header"]
            try:
                history = tuple(store.get(identifier) for identifier in header["message_ids"])
            except KeyError as error:
                raise ValueError("Original conversation is unavailable; memory-mode resume requires the original backend") from error
            history = tuple(replace(item, id=run.input_message_id, run_id=run.id)
                            if item.id == header["input_message_id"] else item for item in history)
        else:
            message = store.get(run.input_message_id)
            admission = RequestAdmission.read(message, project_id=runtime.project.id, session_id=runtime.session.id)
            if admission["dispatch"] is None:
                raise ValueError("Run requires its dispatched history snapshot")
            history = self.context_builder.select_snapshot(
                tuple(Message.from_record(value) for value in admission["dispatch"]["history"]),
                policy=context_policy)
        # Copy domain state, but do not deepcopy Python handler closures or live
        # capabilities. The resolver returns a fresh registry for this Run.
        if engine is None:
            engine = self._source_strategy(project if project is not None else runtime.project, runtime.session, run)
        names = required_capabilities(engine)
        project = deepcopy(project if project is not None else runtime.project)
        project.config["policies"] = deepcopy(run.metadata["policies"])
        values = {}
        if names:
            values = self.capabilities.resolve(project, names)
            if not isinstance(values, dict) or any(name not in values for name in names):
                raise ValueError("Capability resolver omitted a required capability")
        request = getattr(engine, "additional_capabilities", None)
        for expansion in count() if request is not None else ():
            extra = request(values)
            if (not isinstance(extra, tuple) or any(not isinstance(name, str) or not name.strip() for name in extra)
                    or len(set(extra)) != len(extra)):
                raise ValueError("additional_capabilities must return a tuple of distinct names")
            missing = tuple(name for name in extra if name not in values)
            if not missing:
                break
            if expansion == limits.max_capability_rounds:
                raise ValueError("Capability expansion limit exceeded")
            resolved = self.capabilities.resolve(project, missing)
            if not isinstance(resolved, dict) or any(name not in resolved for name in missing):
                raise ValueError("Capability resolver omitted a required capability")
            values.update(resolved)
        retries = run.metadata["policies"].get("tool_retry", {})
        tool_scope = ToolExecutionScope(self.tool_runtime, policy=run.metadata["policies"].get("tools", {}), retry=retries,
              operations=ToolOperations(deepcopy(runtime.session), self._io, self.repository, steps=self.steps))
        if checkpoint:
            tool_scope.restore_checkpoint(checkpoint["records"])
        for name in values.get("tools", ToolRegistry()).names():
            tool_scope.validate(values["tools"].get(name))
        inbox = (SteeringInbox(history=checkpoint_messages(store, checkpoint) if checkpoint else {})
                 if "steering" in run.metadata else None)
        if inbox is not None:
            inbox.engine, inbox.mode = run.engine, steering_mode(engine)
            inbox.channels[inbox.target_id] = inbox
        # One Run-local capability snapshot per name. Selected child inspection
        # and execution share it without repeatedly rebuilding provider resources.
        capability_cache = dict(values)
        def resolve_selected(names, *, expansion=None):
            if (expansion is not None and limits.max_capability_rounds is not None
                    and expansion >= limits.max_capability_rounds):
                raise ValueError("Capability expansion limit exceeded")
            missing = tuple(name for name in names if name not in capability_cache)
            if missing:
                resolved = self.capabilities.resolve(deepcopy(project), missing)
                if not isinstance(resolved, dict) or set(resolved) != set(missing):
                    raise ValueError("Capability resolver must return exactly the requested capabilities")
                capability_cache.update(resolved)
            return {name: capability_cache[name] for name in names}
        context = EngineContext(project, deepcopy(runtime.session), deepcopy(run), history,
                             tools=values.get("tools", ToolRegistry()), capabilities=values,
                             checkpoint=checkpoint, resume_outputs=self._partial_outputs(runtime.session, run),
                             tool_scope=tool_scope, pending_work=self.pending_work,
                             token_counters=dict(getattr(self.policy_resolver, "token_counters", {})), steering=inbox,
                             capability_resolver=resolve_selected)
        context = engine.prepare_context(context)
        if "resume" in run.metadata:
            source = self.repository.load(runtime.session, run.metadata["resume"]["run_id"])
            if "agent_execution" not in source.metadata:
                raise ValueError("Original Agent execution evidence is missing")
            if source.metadata["agent_execution"] != engine.runtime_binding:
                raise ValueError("Agent execution configuration changed since the original Run")
        return context

    def _bind_execution(self, run, engine):
        if "agent_execution" in run.metadata and run.metadata["agent_execution"] != engine.runtime_binding:
            raise ValueError("Agent execution binding is immutable")
        run.metadata["agent_execution"] = deepcopy(engine.runtime_binding)
        self.repository.save(run)

    def _dispatch(self, run):
        run.metadata["execution_started"] = True
        self.repository.save(run)

    async def _consume_limited(self, runtime, run):
        # ACK before resolving capabilities or invoking an Engine implementation.
        await self._io.run(self._dispatch, run)
        from llm.providers.runtime import logging_scope as provider_logging_scope
        policies = self.policy_resolver.resolve(run.metadata["policies"])
        guard = timeout(policies[1].timeout_seconds)
        try:
            async with guard:
                counter = getattr(self.policy_resolver, "token_counters", {}).get(run.metadata["policies"].get("usage", {}).get("counter"))
                source = {"project_id": runtime.project.id, "session_id": runtime.session.id, "run_id": run.id}
                with self.observability.scope(), provider_logging_scope(self.sessions.ownership.path.parent, **source, engine=run.engine, diagnostics=self.provider_diagnostics), self.provider_calls.scope(), UsageScope(run.metadata["policies"].get("usage", {}), counter, source).scope():
                    await self._consume(runtime, run, policies)
        except asyncio.TimeoutError as error:
            expired = guard.expired() if callable(guard.expired) else guard.expired
            if expired:
                raise ExecutionLimitError(RunErrorCode.RUN_TIMEOUT, "Run execution deadline exceeded") from error
            raise

    async def _consume(self, runtime: SessionRuntime, run: Run, policies) -> None:
        if run.engine not in self.engines.names():
            raise RunRequestError(RunErrorCode.ENGINE_NOT_REGISTERED, "Requested Engine is not registered")
        try:
            engine = await self._io.run(self._source_strategy, runtime.project, runtime.session, run)
        except KeyError as error:
            raise RunRequestError(RunErrorCode.ENGINE_NOT_REGISTERED, "Requested Engine is not registered") from error
        store = self._store(runtime.session)
        try:
            if "resume" in run.metadata:
                checkpoint = await self._io.run(self._resume_checkpoint, runtime.session, run)
                await self._io.run(self.repository.inherit_interactions, runtime.session, run, checkpoint)
                # 실행 설정 검증이 실패해도 재개 시도의 원본 체크포인트는 남긴다.
                await self._io.run(self.repository.record_checkpoint, run,
                    EngineEvent(EngineEventType.CHECKPOINT, metadata={
                        "name": run.metadata["resume"]["checkpoint"], "operation": "initialize",
                        "header": checkpoint["header"], "records": checkpoint["records"]}))
            context = await self._io.run(self._context, runtime, run, policies, engine=engine)
            await self._io.run(self._bind_execution, run, engine)
            if context.checkpoint is not None and context.steering is not None:
                await self._io.run(self._open_resumed_steering, run, context.checkpoint)
        except ExecutionLimitError:
            raise
        except Exception as error:
            raise RunRequestError(RunErrorCode.CAPABILITY_FAILED, str(error)) from error
        events = engine.execute(context)
        paused = False
        outputs = RunOutputState()
        active_steps = set()
        async def handle(batch):
            nonlocal paused
            pending_outputs, pending_deltas, notifications = [], [], []
            for event in batch:
                if paused:
                    raise ValueError("Engine emitted events after pause")
                event, change = outputs.accept(event, active_steps, has_result="output" in run.metadata)
                if event.type == EngineEventType.CHECKPOINT and event.metadata.get("operation") == "record":
                    # Run 전체 예산은 서비스 스코프가 소유한다. Engine payload를 해석하지 않고
                    # 같은 durable 저장 경계에 스냅샷을 붙여 명시적 재개가 예산을 초기화하지 않게 한다.
                    event = replace(event, metadata={**event.metadata,
                        "value": context.tool_scope.checkpoint_record(event.metadata["value"])})
                if event.checkpoint is not None:
                    receipt = event.checkpoint
                    event = replace(event, checkpoint=replace(receipt, metadata={**receipt.metadata,
                        "value": context.tool_scope.checkpoint_record(receipt.metadata["value"])}))
                value = event.delta if event.type == EngineEventType.TEXT_DELTA else event.output
                if event.type == EngineEventType.STEERING:
                    if context.steering is None:
                        raise ValueError("Engine has no live instruction channel")
                    channel = context.steering.channels[event.metadata["target_id"]]
                    if event.metadata["operation"] == "open":
                        target = event.metadata["target"]
                        if (target["scope"] != channel.scope or target["mode"] != channel.mode
                                or target["engine"] != channel.engine):
                            raise ValueError("Instruction target does not match its execution channel")
                    usage = context.tool_scope.checkpoint() if event.metadata["operation"] == "select" else None
                    messages, changed = await self._io.run(self._steering_boundary, run, store, event, usage)
                    if event.metadata["operation"] in ("routes", "bind"):
                        channel.acknowledged = event.metadata["request_id"]
                        if event.metadata["operation"] == "routes":
                            notifications.append(EngineEvent(EngineEventType.STEERING_CHANGED,
                                metadata={"routes": deepcopy(list(run.metadata["steering"]["routes"].values()))}))
                        if changed:
                            notifications.append(EngineEvent(EngineEventType.STEERING_CHANGED,
                                metadata={"instructions": [v.to_dict() for v in changed]}))
                        continue
                    channel.messages = messages
                    channel.history.update((m.id, m) for m in messages)
                    channel.acknowledged = event.metadata["request_id"]
                    channel.opened = True
                    channel.closed = run.metadata["steering"]["targets"][channel.target_id]["reason"] is not None
                    if channel.closed and channel.target_id != "root":
                        # UI 이력은 저장된 descriptor가 소유한다. 완료한 자식 전달함은 회수한다.
                        context.steering.channels.pop(channel.target_id)
                    if event.metadata["operation"] in ("open", "close") or channel.closed:
                        notifications.append(EngineEvent(EngineEventType.STEERING_CHANGED,
                            metadata={"targets": deepcopy(list(run.metadata["steering"]["targets"].values()))}))
                    if changed:
                        notifications.append(EngineEvent(EngineEventType.STEERING_CHANGED,
                            metadata={"instructions": [v.to_dict() for v in changed]}))
                    continue
                if event.type == EngineEventType.TEXT_DELTA:
                    pending_outputs.append(value)
                    if change is not None:
                        pending_deltas.append(change)
                elif event.type in (EngineEventType.OUTPUT, EngineEventType.COMPLETION,
                                    EngineEventType.CHECKPOINT, EngineEventType.PAUSED,
                                    EngineEventType.STEP_STARTED, EngineEventType.STEP_UPDATED, EngineEventType.STEP_COMPLETED,
                                    EngineEventType.STEP_FAILED, EngineEventType.STEP_INTERRUPTED,
                                    EngineEventType.STEP_CANCELLED):
                    # 한 이벤트의 쓰기를 한 저장 경계에서 끝낸 뒤 런타임 상태를 진행한다.
                    await self._io.run(self._record_engine_event, runtime.project, run, store, event)
                    if event.type == EngineEventType.STEP_STARTED:
                        active_steps.add(event.step_id)
                    elif event.type in (EngineEventType.STEP_COMPLETED, EngineEventType.STEP_FAILED,
                                        EngineEventType.STEP_INTERRUPTED, EngineEventType.STEP_CANCELLED):
                        active_steps.discard(event.step_id)
                    elif event.type == EngineEventType.PAUSED:
                        paused = True
                else:
                    await self.event_handlers.handle(EventContext(run, context, self._io, self.repository), event)
                # Keep checkpoint observers independent of event composition.
                # Both observations are published only after the atomic commit.
                if event.checkpoint is not None:
                    notifications.append(event.checkpoint)
                notifications.append(event)
            if pending_outputs:
                await drain_on_cancel(self._io.run(self._record_deltas, run, store, pending_outputs, pending_deltas))
            for event in notifications:
                await self.subscriptions.publish("engine", run, event, on_error=lambda: self._observer_failed(run))

        try:
            output_buffer = self.output_buffer.for_project(run.metadata["policies"].get("output", {}))
            await consume_events(events, output_buffer, handle)

        finally:
            close = getattr(events, "aclose", None)
            if close is not None:
                await close()
        if paused:
            raise _RunPaused()
        if context.steering is not None:
            if steering_mode(engine) == SteeringMode.CONSUME and not context.steering.opened:
                raise ValueError("Instruction consumer did not open its channel")
            if any(t["accepting"] for t in run.metadata["steering"]["targets"].values()):
                raise ValueError("Engine ended without closing instruction targets")
        if any(step.status in (StepStatus.PENDING, StepStatus.RUNNING, StepStatus.FAILED)
               for step in await self._io.run(self.steps.list, run)):
            raise RuntimeError("Engine ended with unfinished or failed Steps")

    def _record_engine_event(self, project: Project, run: Run, store: Conversation, event: EngineEvent) -> None:
        """표준 이벤트 하나의 저장 단위. IO 실행기가 잠금·트랜잭션을 제공한다."""
        if event.type == EngineEventType.COMPLETION:
            self._record_completion(run, event)
        elif event.type == EngineEventType.CHECKPOINT:
            self.repository.record_checkpoint(run, event)
        elif event.type == EngineEventType.PAUSED:
            # 일시정지를 알리기 전에 체크포인트가 실제로 존재해야 한다.
            self.repository.checkpoint(run, event.metadata["checkpoint"])
        else:
            if event.output is not None:
                self.repository.record_output(run, event.output)
            if event.type == EngineEventType.OUTPUT and event.output.step_id is None:
                self._record_result(run, store, event.output)
            else:
                if event.type == EngineEventType.OUTPUT:
                    event = replace(event, type=EngineEventType.STEP_UPDATED)
                self.recorder.record(run, event)
                if event.checkpoint is not None:
                    self.repository.record_checkpoint(run, event.checkpoint)
                if event.type == EngineEventType.STEP_FAILED:
                    activity_event(project, run, "step.failed", time=now(), status=StepStatus.FAILED.value,
                                   step_id=event.step_id, code=event.failure_code or "step_failed")

    def _record_deltas(self, run: Run, store: Conversation, outputs, deltas) -> None:
        """출력 저널과 대화 델타를 같은 트랜잭션에 저장한다. 알림은 호출자가 보낸다."""
        if len(outputs) == 1:
            self.repository.record_output(run, outputs[0])
        else:
            self.repository.record_outputs(run, outputs)
        if len(deltas) == 1:
            text, operation = deltas[0]
            if operation == "append":
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
        if (value.visibility == "user" and (value.text or value.data is None)
                and store.get(run.assistant_message_id).content != value.text):
            store.delta(run.assistant_message_id, value.text, operation="replace")
        run.metadata["output"] = value.to_dict()
        self.repository.save(run)

    def _record_completion(self, run: Run, event: EngineEvent) -> None:
        if event.completion is None:
            raise ValueError("Completion event requires a result")
        result = asdict(event.completion)
        child(run.paths.state, result["id"])
        entries = run.metadata.setdefault("completions", [])
        for index, previous in enumerate(entries):
            if previous["id"] == result["id"]:
                entries[index] = result
                break
        else:
            policy = run.metadata["policies"].get("usage", {})
            project_entries = []
            auxiliary = []
            project_limited = has_limit(policy, project=True)
            if project_limited or has_limit(policy):
                project = self.sessions.project_access.load(self._session.project_id)
                reader = getattr(self.capabilities, "model_usage", None)
                if not callable(reader):
                    raise ExecutionLimitError("usage_configuration",
                        "Usage limits require CapabilityResolver.model_usage(project)")
                auxiliary = reader(project)
                if not isinstance(auxiliary, list) or any(not isinstance(entry, dict) for entry in auxiliary):
                    raise ExecutionLimitError("usage_configuration", "Component usage must be a list of receipts")
            if project_limited:
                reader = getattr(self.repository, "completion_history", None)
                project_entries = [entry for session in self.sessions.list(project, include_deleted=True)
                                   for entry in (reader(session) if reader else [e for r in self.repository.list(session)
                                       for e in r.metadata.get("completions", [])])]
            check_admission(run, result, event.completion.reserved_tokens, [*project_entries, *auxiliary],
                            [e for e in auxiliary if e.get("run_id") == run.id])
            entries.append(result)
        self.repository.save(run)

    def _finish(self, runtime: SessionRuntime, run: Run, status: RunStatus,
                error: Optional[str] = None, error_code: Optional[str] = None) -> list[RunInstruction]:
        # 변경 전 검증한다. 중복 종료로 시각/오류/Step을 덮거나 다음 Run의 Session을 해제하지 않는다.
        validate_run_transition(run.status, status, reason="finish")
        _RunOutcome(status, error, error_code)
        if run.session_id != runtime.session.id or run.id not in runtime.session.active_run_ids:
            raise ValueError("Run finalization requires the Session's current Run")
        from llm.services.infrastructure.transactions import watch
        watch(runtime.session)
        for step in self.steps.list(run):
            if step.status in (StepStatus.PENDING, StepStatus.RUNNING):
                if status == RunStatus.FAILED:
                    self.steps.fail(step, error or "Run failed")
                else:
                    self.steps.interrupt(step)
        store = self._store(runtime.session)
        changed = []
        if "steering" in run.metadata:
            changed = finish_instructions(store, run.id,
                run.metadata["steering"].get("unapplied_reason") or error_code or status.value)
            close_targets(run, error_code or status.value)
        store.set_status(run.assistant_message_id, MessageStatus(status.value))
        run.status = status
        run.error = error
        run.error_code = error_code
        run.ended_at = now()
        self.repository.save(run)
        log_event(run.paths.logs, f"run.{status.value}", entity_id=run.id,
                  status=status)
        runtime.session.active_run_ids.remove(run.id)
        runtime.session.status = SessionStatus.RUNNING if runtime.session.active_run_ids else SessionStatus.IDLE
        self.sessions._save_runtime(runtime.session)
        activity_event(runtime.project, run, f"run.{status.value}", time=run.ended_at,
                       status=run.status, code=run.error_code)
        return changed

    async def _run_one(self, runtime: SessionRuntime, control: RunRuntime) -> None:
        run = control.run
        started = time.monotonic()
        try:
            await self._io.run(self._begin, runtime, run)
            self.observability.record(ObservationKind.RUNS.value, ObservationStatus.STARTED.value,
                run_id=run.id, project_id=runtime.project.id, session_id=runtime.session.id)
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
                outcome = _RunOutcome(RunStatus.FAILED, str(failure),
                                      stable_error_code(failure) or RunErrorCode.ENGINE_FAILED)
                issue = Diagnostic.from_exception(failure, code=outcome.error_code,
                    boundary="engine", source=ResourceRef("run", run.id, project_id=runtime.project.id,
                                       session_id=runtime.session.id, run_id=run.id))
                if issue.code != outcome.error_code:
                    issue = replace(issue, code=outcome.error_code,
                                    details={**issue.details, "observed_code": issue.code})
                run.metadata["diagnostic"] = issue.to_dict()
            finally:
                changed = await self._io.run(self._finish, runtime, run,
                                             outcome.status, outcome.error, outcome.error_code)
                self.observability.record(ObservationKind.RUNS.value, outcome.status.value,
                    code=outcome.error_code, duration_seconds=time.monotonic() - started,
                    run_id=run.id, project_id=runtime.project.id, session_id=runtime.session.id)
                if outcome.status == RunStatus.PAUSED:
                    try:
                        await self._io.run(self.repository.apply_interaction_policy, runtime.session, run,
                                           run.metadata["policies"].get("approval", {}))
                    except Exception:
                        log_event(run.paths.logs, "interaction.policy_failed", entity_id=run.id,
                                  status=RunManager.RESPONSE_WRITE_FAILED)
                await self._publish_run(run)
                if "steering" in run.metadata:
                    await self._publish_instructions(run, changed)
        finally:
            control.execution = None
            control.finished.set()
            runtime.changed.set()

    async def _pump(self, runtime: SessionRuntime) -> None:
        for identifier, control in tuple(runtime.executions.items()):
            if control.task.done():
                control.task.result()
                del runtime.executions[identifier]
        occupied = {control.run.agent_id for control in runtime.executions.values()}
        for message_id in tuple(runtime.waiting):
            runs = runtime.groups.get(message_id)
            if runs is not None and all(run.status not in Run.ACTIVE_STATES for run in runs):
                # Finalizers, including publication, must finish before the group
                # releases its queue accounting or the next request's history.
                if any(run.id in runtime.executions for run in runs):
                    continue
                runtime.waiting.remove(message_id)
                del runtime.groups[message_id]
                runtime.queue.task_done()
                continue
            if runs is None:
                message = await self._io.run(self._store(runtime.session).get, message_id)
                admission = RequestAdmission.read(message, project_id=runtime.project.id,
                                                   session_id=runtime.session.id)
                if message.status == MessageStatus.CANCELLED:
                    runtime.waiting.remove(message_id)
                    runtime.queue.task_done()
                    continue
                recipients = set(admission["recipients"])
                if not runtime.closed and not self.pending_work.active and recipients - occupied:
                    runs = await self._io.run(self._dispatch_request, runtime, message)
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
                    control.task = asyncio.create_task(self._run_one(runtime, control),
                                                       name=f"llm-run-{run.id}")
                # Older pending recipients reserve FIFO position even when another
                # recipient in this group can already execute in parallel.
                occupied.add(run.agent_id)

    async def _worker(self, runtime: SessionRuntime) -> None:
        failure = None
        try:
            while not runtime.closed:
                runtime.changed.clear()
                await self._pump(runtime)
                if runtime.closed:
                    break
                reader = asyncio.create_task(runtime.queue.get())
                changed = asyncio.create_task(runtime.changed.wait())
                quarantine = asyncio.create_task(self.pending_work.wait()) if self.pending_work.active else None
                waiters = [reader, changed]
                if quarantine is not None:
                    waiters.append(quarantine)
                try:
                    await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                    if reader.done():
                        identifier = reader.result()
                        if identifier is None:
                            runtime.queue.task_done()
                        else:
                            runtime.waiting.append(identifier)
                        while not runtime.queue.empty():
                            identifier = runtime.queue.get_nowait()
                            if identifier is None:
                                runtime.queue.task_done()
                            else:
                                runtime.waiting.append(identifier)
                finally:
                    for waiter in waiters:
                        waiter.cancel()
                    await asyncio.gather(*waiters, return_exceptions=True)
        except BaseException as error:
            failure = error
            raise
        finally:
            controls = tuple(runtime.executions.values())
            for control in controls:
                control.interrupt()
            results = await asyncio.gather(*(control.task for control in controls), return_exceptions=True)
            if failure is None:
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
            from llm.core.models import ProjectConfig
            if not isinstance(request_context, dict):
                raise TypeError("request_context must be a JSON object")
            ProjectConfig.validate_json(request_context)
            request_context = deepcopy(request_context)
        return await drain_on_cancel(self._submit(content, recipients, request_context))

    async def steer(self, run_id: str, content: str, *, targets=None) -> RunInstruction:
        """현재 Run에 추가 지시를 저장한다. 일반 요청 큐나 새 Run을 만들지 않는다."""
        if targets is not None:
            if not isinstance(targets, (list, tuple)) or any(not isinstance(v, str) or not v for v in targets):
                raise ValueError("targets must be a list of execution target IDs")
            targets = tuple(targets)
        return await self._send_instruction(run_id, content, targets)

    async def reserve_instruction(self, run_id: str, content: str, *, targets) -> RunInstruction:
        """경로별 다음 Agent 실행 한 번에 예약한다. 실행 중 대상에는 소급 적용하지 않는다."""
        if not isinstance(targets, (list, tuple)) or any(not isinstance(v, SteeringRoute) for v in targets):
            raise ValueError("targets must be a list of SteeringRoute values")
        return await self._send_instruction(run_id, content, tuple(targets), reserved=True)

    async def resume_plan(self, run_id: str, *, agent_id: str) -> ResumePlan:
        """큐 등록이나 모델 호출 없이 재사용·승인·재개 차단 사유를 조회한다."""
        return await self._io.run(self._resume_plan, run_id, agent_id)

    async def restart(self, run_id: str, *, agent_id: str, expected_revision: str) -> Message:
        """Only a durably proven pre-dispatch stop can become a new request."""
        async def apply():
            async with self._control_lock():
                runtime = await self._runtime()
                message = await self._io.run(self._restart_request, runtime, run_id, agent_id, expected_revision)
                runtime.queue.put_nowait(message.id)
                return message
        return await drain_on_cancel(apply())

    async def resume(self, run_id: str, *, agent_id: str, retry_nodes=(), decisions=None, partial_output=False,
                     expected_revision=None) -> Message:
        """체크포인트에서 새 Run을 예약한다. 불확실한 노드의 키는 명시적으로 승인한다."""
        if decisions is not None and not isinstance(decisions, dict):
            raise TypeError("Resume decisions must be a JSON object")
        if type(partial_output) is not bool:
            raise TypeError("partial_output must be a boolean")
        decisions = json.loads(json.dumps(decisions if decisions is not None else {}, allow_nan=False))
        return await drain_on_cancel(self._resume(run_id, agent_id, tuple(retry_nodes), decisions, partial_output, expected_revision))

    async def cancel_request(self, message_id: str) -> bool:
        """대기 요청만 취소한다. 실행 중이면 False이며 interrupt로 명시적으로 중단한다."""
        return await drain_on_cancel(self._cancel_request(message_id))

    async def wait_request(self, message_id: str, *, timeout: Optional[float] = None) -> list[Run]:
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
                if self._request_changed is None:
                    self._request_changed = asyncio.Event()
                changed = self._request_changed
                runs = await self._io.run(self._request_runs, message_id)
                if runs and all(run.status not in Run.ACTIVE_STATES for run in runs):
                    return runs
                if runtime.worker.done():
                    runtime.worker.result()
                    raise RuntimeError("Session runtime stopped before this request finished")
                notification = asyncio.create_task(changed.wait())
                try:
                    await asyncio.wait((notification, runtime.worker), return_when=asyncio.FIRST_COMPLETED)
                finally:
                    notification.cancel()
                    await asyncio.gather(notification, return_exceptions=True)
        return await asyncio.wait_for(wait(), timeout=timeout)

    async def interrupt(self, run_id=None) -> bool:
        runtime = self._active
        if runtime is None:
            return False
        controls = [control for identifier, control in runtime.executions.items()
                    if (run_id is None or identifier == run_id) and not control.finished.is_set()]
        if not controls:
            return False
        for control in controls:
            control.interrupt()
        await asyncio.gather(*(control.finished.wait() for control in controls))
        await self._io.run(log_event, runtime.session.paths.logs, "runtime.interrupted", entity_id=runtime.session.id)
        return True

    async def wait_idle(self) -> None:
        runtime = await drain_on_cancel(self._start())
        assert runtime.worker is not None
        joined = asyncio.create_task(runtime.queue.join())
        try:
            done, _ = await asyncio.wait((joined, runtime.worker),
                                         return_when=asyncio.FIRST_COMPLETED)
            if runtime.worker in done:
                runtime.worker.result()
                if not joined.done():
                    raise RuntimeError("Session worker stopped before draining its queue")
            await joined
        finally:
            joined.cancel()
            await asyncio.gather(joined, return_exceptions=True)

    async def shutdown(self) -> None:
        await drain_on_cancel(self._shutdown())
