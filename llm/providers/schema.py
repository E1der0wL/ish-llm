"""Provider가 소유하는 요청 schema. 상위 Engine/Component는 옵션 이름을 복제하지 않는다."""

from llm.core.schema import open_schema
from jsonschema import Draft202012Validator
from urllib.parse import urlsplit


def completion_schema():
    return open_schema("LiteLLM completion adapter / SDK", category="provider", properties={
        "model": {"type": "string", "minLength": 1, "description": "공급자/모델 이름"},
        "api_key": {"type": ["string", "null"], "description": "공급자 인증 인자"},
        "api_base": {"type": ["string", "null"], "description": "공급자 API 주소"},
        "temperature": {"type": ["number", "null"], "description": "공급자가 지원하는 생성 온도"},
        "max_tokens": {"type": ["integer", "null"], "minimum": 1, "description": "최대 출력 토큰"},
        "max_completion_tokens": {"type": ["integer", "null"], "minimum": 1},
        "top_p": {"type": ["number", "null"]}, "seed": {"type": ["integer", "null"]},
        "timeout": {"type": ["number", "null"], "exclusiveMinimum": 0},
        "num_retries": {"type": ["integer", "null"], "minimum": 0},
        "max_retries": {"type": ["integer", "null"], "minimum": 0},
        "response_format": open_schema("provider response format", category="provider", type=["object", "null"]),
    }, description="알려진 공통 인자와 추가 JSON 인자를 허용한다. 실제 지원 여부는 공급자/모델에 따른다.",
       **{"x-open-parameters": True})


def model_schema(operation):
    """LiteLLM 작업별 요청 계약. 상위 RAG는 이 필드 목록을 복제하지 않는다."""
    if operation == "acompletion":
        return completion_schema()
    return open_schema(f"LiteLLM {operation} adapter / SDK", category="provider", properties={
        "model": {"type": "string", "minLength": 1},
        "api_key": {"type": ["string", "null"]}, "api_base": {"type": ["string", "null"]},
        "timeout": {"type": ["number", "null"], "exclusiveMinimum": 0},
        "num_retries": {"type": ["integer", "null"], "minimum": 0},
        "max_retries": {"type": ["integer", "null"], "minimum": 0},
        **({"dimensions": {"type": ["integer", "null"], "minimum": 1}} if operation == "aembedding" else {}),
    }, **{"x-open-parameters": True})


def validate_model_params(params, *, operation="acompletion", require_model=False, json_contract=True):
    """값을 변형하지 않는다. provider 주소/모델 해석은 adapter 경계가 소유한다."""
    if json_contract:
        Draft202012Validator(model_schema(operation)).validate(params)
    if require_model and (not isinstance(params.get("model"), str) or not params["model"].strip()):
        raise ValueError("Completion model is required" if operation == "acompletion" else "Model is required")
    if "model" in params and (not isinstance(params["model"], str) or not params["model"].strip()):
        raise ValueError("Model must be nonempty text")
    if params.get("api_base") is not None:
        url = urlsplit(params["api_base"])
        if url.scheme not in ("http", "https") or not url.netloc:
            raise ValueError("api_base must be an HTTP URL")
