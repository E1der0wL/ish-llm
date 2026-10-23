"""프롬프트 메시지를 저장한다. 모델 호출과 소비자별 출력 검증은 소유하지 않는다."""

from llm.components.definitions import DefinitionComponent
from .records import PromptDefinition


class PromptComponent(DefinitionComponent):
    """messages와 열린 메타데이터를 관리한다. 코드 실행이나 문자열 템플릿 평가는 하지 않는다."""

    name = directory = CAPABILITY = PromptDefinition.CAPABILITY
    capabilities = (CAPABILITY,)
    schema = PromptDefinition.SCHEMA
