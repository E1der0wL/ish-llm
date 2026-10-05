# 설정 일관성

저장 원본은 ProjectConfig이며 `policies`와 `parameters`를 구분한다. 변경 예제와 UI 응답은
[Project 설정](project-settings.md), missing/null/강제값은 [설정 계약](../../llm/CONFIGURATION.md)을 따른다.

## 소유권과 상속

| 대상 | 저장·전달 경로 | 해석 순서 |
|---|---|---|
| Run 문맥·토큰·사용량·보관 정책 | ProjectConfig.policies | Run 시작 사본, 서비스 집행 |
| Loop/Graph/사용자 Engine | parameters.engines[설정 키] | Project → Session → Agent → host |
| RAG/Memory/Vision/사용자 Component | parameters.components[이름] | 주입 client의 명시값 → Project |
| 런타임 자원·권한·함수 | ServiceConfig, 생성자 | host 소유; JSON 저장 안 함 |

모든 구현체는 `config`(기능 입력·SDK 인자), `policy`(자체 실행 판단·제한)를 공개한다.
Loop의 `config.completion`은 Loop 구현체의 입력이다. Graph는 Workflow 조율에 모델을 요구하지 않는다.
RAG의 embedding_params/extraction_params/rerank_params, Vision과 Memory의 completion은 각각
해당 Component에 속한다. 같은 인자 이름을 이유로 설정을 공유하지 않는다.
Agent의 기존 completion/engine_options override는 선택한 Engine의 for_agent 계약이 해석한다.
이들은 Project의 전역 SDK 설정이 아니며 Graph의 모든 노드에 전달되지 않는다.

Session에는 명시된 override만 저장한다. Project 값은 실행 시 상속한다. 정책과 Component
설정은 Session에서 바꿀 수 없다. 호스트 ToolPolicy와 공유 ProviderLimits의 소유권도 유지한다.
실행 중 사본은 Project 편집으로 바뀌지 않는다. 재개는 설정·Tool 계약 지문을 다시 검사한다.

## 소비자 스키마와 실제 실행

EngineRegistry는 등록 엔진이 선언한 입력 스키마와 effective configuration을 검증한다.
잘못된 Project/Session 값은 host override가 있어도 저장하지 않는다. 설정 키를 명시적으로
공유한 엔진들의 스키마는 allOf로 합쳐 모든 소비자의 제약을 지킨다.
계약을 선언하지 않은 사용자 엔진의 임의 JSON 의미를 서비스가 추측하지 않는다.

Loop는 자신의 설정 키에서 completion을 읽는다. `settings_name`으로 다른 키를 명시할 수 있다.
Pipeline은 `parameters.engines.pipeline.config.stages.<단계>`의 config/policy를 해당 단계에 전달한다.
준비 단계와 모델 단계에 timeout/model을 이름 일치로 방송하지 않는다. 별도 settings_name을
명시한 단계만 그 설정을 공유한다.

RAG는 `aconfigure`에 명시된 분할·동시성·검색·모델 옵션을 해석한다. provider native 옵션은
생략 시 라이브러리에 맡긴다. 필요한 알고리즘 설정이 없으면 작업 시작 시 오류다.
준비/검색 중 유효 설정 변경은 충돌로 거부하며 예전 모델의 벡터를 잘못 재사용하지 않는다.

## UI와 저장

`project.aconfiguration()`은 저장된 값과 선택한 모든 컴포넌트의 스키마·설정·출처를 모은다.
없는 키는 values에도 없다. `ComponentData.aconfigure`와 `ProjectHandle.asave`는 같은 저장
원본·잠금·버전 비교를 사용한다. Component별 설정 파일이나 이중 저장소는 없다.

Engine view의 sources/editable/overridden은 `/policy/request_timeout`, `/config/completion/model`처럼
구현체 기준 JSON Pointer다. 동적 completion factory는 runtime 목록과 host_runtime으로
표시하고 실행하지 않는다. Component 모델 클라이언트는 client 출처로 구분한다.
관련 회귀는 test_project_creation, test_configuration_validation, test_settings_consistency,
test_project_component_settings, test_explicit_configuration 및 hub의 설정 UI 테스트에 있다.
