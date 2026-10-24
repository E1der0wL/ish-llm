"""Provider request Schema and validation; caller options are never defaulted."""

from copy import deepcopy
from urllib.parse import urlsplit
from jsonschema import Draft202012Validator
from llm.core.model_protocol import copy_model
from llm.core.schema import open_schema, SchemaKeyword, SchemaType, SchemaExtension, OpenSchemaKind
from .request_values import ModelParameters, EmbeddingParameters, LiteLLMParameters, SDKOperation


class ProviderRequestSchema:
    COMPLETION_OWNER = "LiteLLM completion adapter / SDK"
    OPERATION_OWNER = "LiteLLM {operation} adapter / SDK"
    RESPONSE_FORMAT_OWNER = "provider response format"
    DESCRIPTION = "알려진 공통 인자와 추가 JSON 인자를 허용한다. 실제 지원 여부는 공급자/모델에 따른다."
    OPEN_PARAMETERS = True
    REQUIRE_MODEL = False
    JSON_CONTRACT = True
    RUNTIME_CONTRACT = False
    HTTP_SCHEMES = ("http", "https")
    MISSING_COMPLETION_MODEL = "Completion model is required"
    MISSING_MODEL = "Model is required"
    INVALID_MODEL = "Model must be nonempty text"
    INVALID_API_BASE = "api_base must be an HTTP URL"
    COMPLETION_PROPERTIES = {
        ModelParameters.MODEL: {
            SchemaKeyword.TYPE: SchemaType.STRING,
            SchemaKeyword.MIN_LENGTH: 1,
            SchemaKeyword.DESCRIPTION: "공급자/모델 이름",
        },
        LiteLLMParameters.API_KEY: {
            SchemaKeyword.TYPE: [SchemaType.STRING, SchemaType.NULL],
            SchemaKeyword.DESCRIPTION: "공급자 인증 인자",
        },
        LiteLLMParameters.API_BASE: {
            SchemaKeyword.TYPE: [SchemaType.STRING, SchemaType.NULL],
            SchemaKeyword.DESCRIPTION: "공급자 API 주소",
        },
        LiteLLMParameters.TEMPERATURE: {
            SchemaKeyword.TYPE: [SchemaType.NUMBER, SchemaType.NULL],
            SchemaKeyword.DESCRIPTION: "공급자가 지원하는 생성 온도",
        },
        LiteLLMParameters.REASONING_EFFORT: {
            SchemaKeyword.TYPE: [SchemaType.STRING, SchemaType.NULL],
            SchemaKeyword.MIN_LENGTH: 1,
            SchemaKeyword.DESCRIPTION: "공급자가 지원하는 추론 노력 수준",
        },
        LiteLLMParameters.MAX_TOKENS: {
            SchemaKeyword.TYPE: [SchemaType.INTEGER, SchemaType.NULL],
            SchemaKeyword.MINIMUM: 1,
            SchemaKeyword.DESCRIPTION: "최대 출력 토큰",
        },
        LiteLLMParameters.MAX_COMPLETION_TOKENS: {
            SchemaKeyword.TYPE: [SchemaType.INTEGER, SchemaType.NULL],
            SchemaKeyword.MINIMUM: 1,
        },
        LiteLLMParameters.TOP_P: {SchemaKeyword.TYPE: [SchemaType.NUMBER, SchemaType.NULL]},
        LiteLLMParameters.SEED: {SchemaKeyword.TYPE: [SchemaType.INTEGER, SchemaType.NULL]},
        LiteLLMParameters.TIMEOUT: {
            SchemaKeyword.TYPE: [SchemaType.NUMBER, SchemaType.NULL],
            SchemaKeyword.EXCLUSIVE_MINIMUM: 0,
        },
        LiteLLMParameters.NUM_RETRIES: {
            SchemaKeyword.TYPE: [SchemaType.INTEGER, SchemaType.NULL],
            SchemaKeyword.MINIMUM: 0,
        },
        LiteLLMParameters.MAX_RETRIES: {
            SchemaKeyword.TYPE: [SchemaType.INTEGER, SchemaType.NULL],
            SchemaKeyword.MINIMUM: 0,
        },
        LiteLLMParameters.RESPONSE_FORMAT: open_schema(
            RESPONSE_FORMAT_OWNER, category=OpenSchemaKind.PROVIDER, type=[SchemaType.OBJECT, SchemaType.NULL]
        ),
    }
    OPERATION_FIELDS = (
        ModelParameters.MODEL,
        LiteLLMParameters.API_KEY,
        LiteLLMParameters.API_BASE,
        LiteLLMParameters.TIMEOUT,
        LiteLLMParameters.NUM_RETRIES,
        LiteLLMParameters.MAX_RETRIES,
    )
    EMBEDDING_PROPERTIES = {
        EmbeddingParameters.DIMENSIONS: {
            SchemaKeyword.TYPE: [SchemaType.INTEGER, SchemaType.NULL],
            SchemaKeyword.MINIMUM: 1,
        }
    }

    @classmethod
    def completion(cls):
        return open_schema(
            cls.COMPLETION_OWNER,
            category=OpenSchemaKind.PROVIDER,
            properties=deepcopy(cls.COMPLETION_PROPERTIES),
            description=cls.DESCRIPTION,
            **{SchemaExtension.OPEN_PARAMETERS: cls.OPEN_PARAMETERS},
        )

    @classmethod
    def operation(cls, operation):
        if operation == SDKOperation.ASYNC_COMPLETION:
            return cls.completion()
        return open_schema(
            cls.OPERATION_OWNER.format(operation=operation),
            category=OpenSchemaKind.PROVIDER,
            properties={
                **{
                    key: {
                        name: deepcopy(value)
                        for name, value in cls.COMPLETION_PROPERTIES[key].items()
                        if name != SchemaKeyword.DESCRIPTION
                    }
                    for key in cls.OPERATION_FIELDS
                },
                **(deepcopy(cls.EMBEDDING_PROPERTIES) if operation == SDKOperation.ASYNC_EMBEDDING else {}),
            },
            **{SchemaExtension.OPEN_PARAMETERS: cls.OPEN_PARAMETERS},
        )

    @classmethod
    def properties(cls, operation):
        return cls.operation(operation).get(SchemaKeyword.PROPERTIES, {})


