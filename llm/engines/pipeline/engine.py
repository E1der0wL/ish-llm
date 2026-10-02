"""준비 작업과 여러 Engine을 하나의 Run 안에서 순서대로 실행한다. 단계들의 capability 요구를 합치고 같은 실행 문맥을 공유한다.

Compose developer-supplied preparation and Engines inside a single Run."""

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Optional
from dataclasses import replace
from copy import copy, deepcopy
from collections.abc import Mapping
from contextlib import aclosing
from llm.core.models import ProjectConfig
from llm.core.configuration import engine_configuration
from llm.core.schema import object_schema, field
from llm.errors import CodedError

from llm.core.models import new_id
from llm.core.results import EngineOutput

from llm.engines.base import BaseEngine, Engine, EngineContext, EngineEvent, EngineEventType, required_capabilities


class PipelineError(CodedError, RuntimeError):
    """Preparation/stage lifecycle failure."""

    def __init__(self, message, *, code=None):
        super().__init__(message)
        self.code = code


_UNSET = object()


class PreparationStep(BaseEngine):
    """Convenience BaseEngine for async preparation; a deadline is applied only when configured."""

    def __init__(self, name: str, action: Callable[[EngineContext], Awaitable[None]], *,
                 kind: str = "preparation", timeout_seconds=_UNSET, settings_name=None) -> None:
        if not callable(action):
            raise TypeError("Preparation action must be callable")
        self._overrides = {} if timeout_seconds is _UNSET else {"timeout_seconds": timeout_seconds}
        if settings_name is not None and (not isinstance(settings_name, str) or not settings_name.strip()):
            raise ValueError("settings_name must be nonempty text")
        self.settings_name = settings_name
        super().__init__(name, kind=kind, action=action, timeout_seconds=None if timeout_seconds is _UNSET else timeout_seconds,
                         error_message="Preparation failed")

    def configuration_schema(self):
        return object_schema({"timeout_seconds": field(["number", "null"],
            exclusiveMinimum=0, **{"x-host-override": "timeout_seconds" in self._overrides})},
            **({"x-settings-key": self.settings_name} if self.settings_name else {}))

    def configuration(self, config, name, *, session_config=None):
        return engine_configuration(config, self.settings_name or name,
            session_config=session_config, host=self._overrides, schema=self.configuration_schema())

    async def execute(self, context):
        worker = copy(self)
        worker.timeout_seconds = self.configuration(context.project.config, context.run.engine,
            session_config=context.session.config)["values"].get("timeout_seconds")
        async with aclosing(BaseEngine.execute(worker, context)) as events:
            async for event in events:
                yield event


