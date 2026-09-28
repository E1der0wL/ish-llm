"""Step의 시작, 완료, 실패, 중단 상태와 저장을 담당한다. 실제 LLM/Tool 작업은 실행하지 않으며 Engine 이벤트를 저장 상태로 변환한다."""

from llm.services.infrastructure.storage import read_domain_record, atomic_domain_json

from llm.services.query import FileCatalog, Query, select
from copy import deepcopy
from typing import Optional
from llm.core.models import ProjectConfig, Run, Step, StepStatus, new_id, now
from llm.core.paths import StepPaths
from llm.core.results import EngineOutput
from llm.engines.base import EngineEvent, EngineEventType
from llm.services.infrastructure.storage import atomic_json, child, read_json, record
from llm.services.infrastructure.logging import log_event


# Run 아래의 Step 메타데이터와 조회를 담당한다.
class StepRepository:
    """Step metadata and paths beneath the owning Run's steps root."""

    def __init__(self, *, catalog_size=4096):
        self.catalog = FileCatalog(catalog_size)

    def read_backup(self, run):
        steps = []
        for path in run.paths.steps.glob("*/step.json"):
            data = read_domain_record(path)
            if child(run.paths.steps, data["id"]) != path.parent or data["run_id"] != run.id:
                raise ValueError("Backup Step ownership mismatch")
            step = Step(**{**data, "paths": StepPaths(path.parent), "status": StepStatus(data["status"])})
            if step.status in (StepStatus.PENDING, StepStatus.RUNNING):
                raise ValueError("Backup contains an unfinished Step")
            steps.append(step)
        return steps

    def paths(self, run: Run, step_id: str) -> StepPaths:
        return StepPaths(child(run.paths.steps, step_id))

    def exists(self, run: Run, step_id: str) -> bool:
        return (self.paths(run, step_id).root / "step.json").exists()

    def save(self, step: Step) -> None:
        atomic_domain_json(step.paths.root / "step.json", record(step))
        log_event(step.paths.logs, "step.saved", entity_id=step.id, status=step.status)

    def load(self, run: Run, step_id: str) -> Step:
        paths = self.paths(run, step_id)
        data = read_domain_record(paths.root / "step.json")
        if data["id"] != step_id or data["run_id"] != run.id:
            raise ValueError("Step ownership mismatch")
        log_event(paths.logs, "step.loaded", entity_id=step_id)
        return Step(**{**data, "status": StepStatus(data["status"]), "paths": paths})

    def list(self, run: Run, *, query=None) -> list[Step]:
        if type(query) is Query:
            return [self.load(run, identifier) for identifier in self.catalog.select(run.paths.steps.glob("*/step.json"), query)]
        return select(sorted((self.load(run, path.parent.name)
                       for path in run.paths.steps.glob("*/step.json")),
                      key=lambda step: (step.created_at, step.id)), query)


# 실행 단위의 상태 전이만 관리하며 실제 작업을 수행하지 않는다.
class StepManager:
    """Step lifecycle; storage is delegated to StepRepository."""

    def __init__(self, repository: Optional[StepRepository] = None) -> None:
        self.repository = repository if repository is not None else StepRepository()

    def _finish(self, step: Step, status: StepStatus, error: Optional[str] = None) -> None:
        from llm.services.infrastructure.transactions import watch
        watch(step)
        if step.status not in (StepStatus.PENDING, StepStatus.RUNNING):
            raise ValueError("Step is already terminal")
        if status == StepStatus.COMPLETED and step.status != StepStatus.RUNNING:
            raise ValueError("Only running Steps can complete")
        step.status = status
        step.ended_at = now()
        step.error = error
        from llm.core.contracts import Diagnostic, OperationProgress, ResourceRef
        from dataclasses import replace
        ref = ResourceRef("step", step.id, step_id=step.id, run_id=step.run_id)
        progress = step.progress or OperationProgress(ref, status.value)
        step.metadata["progress"] = replace(progress, phase=status.value).to_dict()
        if error is not None and "diagnostic" not in step.metadata:
            step.metadata["diagnostic"] = Diagnostic("step_failed", error, source=ref).to_dict()
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
        self.save(step)
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
        if step.status != StepStatus.PENDING:
            raise ValueError("Only pending Steps can start")
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
        for name in ("progress", "diagnostic"):
            value = getattr(event, name)
            if value is None:
                continue
            ref = value.source
            if ref is not None and (ref.kind == "step" and ref.id != event.step_id
                    or ref.step_id not in (None, event.step_id) or ref.run_id not in (None, run.id)
                    or ref.session_id not in (None, run.session_id)):
                raise ValueError("Step observation ownership mismatch")
            bound = replace(ref, step_id=event.step_id, run_id=run.id, session_id=run.session_id) if ref else ResourceRef(
                "step", event.step_id, step_id=event.step_id, run_id=run.id, session_id=run.session_id)
            metadata[name] = replace(value, source=bound).to_dict()
        event = replace(event, metadata=metadata)
        if event.type == EngineEventType.TEXT_DELTA:
            return
        if event.step_id is None:
            raise ValueError("Step events require an ID")
        if event.type == EngineEventType.STEP_STARTED:
            if "output" in event.metadata or event.output is not None:
                raise ValueError("A Step cannot start with a final output")
            step = self.manager.create(run, event.kind, event.name,
                                       step_id=event.step_id, metadata=event.metadata)
            self.manager.start(step)
            return
        step = self.manager.load(run, event.step_id)
        # 최종 이벤트의 결과도 같은 Step에 저장한다. 별도 실행 결과 파일을 만들지 않는다.
        if event.metadata:
            if "output" in event.metadata:
                raise ValueError("Use EngineEvent.output for Step results")
            ProjectConfig.validate_settings(event.metadata)
            step.metadata.update(deepcopy(event.metadata))
        if event.output is not None:
            if not isinstance(event.output, EngineOutput) or event.output.step_id != step.id:
                raise ValueError("Step output ownership mismatch")
            step.metadata["output"] = event.output.to_dict()
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
