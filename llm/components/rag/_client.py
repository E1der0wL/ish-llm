"""재사용 가능한 모델 호출의 공통 클라이언트 처리. Engine/Run/Step의 수명 주기와 분리하여 다른 기능에서 사용할 수 있다.

Shared request mechanics; no scheduler, lifecycle events or domain writes."""

import asyncio
import importlib
from copy import copy
from collections.abc import Awaitable, Callable
from typing import Any, Optional

from llm.providers.parameters import copy_params, merge_params
from llm.providers.observations import observed_call


class ModelClient:
    observes_model_calls = True

    def __init__(self, operation: str, call_fn: Optional[Callable[..., Awaitable[Any]]],
                 params: dict[str, Any]) -> None:
        if call_fn is not None and not callable(call_fn):
            raise TypeError("Model call function must be callable")
        self._operation = operation
        self._call_fn = call_fn
        self.params = copy_params(params)

    def configured(self, params):
        """호스트 함수를 유지한 호출별 사본. 프로젝트에서 전달한 인자를 클라이언트 기본값 위에 적용한다."""
        worker = copy(self)
        worker.params = merge_params(self.params, params)
        return worker

    async def _invoke(self, **kwargs: Any) -> Any:
        request = copy_params(self.params)
        request.update(copy_params(kwargs))
        if not isinstance(request.get("model"), str) or not request["model"].strip():
            raise ValueError("Model is required")
        request.setdefault("timeout", 60)
        request.setdefault("num_retries", 0)
        call = self._call_fn
        if call is None:
            # Heavy SDK initialization must not block the application's loop.
            sdk = await asyncio.to_thread(importlib.import_module, "litellm")
            call = getattr(sdk, self._operation)
        return await observed_call(self._operation, request, call)
