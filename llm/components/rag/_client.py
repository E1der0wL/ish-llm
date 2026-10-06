"""재사용 가능한 모델 호출의 공통 클라이언트 처리. Engine/Run/Step의 수명 주기와 분리하여 다른 기능에서 사용할 수 있다.

Shared request mechanics; no scheduler, lifecycle events or domain writes."""

import asyncio
import time
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

    @classmethod
    def configuration_schema(cls):
        from llm.providers.schema import model_schema
        return model_schema(cls.operation)

    @classmethod
    def validate_configuration(cls, params):
        from llm.providers.schema import validate_model_params
        validate_model_params(params, operation=cls.operation)

    def cache_identity(self):
        """프로세스 내 호출 구현의 정체성. 영속 fingerprint와 별도로 사용한다."""
        return (id(self._call_fn), id(self.__dict__.get("embed", type(self))))

    def with_provider(self, options):
        worker = copy(self)
        worker.provider_options = dict(options)
        return worker

    def configured(self, params):
        """호스트 함수를 유지한 호출별 사본. 프로젝트에서 전달한 인자를 클라이언트 기본값 위에 적용한다."""
        worker = copy(self)
        worker.params = merge_params(self.params, params)
        worker.validate_configuration(params)
        return worker

    async def _invoke(self, **kwargs: Any) -> Any:
        request = copy_params(self.params)
        request.update(copy_params(kwargs))
        from llm.providers.requests import invoke, resolve_provider_options
        options = resolve_provider_options(self.provider_options)
        deadline = request.pop("_provider_deadline", None)
        if options.get("wall_timeout") is not None:
            end = time.monotonic() + options["wall_timeout"]
            deadline = min(deadline, end) if deadline is not None else end
        if request.pop("_without_response_format", False):
            request.pop("response_format", None)
        from llm.providers.schema import validate_model_params
        validate_model_params(request, operation=self._operation, require_model=True, json_contract=False)
        call = self._call_fn
        if call is None:
            # Heavy SDK initialization must not block the application's loop.
            from llm.providers.runtime import litellm_sdk, diagnostic
            sdk = await asyncio.wait_for(asyncio.to_thread(litellm_sdk), None if deadline is None else max(0, deadline - time.monotonic()))
            call = getattr(sdk, self._operation)
            diagnostic("provider_request", operation=self._operation,
                num_retries=request.get("num_retries"), max_retries=request.get("max_retries"),
                caching=request.get("caching"), cache=request.get("cache"),
                sdk_cache="none" if sdk.cache is None else "configured")
        result = await invoke(self._operation, request, call, options, deadline=deadline,
                              sdk_defaults=self._call_fn is None)
        return result
