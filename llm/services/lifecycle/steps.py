"""Step의 시작, 완료, 실패, 중단 상태와 저장을 담당한다. 실제 LLM/Tool 작업은 실행하지 않으며 Engine 이벤트를 저장 상태로 변환한다."""

from llm.core.execution_observations import ExecutionObservations
from llm.core.models import StepErrorCode

from llm.core.models import ResourceKind

from llm.services.infrastructure.storage import read_domain_record, atomic_domain_json

from llm.services.query import FileCatalog, Query, select
from copy import deepcopy
from typing import Optional
from llm.core.models import Run, Step, StepStatus, new_id, now, validate_step_transition
from llm.core.json_data import JsonObject
from llm.core.paths import StepPaths
from llm.core.results import EngineOutput
from llm.engines.base import EngineEvent, EngineEventType
from llm.services.infrastructure.storage import atomic_json, child, read_json, record
from llm.services.infrastructure.logging import log_event


# Run 아래의 Step 메타데이터와 조회를 담당한다.
class StepRepository:
    """Step metadata and paths beneath the owning Run's steps root."""

    def __init__(self, *, catalog_size=FileCatalog.DEFAULT_MAX_ENTRIES):
        self.catalog = FileCatalog(catalog_size)

    def read_backup(self, run):
        steps = []
        for path in self.record_paths(run):
            data = read_domain_record(path)
            if child(run.paths.steps, data["id"]) != path.parent or data["run_id"] != run.id:
                raise ValueError("Backup Step ownership mismatch")
            step = Step.from_record(data, StepPaths(path.parent))
            if step.status in (StepStatus.PENDING, StepStatus.RUNNING):
                raise ValueError("Backup contains an unfinished Step")
            steps.append(step)
        return steps

    def paths(self, run: Run, step_id: str) -> StepPaths:
        return StepPaths(child(run.paths.steps, step_id))

    def exists(self, run: Run, step_id: str) -> bool:
        paths = self.paths(run, step_id)
        if paths.root.exists():
            self.load(run, step_id)
            return True
        return False

    def record_paths(self, run):
        from llm.services.infrastructure.storage import reject_links
        for directory in run.paths.steps.glob("*"):
            reject_links(directory)
            if directory.is_dir() and not directory.name.startswith("."):
                yield self.paths(run, directory.name).record

    def create(self, step: Step) -> None:
        if step.paths.root.exists():
            raise FileExistsError("Step storage already exists")
        self._write(step)

    def save(self, step: Step) -> None:
        prior = Step.from_record(read_domain_record(step.paths.record), step.paths)
        if prior.id != step.id or prior.run_id != step.run_id:
            raise ValueError("Step ownership mismatch")
        if prior.status != step.status:
            validate_step_transition(prior.status, step.status)
        self._write(step)

    def _write(self, step: Step) -> None:
        data = record(step)
        Step.from_record(data, step.paths)
        atomic_domain_json(step.paths.record, data)
        log_event(step.paths.logs, "step.saved", entity_id=step.id, status=step.status)

    def load(self, run: Run, step_id: str) -> Step:
        paths = self.paths(run, step_id)
        data = read_domain_record(paths.record)
        if data["id"] != step_id or data["run_id"] != run.id:
            raise ValueError("Step ownership mismatch")
        log_event(paths.logs, "step.loaded", entity_id=step_id)
        return Step.from_record(data, paths)

    def list(self, run: Run, *, query=None) -> list[Step]:
        if type(query) is Query:
            return [self.load(run, identifier) for identifier in self.catalog.select(self.record_paths(run), query)]
        return select(sorted((self.load(run, path.parent.name)
                       for path in self.record_paths(run)),
                      key=lambda step: (step.created_at, step.id)), query)


