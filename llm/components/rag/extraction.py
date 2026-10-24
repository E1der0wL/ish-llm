"""Model inference and explicit repair selection for RAG graph extraction."""

from llm.providers.codes import ProviderErrorCode
import json
from copy import copy, deepcopy
from llm.components.rag._client import ModelClient
from llm.core.model_protocol import ModelMessage, ModelRole
from .prompts import EXTRACTION_CONTRACT, ExtractionJSONMode
from llm.components.prompts.records import PromptDefinition
from .graph_records import validate_graph, GraphValidationError


class TripleExtractor(ModelClient):
    """선택한 Operation Adapter의 공통 응답에서 트리플을 추출한다."""

    def __init__(self, *, adapter=None, **params):
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
        messages = [ModelMessage(ModelRole.SYSTEM, EXTRACTION_CONTRACT + "\nPreferred relation types: "
                     + json.dumps(self.extraction.get("relation_types", []), ensure_ascii=False)),
                    *PromptDefinition.model_messages(self.prompt),
                    ModelMessage(ModelRole.USER, json.dumps(chunks, ensure_ascii=False))]
        from llm.providers.requests import error_code
        from llm.providers.runtime import diagnostic
        mode = self.extraction.get("json_mode")
        from llm.providers.options import ProviderOptions
        wall_timeout = ProviderOptions(self.provider_options).wall_timeout
        from llm.policies.retry import RetryPolicy
        repair = RetryPolicy(self.extraction.get("repair_attempts", 0) + 1,
                             deadline=RetryPolicy.end(wall_timeout))
        deadline = repair.deadline
        for attempt in range(repair.max_attempts):
            try:
                response = await self._complete(messages, deadline=deadline,
                    output_format="json" if mode in (ExtractionJSONMode.STRICT.value, ExtractionJSONMode.AUTO.value)
                    else "text" if mode == ExtractionJSONMode.OFF.value else None)
            except Exception as error:
                if mode != ExtractionJSONMode.AUTO.value or error_code(error) not in (ProviderErrorCode.EMPTY_RESPONSE.value, ProviderErrorCode.INVALID_RESPONSE.value):
                    raise
                mode = ExtractionJSONMode.OFF.value
                diagnostic("provider_json_mode_fallback", operation="acompletion")
                response = await self._complete(messages, deadline=deadline, output_format="text")
            content = None
            try:
                if response.message.calls:
                    raise ValueError("Graph extraction cannot request Tools")
                content = response.message.text
                graph = json.loads(content)
                validate_graph(graph, chunks)
                return graph
            except (ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
                if not repair.allows(attempt + 1):
                    raise GraphValidationError(f"RAG extraction invalid after {attempt} repair attempts: {error}") from error
                # 이전 응답을 assistant 지침으로 승격하지 않는다. 고정 크기의 최신 오류만 전달한다.
                messages[-1] = ModelMessage(ModelRole.USER, json.dumps({
                    "instruction": "Correct the previous JSON to satisfy the extraction contract. Return the full corrected JSON.",
                    "validation_error": str(error), "previous_json": content, "chunks": chunks,
                }, ensure_ascii=False))
