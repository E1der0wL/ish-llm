"""재사용 가능한 모델 호출의 공통 클라이언트 처리. Engine/Run/Step의 수명 주기와 분리하여 다른 기능에서 사용할 수 있다.

Shared request mechanics; no scheduler, lifecycle events or domain writes."""

from copy import copy

from llm.providers.parameters import copy_params, merge_params


class ModelClient:
    observes_model_calls = True
    operation = "acompletion"

    def __init__(self, operation: str, params: dict, *, adapter=None) -> None:
        if set(params) & {'completion_fn', 'embedding_fn', 'rerank_fn'}:
            raise TypeError('Model clients require an operation adapter')
        self._operation = operation
        from llm.providers.operations import LiteLLMOperations
        self.adapter = adapter if adapter is not None else LiteLLMOperations()
        self.params = copy_params(params)
        self.adapter.validate_options(self.params, operation=operation, json_contract=False)
        self.provider_options = {}
        self._retry_decider = None

    @classmethod
    def describe_config(cls):
        from llm.providers.schema import model_schema
        return model_schema(cls.operation)

    @classmethod
    def validate_config(cls, params):
        from llm.providers.schema import validate_model_params
        validate_model_params(params, operation=cls.operation)

    def operation_schema(self):
        return self.adapter.describe_config(self._operation)

    def validate_operation(self, params):
        return self.adapter.validate_options(params, operation=self._operation)

    def cache_identity(self):
        """프로세스 내 호출 구현의 정체성. 영속 fingerprint와 별도로 사용한다."""
        return (id(self.adapter), id(self.__dict__.get("embed", type(self))))

    def with_provider(self, options):
        worker = copy(self)
        worker.provider_options = dict(options)
        return worker

    def with_config(self, params):
        """호스트 함수를 유지한 호출별 사본. 프로젝트에서 전달한 인자를 클라이언트 기본값 위에 적용한다."""
        worker = copy(self)
        worker.params = merge_params(self.params, params)
        worker.validate_operation(params)
        return worker

    def with_retry(self, decide):
        """Application 선택기. 코퍼스 identity/input은 고정하고 transport timeout만 수정 가능하다."""
        if not callable(decide):
            raise TypeError("Retry selector must be callable")
        worker = copy(self)
        worker._retry_decider = decide
        return worker

    async def _complete(self, messages, *, output_format=None, deadline=None):
        from llm.core.model_protocol import ModelRequest, ModelResponse
        result = await self.adapter.complete(ModelRequest(messages, copy_params(self.params)), self.provider_options,
            output_format=output_format, deadline=deadline, decide=self._retry_decider)
        if not isinstance(result, ModelResponse):
            raise TypeError("Completion adapter must return ModelResponse")
        return result
