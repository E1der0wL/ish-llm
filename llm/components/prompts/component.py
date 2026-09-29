"""프롬프트 메시지를 저장한다. 모델 호출과 소비자별 출력 검증은 소유하지 않는다."""

from llm.components.definitions import DefinitionComponent


class PromptComponent(DefinitionComponent):
    """messages와 열린 메타데이터를 관리한다. 코드 실행이나 문자열 템플릿 평가는 하지 않는다."""

    name = "prompts"
    directory = "prompts"
    capabilities = ("prompts",)
    schema = {
        "type": "object", "required": ["messages"],
        "properties": {
            "description": {"type": "string"},
            "messages": {"type": "array", "minItems": 1, "items": {
                "type": "object", "required": ["role", "content"],
                "properties": {
                    "role": {"enum": ["system", "user", "assistant"]},
                    "content": {"type": "string", "minLength": 1},
                }, "additionalProperties": False,
            }},
        },
    }
