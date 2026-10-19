"""재사용 가능한 모델 호출의 공통 클라이언트 처리. Engine/Run/Step의 수명 주기와 분리하여 다른 기능에서 사용할 수 있다.

Shared request mechanics; no scheduler, lifecycle events or domain writes."""

from copy import copy
from collections.abc import Awaitable, Callable
from typing import Any, Optional

from llm.providers.parameters import copy_params, merge_params


class ModelClient:
    observes_model_calls = True
    operation = "acompletion"

    def __init__(self, operation: str, call_fn: Optional[Callable[..., Awaitable[Any]]],
                 params: dict[str, Any], *, adapter=None) -> None:
        if call_fn is not None and not callable(call_fn):
            raise TypeError("Model call function must be callable")
        self._operation = operation
        self._call_fn = call_fn
        if adapter is not None and call_fn is not None:
            raise ValueError('Choose an operation adapter or SDK call function')
        from llm.providers.operations import LiteLLMOperations
        self._adapter = adapter
        self.params = copy_params(params)
        self.adapter.validate_options(self.params, operation=operation, json_contract=False)
        self.provider_options = {}
        self._retry_decider = None

    @property
    def adapter(self):
        from llm.providers.operations import LiteLLMOperations
        return self._adapter if self._adapter is not None else LiteLLMOperations(self._call_fn)

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
        return (id(self._call_fn), id(self._adapter), id(self.__dict__.get("embed", type(self))))

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

    async def _invoke(self, **kwargs: Any) -> Any:
        request = copy_params(self.params)
        request.update(copy_params(kwargs))
        deadline = request.pop("_provider_deadline", None)
        return await self.adapter.invoke(self._operation, request, self.provider_options,
                                         deadline=deadline, decide=self._retry_decider)

    async def _complete(self, messages, *, output_format=None, deadline=None):
        from llm.core.model_protocol import ModelRequest
        return await self.adapter.complete(ModelRequest(messages, copy_params(self.params)), self.provider_options,
            output_format=output_format, deadline=deadline, decide=self._retry_decider)