# 여러 실행 단계를 같은 Run 안에서 순서대로 연결한다.
class PipelineEngine:
    """Ordered Engine composition; all stages share the same Run context."""

    def __init__(self, stages: Sequence[Engine], *, settings_name=None) -> None:
        if settings_name is not None and (not isinstance(settings_name, str) or not settings_name.strip()):
            raise ValueError("settings_name must be nonempty text")
        self.settings_name = settings_name
        self.stage_names = tuple(stages) if isinstance(stages, Mapping) else tuple(str(i) for i in range(len(stages)))
        if any(not isinstance(name, str) or not name.strip() for name in self.stage_names):
            raise ValueError("Stage names must be nonempty text")
        self.stages = tuple(stages.values()) if isinstance(stages, Mapping) else tuple(stages)
        if not self.stages or any(not callable(getattr(stage, "execute", None)) for stage in self.stages):
            raise ValueError("Pipeline requires at least one Engine stage")
        self.required_capabilities = tuple(dict.fromkeys(
            name for stage in self.stages for name in required_capabilities(stage)))

    def _stage_settings(self, config, session_config, name, index):
        """단계 설정을 호출별 사본에만 연결한다. 저장된 Project/Session은 변경하지 않는다."""
        config, session = ProjectConfig(config), deepcopy(session_config or {})
        pipeline_key, stage = self.settings_name or name, self.stages[index]
        explicit = getattr(stage, "settings_name", None)
        key = explicit or (pipeline_key + ":" + self.stage_names[index] if hasattr(stage, "settings_name") else pipeline_key)
        for owner in (config, session):
            engines = owner.setdefault("engines", {})
            stages = engines.get(pipeline_key, {}).get("stages", {})
            if not isinstance(stages, dict) or stages.keys() - set(self.stage_names):
                raise ValueError("Unknown Pipeline stage configuration")
            options = stages.get(self.stage_names[index], {})
            if not isinstance(options, dict):
                raise ValueError("Stage configuration must be an object")
            base = {k: v for k, v in engines.get(explicit or pipeline_key, {}).items() if k != "stages"}
            engines[key] = ProjectConfig.merge(base, options)
        worker = stage
        if hasattr(stage, "settings_name"):
            worker = copy(stage)
            worker.settings_name = key
        return worker, config, session, key

    def configuration_schema(self):
        return object_schema({"stages": object_schema({name:
            stage.configuration_schema() if callable(getattr(stage, "configuration_schema", None)) else
            object_schema(**{"x-runtime-only": True}) for name, stage in zip(self.stage_names, self.stages)},
            additionalProperties=False)}, **({"x-settings-key": self.settings_name} if self.settings_name else {}))

    def configuration(self, config, name, *, session_config=None):
        stages = {}
        for index, stage_name in enumerate(self.stage_names):
            stage, project, session, key = self._stage_settings(config, session_config, name, index)
            describe = getattr(stage, "configuration", None)
            stages[stage_name] = describe(project, key, session_config=session) if describe else {"runtime_only": True}
        return {"configuration_key": self.settings_name or name, "stages": stages}

    async def execute(self, context: EngineContext) -> AsyncIterator[EngineEvent]:
        final_output = None
        for index, stage in enumerate(self.stages):
            stage, project, session, key = self._stage_settings(context.project.config, context.session.config, context.run.engine, index)
            active = set()
            stage_id = new_id()
            stage_output = None
            yield EngineEvent(EngineEventType.STEP_STARTED, step_id=stage_id, kind="engine",
                              name=type(stage).__name__, metadata={"stage_index": index,
                              "parent_step_id": context.output_step_id})
            try:
                events = stage.execute(replace(context, output_step_id=stage_id,
                    project=replace(context.project, config=project), session=replace(context.session, config=session)))
                try:
                    async for event in events:
                        if event.type == EngineEventType.PAUSED:
                            yield event
                            return
                        if event.type == EngineEventType.OUTPUT:
                            if event.output is None or event.output.step_id != stage_id or stage_output is not None:
                                raise PipelineError("Stage must emit one output owned by its stage Step")
                            stage_output = event.output
                            continue
                        if event.type == EngineEventType.STEP_STARTED:
                            active.add(event.step_id)
                            event = replace(event, metadata={"parent_step_id": stage_id, **event.metadata})
                        elif event.type == EngineEventType.STEP_COMPLETED:
                            active.discard(event.step_id)
                        yield event
                        if event.type in (EngineEventType.STEP_FAILED,
                                          EngineEventType.STEP_INTERRUPTED,
                                          EngineEventType.STEP_CANCELLED):
                            # 실패 뒤 generator를 더 실행하지 않는다. raw Diagnostic code를
                            # 승격하지 않고 Engine이 명시적으로 전달한 classified code만 보존한다.
                            raise PipelineError(event.error or "Pipeline stage did not complete", code=event.failure_code)
                finally:
                    close = getattr(events, "aclose", None)
                    if close is not None:
                        await close()
                if active:
                    raise PipelineError("Pipeline stage left unfinished Steps")
                # 결과를 발행하지 않는 준비 단계도 종료 사실을 명시한다.
                completed = BaseEngine.step_completed_event(context, stage_id, stage_output or EngineOutput())
            except Exception as error:
                yield BaseEngine.step_failed_event(stage_id, error)
                raise
            yield completed
            final_output = completed.output
        yield BaseEngine.output_event(context, final_output)
