"""UI/Tool/RAG가 공유하는 이미지 공개 API. 준비는 잠금 밖, 등록 확정은 공통 저장 경계 안에서 한다."""

import asyncio
from copy import copy, deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

from llm.core.configuration import UNSET
from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.providers.observations import observe_component_models
from . import images
from .backends import validate_ocr, image_part
from llm.core.model_protocol import ContentPart
from .records import VisionImageRecord, ImageOrigin
from .results import VisionResult, VisionExtraction


class VisionData(ComponentData):
    ABSENT = None
    BINDING_ATTRIBUTE = "_execution_binding"
    BINDING_CHANGED = "Vision configuration/backend changed; start a new Run"
    CONFIG_CHANGED = "Vision configuration changed during processing"
    IMAGE_CHANGED = "Image changed during processing"

    def _current(self):
        project, component = super()._current()
        expected = getattr(self, self.BINDING_ATTRIBUTE, self.ABSENT)
        if expected is not self.ABSENT and component.binding(project) != expected:
            raise ValueError(self.BINDING_CHANGED)
        return project, component

    @workspace_locked
    def _snapshot(self, image_id=ABSENT):
        project, component = self._current()
        config = component.read_parameters(project)
        record, raw = (
            (self.ABSENT, self.ABSENT) if image_id is self.ABSENT else component.read_image(project, image_id)
        )
        return component, config, self.ABSENT if record is self.ABSENT else VisionImageRecord(record), raw

    @workspace_locked
    def _finish(self, config, record):
        project, component = self._current()
        if config.changed(component.read_parameters(project)):
            raise ValueError(self.CONFIG_CHANGED)
        if (
            record is not self.ABSENT
            and VisionImageRecord(component.load(project, record.identifier)).sha256 != record.sha256
        ):
            raise ValueError(self.IMAGE_CHANGED)

    @workspace_locked
    def _commit(
        self, config, raw, info, *, title, metadata, identifier=ABSENT, source=ABSENT, operations=ABSENT
    ):
        self._finish(config, source)
        project, component = self._current()
        origin = self.ABSENT if source is self.ABSENT else ImageOrigin.create(source, operations)
        return component.import_bytes(
            project, raw, info, title=title, metadata=metadata, identifier=identifier, origin=origin
        )

    @staticmethod
    def _reference(record):
        return record.reference()

    # 공개 API. 모델 호출은 비동기 전용이며 새 Run을 만들지 않는다.
    def bound(self, binding):
        """Run의 설정 지문을 고정한다. 객체와 설정 저장소를 새로 생성하지 않는다."""
        handle = copy(self)
        handle._execution_binding = binding
        return handle

    def import_image(
        self,
        source: str | Path | bytes,
        *,
        title: str,
        metadata: dict | None = ABSENT,
        identifier: str | None = ABSENT,
    ) -> dict:
        component, config, _, _ = self._snapshot()
        limits = config.limits
        raw = images.read_source(source, limits)
        info = images.inspect_image(raw, limits)
        return self._commit(
            config,
            raw,
            info,
            title=title,
            metadata={} if metadata is self.ABSENT else metadata,
            identifier=identifier,
        )

    async def aimport_image(
        self,
        source: str | Path | bytes,
        *,
        title: str,
        metadata: dict | None = ABSENT,
        identifier: str | None = ABSENT,
    ) -> dict:
        _, config, _, _ = await self._async_call(self._snapshot)
        limits = config.limits
        raw = await asyncio.to_thread(images.read_source, source, limits)
        info = await asyncio.to_thread(images.inspect_image, raw, limits)
        return await self._async_call(
            self._commit,
            config,
            raw,
            info,
            title=title,
            metadata={} if metadata is self.ABSENT else metadata,
            identifier=identifier,
        )

    async def apreprocess(self, image_id: str, *, operations: list[dict], title: str | None = ABSENT) -> dict:
        _, config, source, raw = await self._async_call(self._snapshot, image_id)
        operations = deepcopy(operations)
        value, info = await asyncio.to_thread(images.preprocess, raw, operations, config.limits)
        return await self._async_call(
            self._commit,
            config,
            value,
            info,
            title=source.title if title is self.ABSENT else title,
            metadata=source.metadata,
            source=source,
            operations=operations,
        )

    async def aocr(self, image_id: str, *, backend=UNSET, options: dict | None = ABSENT) -> dict:
        component, config, record, raw = await self._async_call(self._snapshot, image_id)
        ocr_config = config.ocr
        backend = ocr_config.selected(backend, component.ocr_backends)
        implementation = component.ocr_backends[backend]
        parameters = ocr_config.parameters(backend, options)
        Draft202012Validator(implementation.describe_config()).validate(parameters)
        component.serialize(parameters)
        await asyncio.to_thread(images.inspect_image, raw, config.limits)
        async with asyncio.timeout(ocr_config.timeout):
            result = await implementation.recognize(raw, options=deepcopy(parameters))
        result = deepcopy(validate_ocr(result, record.width, record.height))
        await self._async_call(self._finish, config, record)
        return VisionResult.ocr(result, self._reference(record), backend, implementation.revision, parameters)

    @observe_component_models
    async def aanalyze(self, image_id: str, *, prompt: str) -> dict:
        VisionResult.validate_prompt(prompt)
        component, config, record, raw = await self._async_call(self._snapshot, image_id)
        await self._async_call(self.require_model_observation, component.model)
        await asyncio.to_thread(images.inspect_image, raw, config.limits)
        result = await component.model.analyze(
            raw, record.mime_type, prompt, parameters=config.completion, provider=config.provider
        )
        await self._async_call(self._finish, config, record)
        return VisionResult.analysis(result, self._reference(record))

    async def acompletion_content(self, image_id: str) -> ContentPart:
        """이미지 지원 Completion용 content block. 반환 Base64를 영속 메시지/로그에 저장하지 않는다."""
        _, config, record, raw = await self._async_call(self._snapshot, image_id)
        await asyncio.to_thread(images.inspect_image, raw, config.limits)
        block = await asyncio.to_thread(image_part, raw, record.mime_type)
        await self._async_call(self._finish, config, record)
        return block

    async def aextract_document(
        self, image_id: str, *, mode: str, title: str, backend=UNSET, prompt=UNSET
    ) -> dict:
        """RAG.add_document에 전달할 자료를 만든다. 원본 픽셀과 OCR/모델 생성 문서를 구분한다."""
        VisionExtraction.validate_title(title)
        if mode == VisionExtraction.OCR:
            if prompt is not UNSET:
                raise ValueError(VisionExtraction.OCR_PROMPT)
            result = await self.aocr(image_id, backend=backend)
        elif mode == VisionExtraction.ANALYSIS:
            if backend is not UNSET or prompt is UNSET:
                raise ValueError(VisionExtraction.ANALYSIS_OPTIONS)
            result = await self.aanalyze(image_id, prompt=prompt)
        else:
            raise ValueError(VisionExtraction.INVALID_MODE)
        result = VisionResult(result)
        if not result.text.strip():
            raise ValueError(VisionExtraction.EMPTY_TEXT)
        return VisionExtraction.document(title, result, lambda: self.project.id)
