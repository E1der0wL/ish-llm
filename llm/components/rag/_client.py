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
                 params: dict[str, Any]) -> None:
        if call_fn is not None and not callable(call_fn):
            raise TypeError("Model call function must be callable")
        self._operation = operation
        self._call_fn = call_fn
        self.params = copy_params(params)
        from llm.providers.schema import validate_model_params
        validate_model_params(self.params, operation=operation, json_contract=False)
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

    def cache_identity(self):
        """프로세스 내 호출 구현의 정체성. 영속 fingerprint와 별도로 사용한다."""
        return (id(self._call_fn), id(self.__dict__.get("embed", type(self))))

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
            raise TypeError("Retry selector must be callable")
        worker = copy(self)
        worker._retry_decider = decide
        return worker

    async def _invoke(self, **kwargs: Any) -> Any:
        request = copy_params(self.params)
        request.update(copy_params(kwargs))
        from llm.providers.requests import request_model
        deadline = request.pop("_provider_deadline", None)
        if request.pop("_without_response_format", False):
            request.pop("response_format", None)
        return await request_model(self._operation, request, self.provider_options, call=self._call_fn,
                                   deadline=deadline, decide=self._retry_decider, changeable=("timeout",))
