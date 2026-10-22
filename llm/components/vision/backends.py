"""교체 가능한 OCR 구현과 LiteLLM 이미지 해석. 저장/Tool 승인/Run 수명을 소유하지 않는다."""

from llm.core.model_protocol import ContentKind, ContentPart, ModelMessage, ModelOperation, ModelRequest, ModelResponse, ModelRole, copy_model

import asyncio
import base64
import csv
from io import StringIO
import math
from typing import Protocol

from llm.errors import CodedError
from llm.services.infrastructure.processes import kill_process_tree
from llm.services.infrastructure.storage import drain_on_cancel


class VisionError(CodedError, ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class OCRBackend(Protocol):
    revision: str
    def describe_config(self) -> dict: ...
    async def recognize(self, image: bytes, *, options: dict) -> dict: ...


def validate_ocr(result: dict, width: int, height: int) -> dict:
    """backend confidence는 그대로 보존하며 backend 간 비교 점수로 변환하지 않는다."""
    from llm.core.models import ProjectConfig
    ProjectConfig.validate_json(result)
    if not isinstance(result, dict) or not isinstance(result.get("text"), str) or not isinstance(result.get("blocks"), list):
        raise VisionError("vision_invalid_response", "OCR requires text and blocks")
    for block in result["blocks"]:
        box = block.get("box") if isinstance(block, dict) else None
        if (not isinstance(block, dict) or not isinstance(block.get("text"), str)
                or not isinstance(box, list) or len(box) != 4
                or any(type(n) not in (int, float) or not math.isfinite(n) for n in box)
                or not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)):
            raise VisionError("vision_invalid_response", "OCR block requires an in-image pixel box")
        score = block.get("confidence")
        if score is not None and (type(score) not in (int, float) or not math.isfinite(score)):
            raise VisionError("vision_invalid_response", "Invalid OCR confidence")
    return result


class TesseractBackend:
    """PATH의 tesseract 실행 파일을 사용한다. 설치/언어 다운로드/다른 backend 전환은 하지 않는다."""
    revision = "tesseract-tsv-v1"

    def __init__(self, *, executable: str = "tesseract"):
        if not isinstance(executable, str) or not executable:
            raise ValueError("Tesseract executable is required")
        self.executable = executable
        self.revision = self.revision + ":" + executable

    def describe_config(self) -> dict:
        return {"type": "object", "additionalProperties": False, "properties": {
            "language": {"type": "string", "minLength": 1},
            "page_segmentation": {"type": "integer", "minimum": 0, "maximum": 13},
            "variables": {"type": "object", "additionalProperties": {"type": "string"}},
        }}

    async def recognize(self, image: bytes, *, options: dict) -> dict:
        from jsonschema import Draft202012Validator
        Draft202012Validator(self.describe_config()).validate(options)
        argv = [self.executable, "stdin", "stdout"]
        if "language" in options:
            argv += ["-l", options["language"]]
        if "page_segmentation" in options:
            argv += ["--psm", str(options["page_segmentation"])]
        for key, value in options.get("variables", {}).items():
            if not key or "=" in key or any(c.isspace() for c in key):
                raise ValueError("Invalid Tesseract variable name")
            argv += ["-c", key + "=" + value]
        # TSV는 좌표/텍스트 계약이며 사용자 OCR 품질 기본값이 아니다.
        argv.append("tsv")
        pending = asyncio.create_task(asyncio.create_subprocess_exec(*argv,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True))

        async def cleanup(process, exchange=None):
            await asyncio.gather(kill_process_tree(process),
                exchange if exchange is not None else process.communicate(), return_exceptions=False)

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
            raise VisionError("vision_backend_unavailable", "Tesseract executable is not installed") from error
        exchange = asyncio.create_task(process.communicate(image))
        try:
            stdout, stderr = await asyncio.shield(exchange)
        except BaseException:
            await drain_on_cancel(cleanup(process, exchange))
            raise
        if process.returncode:
            raise VisionError("vision_ocr_failed", f"Tesseract exited with code {process.returncode}")
        try:
            rows = csv.DictReader(StringIO(stdout.decode("utf-8")), delimiter="\t", quoting=csv.QUOTE_NONE)
            if not {"level", "page_num", "block_num", "par_num", "line_num", "left", "top", "width", "height", "conf", "text"}.issubset(rows.fieldnames or ()):
                raise ValueError("TSV header is missing")
            blocks, lines = [], {}
            for row in rows:
                if int(row["level"]) != 5 or not row["text"].strip():
                    continue
                left, top, width, height = (int(row[key]) for key in ("left", "top", "width", "height"))
                blocks.append({"text": row["text"], "box": [left, top, left + width, top + height],
                               "confidence": float(row["conf"])})
                key = tuple(row[k] for k in ("page_num", "block_num", "par_num", "line_num"))
                lines.setdefault(key, []).append(row["text"])
            return {"text": "\n".join(" ".join(words) for words in lines.values()), "blocks": blocks,
                    "confidence_kind": "tesseract_word_confidence_0_100"}
        except (ValueError, KeyError, TypeError, UnicodeError) as error:
            raise VisionError("vision_invalid_response", "Invalid Tesseract TSV response") from error


def image_part(raw: bytes, mime_type: str) -> ContentPart:
    """Encode only for invocation; never put the full image into stored Tool results."""
    return ContentPart(ContentKind.IMAGE,
        "data:" + mime_type + ";base64," + base64.b64encode(raw).decode("ascii"), mime_type)


class VisionModel:
    observes_model_calls = True

    def __init__(self, *, adapter=None, retry_decider=None):
        if retry_decider is not None and not callable(retry_decider):
            raise TypeError("Retry selector must be callable")
        from llm.providers.operations import LiteLLMOperations
        self.adapter = LiteLLMOperations() if adapter is None else adapter
        self.retry_decider = retry_decider

    async def analyze(self, raw: bytes, mime_type: str, prompt: str, *, parameters: dict, provider: dict) -> dict:
        parameters = copy_model(parameters)
        self.adapter.validate_vision(parameters, require_model=True)
        self.adapter.validate_options(parameters, operation=ModelOperation.COMPLETION, json_contract=False)
        response = await self.adapter.complete(ModelRequest([ModelMessage(ModelRole.USER,
            (ContentPart(ContentKind.TEXT, prompt), image_part(raw, mime_type)))], parameters),
            provider, decide=self.retry_decider)
        if not isinstance(response, ModelResponse):
            raise TypeError("Vision adapter must return ModelResponse")
        if response.message.calls:
            raise ValueError("Vision analysis cannot request Tools")
        return {"text": response.message.text, "model": response.model,
                "finish_reason": response.raw_finish_reason}