def completion_schema():
    return ProviderRequestSchema.completion()


def model_schema(operation):
    return ProviderRequestSchema.operation(operation)


def validate_model_params(
    params,
    *,
    operation=SDKOperation.ASYNC_COMPLETION,
    require_model=ProviderRequestSchema.REQUIRE_MODEL,
    json_contract=ProviderRequestSchema.JSON_CONTRACT,
):
    """값을 변형하지 않는다. provider 주소/모델 해석은 adapter 경계가 소유한다."""
    if json_contract:
        Draft202012Validator(model_schema(operation)).validate(params)
    if require_model and (
        not isinstance(ModelParameters(params).optional_model, str)
        or not ModelParameters(params).model.strip()
    ):
        raise ValueError(
            ProviderRequestSchema.MISSING_COMPLETION_MODEL
            if operation == SDKOperation.ASYNC_COMPLETION
            else ProviderRequestSchema.MISSING_MODEL
        )
    if ModelParameters(params).has_model and (
        not isinstance(ModelParameters(params).model, str) or not ModelParameters(params).model.strip()
    ):
        raise ValueError(ProviderRequestSchema.INVALID_MODEL)
    if LiteLLMParameters(params).optional_api_base is not LiteLLMParameters.UNSPECIFIED:
        url = urlsplit(LiteLLMParameters(params).api_base)
        if url.scheme not in ProviderRequestSchema.HTTP_SCHEMES or not url.netloc:
            raise ValueError(ProviderRequestSchema.INVALID_API_BASE)


def validate_retry_params(params, *, original, operation):
    """SDK retry/실행 protocol은 수정안으로 바꾸지 않는다. SDK 중첩·권한 확대를 막는다."""
    LiteLLMParameters(params).validate_retry_contract(original)
    validate_model_params(
        copy_model(params), operation=operation, json_contract=ProviderRequestSchema.RUNTIME_CONTRACT
    )
    # 검증 대상은 수정한 값이다. 원래 runtime timeout/client 객체의 SDK 계약을 바꾸지 않는다.
    properties = ProviderRequestSchema.properties(operation)
    for key, spec in properties.items():
        if key in params and (key not in original or params[key] != original[key]):
            Draft202012Validator(spec).validate(params[key])
