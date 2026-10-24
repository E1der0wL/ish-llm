"""Model inference and explicit repair selection for RAG graph extraction."""

from llm.providers.codes import ProviderErrorCode
import json
from copy import copy, deepcopy
from llm.components.rag._client import ModelClient
from llm.core.model_protocol import ModelMessage, ModelRole
from .prompts import EXTRACTION_CONTRACT, ExtractionJSONMode, ExtractionOptions
from .chunk_records import SplitDocument
from llm.providers.operations import CompletionFormat
from llm.providers.diagnostic_values import ProviderRetryObservation
from llm.policies.retry import RetryPolicy
from llm.components.prompts.records import PromptDefinition
from .graph_records import validate_graph, GraphValidationError


class GraphRepairRequest:
    INSTRUCTION = "Correct the previous JSON to satisfy the extraction contract. Return the full corrected JSON."

    class Field:
        INSTRUCTION = "instruction"
        VALIDATION_ERROR = "validation_error"
        PREVIOUS_JSON = "previous_json"
        CHUNKS = SplitDocument.Field.CHUNKS

    @classmethod
    def create(cls, error, content, chunks):
        return {cls.Field.INSTRUCTION: cls.INSTRUCTION, cls.Field.VALIDATION_ERROR: str(error),
                cls.Field.PREVIOUS_JSON: content, cls.Field.CHUNKS: chunks}


class TripleExtractor(ModelClient):
    """선택한 Operation Adapter의 공통 응답에서 트리플을 추출한다."""

    PREFERRED_TYPES_PREFIX = "\nPreferred relation types: "
    ASCII_ONLY = False
    UNPARSED_CONTENT = None
    LAST_MESSAGE = -1
    TOOLS_FORBIDDEN = "Graph extraction cannot request Tools"
    INVALID_RESULT = "RAG extraction invalid after {attempt} repair attempts: {error}"

    def __init__(self, *, adapter=ModelClient.ABSENT, **params):
        super().__init__(params, adapter=adapter)
        self.extraction = {}
        self.prompt = PromptDefinition.create(())

    def with_extraction(self, options: dict, prompt: dict):
        """호출별 정책/프롬프트 사본. 등록 클라이언트나 다른 Project는 변경하지 않는다."""
        from jsonschema import Draft202012Validator
        from .prompts import extraction_schema
        Draft202012Validator(extraction_schema()).validate(options)
        worker = copy(self)
        worker.extraction, worker.prompt = deepcopy(options), deepcopy(prompt)
        return worker

    async def extract(self, chunks: list) -> dict:
        """검증 오류만 제한 횟수 수정한다. 연결 오류·취소·사용량 제한은 그대로 전달한다."""
        messages = [ModelMessage(ModelRole.SYSTEM, EXTRACTION_CONTRACT + self.PREFERRED_TYPES_PREFIX
                     + json.dumps(self.extraction.get(ExtractionOptions.Field.RELATION_TYPES, []), ensure_ascii=self.ASCII_ONLY)),
                    *PromptDefinition.model_messages(self.prompt),
                    ModelMessage(ModelRole.USER, json.dumps(chunks, ensure_ascii=self.ASCII_ONLY))]
        from llm.providers.requests import error_code
        mode = self.extraction.get(ExtractionOptions.Field.JSON_MODE)
        from llm.providers.options import ProviderOptions
        wall_timeout = ProviderOptions(self.provider_options).wall_timeout
        repair = RetryPolicy(self.extraction.get(ExtractionOptions.Field.REPAIR_ATTEMPTS, ExtractionOptions.NO_REPAIRS) + RetryPolicy.INITIAL_ATTEMPTS,
                             deadline=RetryPolicy.end(wall_timeout))
        deadline = repair.deadline
        for attempt in range(repair.max_attempts):
            try:
                response = await self._complete(messages, deadline=deadline,
                    output_format=CompletionFormat.JSON if mode in (ExtractionJSONMode.STRICT.value, ExtractionJSONMode.AUTO.value)
                    else CompletionFormat.TEXT if mode == ExtractionJSONMode.OFF.value else ModelClient.ABSENT)
            except Exception as error:
                if mode != ExtractionJSONMode.AUTO.value or error_code(error) not in (ProviderErrorCode.EMPTY_RESPONSE.value, ProviderErrorCode.INVALID_RESPONSE.value):
                    raise
                mode = ExtractionJSONMode.OFF.value
                ProviderRetryObservation.json_mode_fallback()
                response = await self._complete(messages, deadline=deadline, output_format=CompletionFormat.TEXT)
            content = self.UNPARSED_CONTENT
            try:
                if response.message.calls:
                    raise ValueError(self.TOOLS_FORBIDDEN)
                content = response.message.text
                graph = json.loads(content)
                validate_graph(graph, chunks)
                return graph
            except (ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
                if not repair.allows(attempt + RetryPolicy.INITIAL_ATTEMPTS):
                    raise GraphValidationError(self.INVALID_RESULT.format(attempt=attempt, error=error)) from error
                # 이전 응답을 assistant 지침으로 승격하지 않는다. 고정 크기의 최신 오류만 전달한다.
                messages[self.LAST_MESSAGE] = ModelMessage(ModelRole.USER, json.dumps(GraphRepairRequest.create(error, content, chunks), ensure_ascii=self.ASCII_ONLY))
