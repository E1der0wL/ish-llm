"""UI/Tool/RAG가 공유하는 이미지 공개 API. 준비는 잠금 밖, 등록 확정은 공통 저장 경계 안에서 한다."""

import asyncio
from copy import copy, deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

from llm.core.configuration import UNSET, required_setting
from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import revision_token
from llm.providers.observations import observe_component_models
from . import images
from .backends import validate_ocr, image_content


class VisionData(ComponentData):
    def _current(self):
        project, component = super()._current()
        expected = getattr(self, "_execution_binding", None)
        if expected is not None and component.binding(project) != expected:
            raise ValueError("Vision configuration/backend changed; start a new Run")
        return project, component

    @workspace_locked
    def _snapshot(self, image_id=None):
        project, component = self._current()
        config = component._options(project)
        record, raw = (None, None) if image_id is None else component.read_image(project, image_id)
        return component, config, record, raw

    @workspace_locked
    def _finish(self, config, record):
        project, component = self._current()
        if component._options(project) != config:
            raise ValueError("Vision configuration changed during processing")
        if record is not None and component.load(project, record["id"])["sha256"] != record["sha256"]:
            raise ValueError("Image changed during processing")

    @workspace_locked
    def _commit(self, config, raw, info, *, title, metadata, identifier=None, source=None, operations=None):
        self._finish(config, source)
        project, component = self._current()
        origin = None if source is None else {"image_id": source["id"], "sha256": source["sha256"],
            "operations": deepcopy(operations), "coordinate_contract": "sequential-input-pixels-ltrb"}
        return component.import_bytes(project, raw, info, title=title, metadata=metadata,
                                      identifier=identifier, origin=origin)

    @staticmethod
    def _reference(record):
        return {key: deepcopy(record[key]) for key in ("id", "sha256", "mime_type", "width", "height", "origin") if key in record}

    # 공개 API. 모델 호출은 비동기 전용이며 새 Run을 만들지 않는다.
    def bound(self, binding):
        """Run의 설정 지문을 고정한다. 객체와 설정 저장소를 새로 생성하지 않는다."""
        handle = copy(self)
        handle._execution_binding = binding
        return handle

    def import_image(self, source: str | Path | bytes, *, title: str, metadata: dict | None = None, identifier: str | None = None) -> dict:
        component, config, _, _ = self._snapshot()
        limits = config.get("limits", {})
        raw = images.read_source(source, limits)
        info = images.inspect_image(raw, limits)
        return self._commit(config, raw, info, title=title, metadata={} if metadata is None else metadata, identifier=identifier)

    async def aimport_image(self, source: str | Path | bytes, *, title: str, metadata: dict | None = None, identifier: str | None = None) -> dict:
        _, config, _, _ = await self._async_call(self._snapshot)
        limits = config.get("limits", {})
        raw = await asyncio.to_thread(images.read_source, source, limits)
        info = await asyncio.to_thread(images.inspect_image, raw, limits)
        return await self._async_call(self._commit, config, raw, info, title=title,
                                     metadata={} if metadata is None else metadata, identifier=identifier)

    async def apreprocess(self, image_id: str, *, operations: list[dict], title: str | None = None) -> dict:
        _, config, source, raw = await self._async_call(self._snapshot, image_id)
        operations = deepcopy(operations)
        value, info = await asyncio.to_thread(images.preprocess, raw, operations, config.get("limits", {}))
        return await self._async_call(self._commit, config, value, info,
            title=source["title"] if title is None else title, metadata=source["metadata"],
            source=source, operations=operations)

    async def aocr(self, image_id: str, *, backend=UNSET, options: dict | None = None) -> dict:
        component, config, record, raw = await self._async_call(self._snapshot, image_id)
        settings = config.get("ocr", {})
        if backend is UNSET:
            backend = required_setting(settings, "backend", scope="vision.ocr")
        if not isinstance(backend, str) or backend not in component.ocr_backends:
            raise ValueError("Select a registered OCR backend; auto is unsupported")
        implementation = component.ocr_backends[backend]
        parameters = {**settings.get("backends", {}).get(backend, {}), **({} if options is None else options)}
        Draft202012Validator(implementation.configuration_schema()).validate(parameters)
        component.serialize(parameters)
        await asyncio.to_thread(images.inspect_image, raw, config.get("limits", {}))
        async with asyncio.timeout(settings.get("timeout")):
            result = await implementation.recognize(raw, options=deepcopy(parameters))
        result = deepcopy(validate_ocr(result, record["width"], record["height"]))
        await self._async_call(self._finish, config, record)
        return {**result, "kind": "ocr", "image": self._reference(record), "backend": backend,
                "backend_revision": implementation.revision, "options": parameters,
                "coordinate_space": "image_pixels_top_left_ltrb"}

    @observe_component_models
    async def aanalyze(self, image_id: str, *, prompt: str) -> dict:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Image analysis prompt is required")
        component, config, record, raw = await self._async_call(self._snapshot, image_id)
        await self._async_call(self.require_model_observation, component.model)
        await asyncio.to_thread(images.inspect_image, raw, config.get("limits", {}))
        result = await component.model.analyze(raw, record["mime_type"], prompt,
            parameters=config.get("completion", {}), provider=config.get("provider", {}))
        await self._async_call(self._finish, config, record)
        return {**result, "kind": "model_analysis", "image": self._reference(record)}

    async def acompletion_content(self, image_id: str) -> dict:
        """이미지 지원 Completion용 content block. 반환 Base64를 영속 메시지/로그에 저장하지 않는다."""
        _, config, record, raw = await self._async_call(self._snapshot, image_id)
        await asyncio.to_thread(images.inspect_image, raw, config.get("limits", {}))
        block = await asyncio.to_thread(image_content, raw, record["mime_type"])
        await self._async_call(self._finish, config, record)
        return block

    async def aextract_document(self, image_id: str, *, mode: str, title: str, backend=UNSET, prompt=UNSET) -> dict:
        """RAG.add_document에 전달할 자료를 만든다. 원본 픽셀과 OCR/모델 생성 문서를 구분한다."""
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Extracted document title is required")
        if mode == "ocr":
            if prompt is not UNSET:
                raise ValueError("OCR does not accept a VLM prompt")
            result = await self.aocr(image_id, backend=backend)
        elif mode == "analysis":
            if backend is not UNSET or prompt is UNSET:
                raise ValueError("Analysis requires prompt and does not select an OCR backend")
            result = await self.aanalyze(image_id, prompt=prompt)
        else:
            raise ValueError("Select document extraction mode: ocr or analysis")
        if not result["text"].strip():
            raise ValueError("Image extraction produced no document text")
        return {"title": title, "content": result["text"], "metadata": {"vision": {
            **{k: v for k, v in result.items() if k != "text"},
            "text_sha256": revision_token({"text": result["text"]}),
            "source_kind": "derived_text", "project_id": self.project.id}}}
