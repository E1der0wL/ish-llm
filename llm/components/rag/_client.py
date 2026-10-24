"""재사용 가능한 모델 호출의 공통 클라이언트 처리. Engine/Run/Step의 수명 주기와 분리하여 다른 기능에서 사용할 수 있다.

Shared request mechanics; no scheduler, lifecycle events or domain writes."""

from llm.core.model_protocol import ModelOperation, ModelRequest, ModelResponse, copy_model

from copy import copy

from llm.providers.parameters import merge_params


class ModelClient:
    observes_model_calls = True
    operation = ModelOperation.COMPLETION
    ABSENT = None
    RUNTIME_OPTIONS = False
    UNSUPPORTED_CALLBACK_FIELDS = frozenset(("completion_fn", "embedding_fn", "rerank_fn"))
    PARAMETERS_ATTRIBUTE = "params"
    INSTANCE_EMBED_METHOD = "embed"
    REQUIRE_ADAPTER = "Model clients require an operation adapter"
    INVALID_RETRY_SELECTOR = "Retry selector must be callable"
    INVALID_COMPLETION_RESULT = "Operation adapter must return ModelResponse"

    def __init__(self, params: dict, *, adapter=ABSENT) -> None:
        if self.UNSUPPORTED_CALLBACK_FIELDS.intersection(params):
            raise TypeError(self.REQUIRE_ADAPTER)
        from llm.providers.operations import LiteLLMOperations
        self.adapter = LiteLLMOperations() if adapter is self.ABSENT else adapter
        self.params = copy_model(params)
        self.adapter.validate_options(self.params, operation=self.operation, json_contract=self.RUNTIME_OPTIONS)
        self.provider_options = {}
        self._retry_decider = self.ABSENT

    @classmethod
    def fingerprint_parameters(cls, client):
        """Optional custom-client parameters participate without filling its definition."""
        return getattr(client, cls.PARAMETERS_ATTRIBUTE, {})

    def describe_config(self):
        return self.adapter.describe_config(self.operation)

    def validate_config(self, params):
        self.adapter.validate_options(params, operation=self.operation)

    def cache_identity(self):
        """프로세스 내 호출 구현의 정체성. 영속 fingerprint와 별도로 사용한다."""
        return (self.adapter.cache_identity(), id(self.__dict__.get(self.INSTANCE_EMBED_METHOD, type(self))))

    def with_provider(self, options):
        worker = copy(self)
        worker.provider_options = dict(options)
        return worker

    def with_config(self, params):
        """호스트 함수를 유지한 호출별 사본. 프로젝트에서 전달한 인자를 클라이언트 기본값 위에 적용한다."""
        worker = copy(self)
        worker.params = merge_params(self.params, params)
        worker.validate_config(params)
        return worker

    def with_retry(self, decide):
        """Application 선택기. 코퍼스 identity/input은 고정하고 transport timeout만 수정 가능하다."""
        if not callable(decide):
            raise TypeError(self.INVALID_RETRY_SELECTOR)
        worker = copy(self)
        worker._retry_decider = decide
        return worker

    async def _complete(self, messages, *, output_format=ABSENT, deadline=ABSENT):
        result = await self.adapter.complete(ModelRequest(messages, self.params), self.provider_options,
            output_format=output_format, deadline=deadline, decide=self._retry_decider)
        if not isinstance(result, ModelResponse):
            raise TypeError(self.INVALID_COMPLETION_RESULT)
        return result