# 실행 단위의 상태 전이만 관리하며 실제 작업을 수행하지 않는다.
class StepManager:
    """Step lifecycle; storage is delegated to StepRepository."""

    def __init__(self, repository: Optional[StepRepository] = None) -> None:
        self.repository = repository if repository is not None else StepRepository()

    def _finish(self, step: Step, status: StepStatus, error: Optional[str] = None) -> None:
        from llm.services.infrastructure.transactions import watch
        watch(step)
        validate_step_transition(step.status, status)
        step.status = status
        step.ended_at = now()
        step.error = error
        from llm.core.contracts import Diagnostic, OperationProgress, ResourceRef
        from dataclasses import replace
        ref = ResourceRef(ResourceKind.STEP, step.id, step_id=step.id, run_id=step.run_id)
        progress = step.progress or OperationProgress(ref, status.value)
        ExecutionObservations.store_progress(step.metadata, replace(progress, phase=status.value))
        if error is not None and not ExecutionObservations.has_diagnostic(step.metadata):
            ExecutionObservations.store_diagnostic(step.metadata, Diagnostic(StepErrorCode.FAILED, error, source=ref))
        self.save(step)
        log_event(step.paths.logs, f"step.{status.value}", entity_id=step.id, status=status)

    # 공개 API
    def create(self, run: Run, kind: str, name: str, *,
               step_id: Optional[str] = None, metadata: Optional[dict] = None) -> Step:
        identifier = step_id or new_id()
        if self.repository.exists(run, identifier):
            raise ValueError("Duplicate Step ID")
        step = Step(identifier, run.id, kind, name, self.repository.paths(run, identifier),
                    metadata=metadata or {})
        self.repository.create(step)
        log_event(step.paths.logs, "step.created", entity_id=step.id, related_id=run.id)
        return step

    def save(self, step: Step) -> None:
        self.repository.save(step)

    def load(self, run: Run, step_id: str) -> Step:
        return self.repository.load(run, step_id)

    def list(self, run: Run, *, query=None) -> list[Step]:
        if query is not None and type(self.repository).list is StepRepository.list:
            return self.repository.list(run, query=query)
        return select(self.repository.list(run), query)

    def start(self, step: Step) -> None:
        from llm.services.infrastructure.transactions import watch
        watch(step)
        validate_step_transition(step.status, StepStatus.RUNNING)
        step.status = StepStatus.RUNNING
        step.started_at = now()
        self.save(step)
        log_event(step.paths.logs, "step.started", entity_id=step.id)

    def complete(self, step: Step) -> None:
        self._finish(step, StepStatus.COMPLETED)

    def fail(self, step: Step, error: str) -> None:
        self._finish(step, StepStatus.FAILED, error)

    def interrupt(self, step: Step) -> None:
        self._finish(step, StepStatus.INTERRUPTED)

    def cancel(self, step: Step) -> None:
        self._finish(step, StepStatus.CANCELLED)

    def recover(self, run: Run) -> None:
        for step in self.list(run):
            if step.status in (StepStatus.PENDING, StepStatus.RUNNING):
                self.interrupt(step)
                log_event(step.paths.logs, "step.recovered", entity_id=step.id)


class StepEventRecorder:
    def __init__(self, manager: StepManager) -> None:
        self.manager = manager

    def record(self, run: Run, event: EngineEvent) -> None:
        from dataclasses import replace
        from llm.core.contracts import ResourceRef
        metadata = dict(event.metadata)
        for value, store_observation in ((event.progress, ExecutionObservations.store_progress),
                                         (event.diagnostic, ExecutionObservations.store_diagnostic)):
            if value is None:
                continue
            ref = value.source
            if ref is not None and (ref.kind == ResourceKind.STEP and ref.id != event.step_id
                    or ref.step_id not in (None, event.step_id) or ref.run_id not in (None, run.id)
                    or ref.session_id not in (None, run.session_id)):
                raise ValueError("Step observation ownership mismatch")
            bound = replace(ref, step_id=event.step_id, run_id=run.id, session_id=run.session_id) if ref else ResourceRef(
                ResourceKind.STEP, event.step_id, step_id=event.step_id, run_id=run.id, session_id=run.session_id)
            store_observation(metadata, replace(value, source=bound))
        event = replace(event, metadata=metadata)
        if event.type == EngineEventType.TEXT_DELTA:
            return
        if event.step_id is None:
            raise ValueError("Step events require an ID")
        if event.type == EngineEventType.STEP_STARTED:
            if ExecutionObservations.has_output(event.metadata) or event.output is not None:
                raise ValueError("A Step cannot start with a final output")
            step = self.manager.create(run, event.kind, event.name,
                                       step_id=event.step_id, metadata=event.metadata)
            self.manager.start(step)
            return
        step = self.manager.load(run, event.step_id)
        # 최종 이벤트의 결과도 같은 Step에 저장한다. 별도 실행 결과 파일을 만들지 않는다.
        if event.metadata:
            if ExecutionObservations.has_output(event.metadata):
                raise ValueError("Use EngineEvent.output for Step results")
            JsonObject.validate(event.metadata)
            step.metadata.update(deepcopy(event.metadata))
        if event.output is not None:
            if not isinstance(event.output, EngineOutput) or event.output.step_id != step.id:
                raise ValueError("Step output ownership mismatch")
            ExecutionObservations.store_output(step.metadata, event.output)
        if event.type == EngineEventType.STEP_UPDATED:
            if step.status != StepStatus.RUNNING:
                raise ValueError("Only running Steps can report progress")
            self.manager.save(step)
        elif event.type == EngineEventType.STEP_COMPLETED:
            self.manager.complete(step)
        elif event.type == EngineEventType.STEP_FAILED:
            self.manager.fail(step, event.error or "Step failed")
        elif event.type == EngineEventType.STEP_INTERRUPTED:
            self.manager.interrupt(step)
        elif event.type == EngineEventType.STEP_CANCELLED:
            self.manager.cancel(step)
        else:
            raise ValueError("Unknown Step event")
