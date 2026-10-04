# Core — 도메인과 공통 데이터 계약

Project → Session → Run → Step의 저장 모델과, Engine·서비스·UI가 함께 사용하는 데이터 구조를 정의합니다. 모델 호출·파일 저장·UI 렌더링은 하지 않습니다. 개발자가 이벤트와 조회 결과의 형태를 확인할 때 시작하는 폴더입니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | Core 패키지의 역할을 설명합니다. 타입은 각 모듈에서 import합니다. |
| [models.py](models.py) | ProjectConfig, Project, Session, Message, Run, Step과 상태·저장 버전·Run 전이 검증을 정의합니다. |
| [paths.py](paths.py) | Project/Session/Run/Step의 주요 경로를 정의합니다. Component 내부 경로는 포함하지 않습니다. |
| [configuration.py](configuration.py) | 명시된 설정 계층을 병합하고 values/sources/overridden/editable을 계산합니다. |
| [policies.py](policies.py) | ProjectConfig.policies의 JSON 형식과 정책 값 검증을 담당합니다. |
| [schema.py](schema.py) | 설정 UI에 쓰는 JSON Schema 생성·검증 도우미입니다. |
| [contracts.py](contracts.py) | Diagnostic, OperationProgress, ResourceRef, ProjectActivityEvent 등 공통 관찰 데이터를 정의합니다. |
| [interactions.py](interactions.py) | InteractionRequest/Response와 선택지·승인·재개 값의 공통 해석 계약입니다. |
| [results.py](results.py) | EngineDelta, EngineOutput, CompletionResult, ExecutionResult를 정의합니다. |
| [steering.py](steering.py) | RunInstruction/InstructionStatus, SteeringTarget/SteeringMode, 예약 경로 SteeringRoute와 일반 대기 요청 구분입니다. 새 영속 도메인은 아닙니다. |
| [views.py](views.py) | SessionRuntimeView, RunView 등 UI 조회용 스냅샷입니다. |
| [plans.py](plans.py) | 재개·복구·보관 작업의 계획과 결과 데이터입니다. |

## 변경할 때 지킬 계약

- 영속 모델에 asyncio.Task, Queue, lock, live client를 넣지 않습니다.
- 저장 버전은 `storage_version=1`입니다. 이전 Task 형식을 자동 변환하지 않습니다.
- `ProjectConfig`는 열린 JSON 설정입니다. 미설정 leaf 값을 만들어 넣지 않습니다.
- `policies`는 서비스가 집행하고 `parameters.engines/components`는 해당 구현체에만 전달합니다. SDK 옵션은 구현체의 하위 설정이며 Project 공용 completion은 없습니다.
- `EngineDelta`는 진행 중 변경, `EngineOutput`은 출력, `ExecutionResult`는 Run 결과 조회입니다. 서로를 새 저장 도메인으로 만들지 않습니다.
- Interaction의 권한 판정·응답 저장은 서비스 책임입니다. 선택지 해석 함수 자체가 승인 권한을 부여하지 않습니다.

설정 규칙은 [CONFIGURATION.md](../CONFIGURATION.md), 저장·이벤트 처리는 [Services](../services/README.md)를 참고하세요.

[상위 안내](../README.md)
