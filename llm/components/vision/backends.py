"""교체 가능한 OCR 구현과 LiteLLM 이미지 해석. 저장/Tool 승인/Run 수명을 소유하지 않는다."""

from llm.core.model_protocol import (
    ContentKind,
    ContentPart,
    ModelMessage,
    ModelOperation,
    ModelRequest,
    ModelResponse,
    ModelRole,
    copy_model,
)

import asyncio
from typing import Protocol

from .ocr_contracts import (
    VisionError,
    VisionErrorCode,
    OCRResult,
    TesseractOptions,
    TesseractWire,
    InlineImage,
    VisionAnalysis,
)
from llm.services.infrastructure.processes import kill_process_tree
from llm.services.infrastructure.storage import drain_on_cancel


class OCRBackend(Protocol):
    revision: str

    def describe_config(self) -> dict: ...
    async def recognize(self, image: bytes, *, options: dict) -> dict: ...


# Public validation uses the sole OCR result Reader.
validate_ocr = OCRResult.validate


class TesseractBackend:
    """PATH의 tesseract 실행 파일을 사용한다. 설치/언어 다운로드/다른 backend 전환은 하지 않는다."""

    revision = "tesseract-tsv-v1"
    NAME = "tesseract"
    DEFAULT_EXECUTABLE = NAME
    REVISION_SEPARATOR = ":"
    ABSENT = None
    OWN_PROCESS_SESSION = True
    COLLECT_CLEANUP_ERRORS = False
    REQUIRED_EXECUTABLE = "Tesseract executable is required"
    MISSING_EXECUTABLE = "Tesseract executable is not installed"
    EXIT_FAILURE = "Tesseract exited with code {code}"

    def __init__(self, *, executable: str = DEFAULT_EXECUTABLE):
        if not isinstance(executable, str) or not executable:
            raise ValueError(self.REQUIRED_EXECUTABLE)
        self.executable = executable
        self.revision = self.revision + self.REVISION_SEPARATOR + executable

    def describe_config(self) -> dict:
        return TesseractOptions.schema()

    async def recognize(self, image: bytes, *, options: dict) -> dict:
        from jsonschema import Draft202012Validator

        Draft202012Validator(self.describe_config()).validate(options)
        argv = TesseractWire.arguments(self.executable, options)
        pending = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=TesseractBackend.OWN_PROCESS_SESSION,
            )
        )

        async def cleanup(process, exchange=TesseractBackend.ABSENT):
            await asyncio.gather(
                kill_process_tree(process),
                exchange if exchange is not TesseractBackend.ABSENT else process.communicate(),
                return_exceptions=TesseractBackend.COLLECT_CLEANUP_ERRORS,
            )

        try:
            process = await asyncio.shield(pending)
        except asyncio.CancelledError:

            async def finish_spawn():
                try:
                    process = await pending
                except OSError:
                    return  # 생성 실패에는 회수할 child가 없다. 원래 취소를 보존한다.
                await cleanup(process)

            # 두 번째 취소도 생성 중인 child의 핸들을 잃게 하지 않는다.
            await drain_on_cancel(finish_spawn())
            raise
        except FileNotFoundError as error:
            raise VisionError(
                VisionErrorCode.BACKEND_UNAVAILABLE, TesseractBackend.MISSING_EXECUTABLE
            ) from error
        exchange = asyncio.create_task(process.communicate(image))
        try:
            stdout, stderr = await asyncio.shield(exchange)
        except BaseException:
            await drain_on_cancel(cleanup(process, exchange))
            raise
        if process.returncode:
            raise VisionError(
                VisionErrorCode.OCR_FAILED, TesseractBackend.EXIT_FAILURE.format(code=process.returncode)
            )
        return TesseractWire.read(stdout)


# This is generic inline-image encoding for ContentPart, before Provider wire encoding.
image_part = InlineImage.part


class VisionModel:
    observes_model_calls = True
    ABSENT = None
    REQUIRE_MODEL = True
    OPTIONS_ARE_JSON = False
    INVALID_RETRY_SELECTOR = "Retry selector must be callable"
    INVALID_RESPONSE = "Vision adapter must return ModelResponse"
    UNEXPECTED_TOOLS = "Vision analysis cannot request Tools"

    def __init__(self, *, adapter=ABSENT, retry_decider=ABSENT):
        if retry_decider is not self.ABSENT and not callable(retry_decider):
            raise TypeError(self.INVALID_RETRY_SELECTOR)
        from llm.providers.operations import LiteLLMOperations

        self.adapter = LiteLLMOperations() if adapter is self.ABSENT else adapter
        self.retry_decider = retry_decider

    async def analyze(
        self, raw: bytes, mime_type: str, prompt: str, *, parameters: dict, provider: dict
    ) -> dict:
        parameters = copy_model(parameters)
        self.adapter.validate_vision(parameters, require_model=self.REQUIRE_MODEL)
        self.adapter.validate_options(
            parameters, operation=ModelOperation.COMPLETION, json_contract=self.OPTIONS_ARE_JSON
        )
        response = await self.adapter.complete(
            ModelRequest(
                [
                    ModelMessage(
                        ModelRole.USER, (ContentPart(ContentKind.TEXT, prompt), image_part(raw, mime_type))
                    )
                ],
                parameters,
            ),
            provider,
            decide=self.retry_decider,
        )
        if not isinstance(response, ModelResponse):
            raise TypeError(self.INVALID_RESPONSE)
        if response.message.calls:
            raise ValueError(self.UNEXPECTED_TOOLS)
        return VisionAnalysis.from_response(response).to_dict()
