# Lifecycle — 도메인 생성·변경·삭제

Project, Session, Step과 Component 데이터 핸들의 수명을 관리합니다. Manager는 작업 순서와 검증을, Repository는 도메인 기록 접근을 담당합니다. 앱/UI는 보통 상위 Facade를 사용합니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | 수명 관리 패키지의 역할을 설명합니다. |
| [access.py](access.py) | Project 소유권·삭제 여부·접근 경계를 검사합니다. |
| [projects.py](projects.py) | ProjectRepository/ProjectManager: 생성, 설정, Component 선택, 복제·삭제·복원·백업을 조율합니다. |
| [sessions.py](sessions.py) | SessionRepository/SessionManager와 런타임 상태 컨테이너 SessionRuntime을 정의합니다. |
| [steps.py](steps.py) | StepRepository/StepManager가 Step 수명을 관리하고 StepEventRecorder가 EngineEvent를 저장으로 연결합니다. |
| [components.py](components.py) | ComponentData가 CRUD·설정 API에 잠금, 최신 Project 확인, 비동기 I/O와 충돌 검사를 적용합니다. |

## 책임 경계

ProjectManager는 Component에 초기화·복제·삭제를 위임하며 각 Component의 내부 파일 구조를 알지 않습니다. SessionManager는 Engine을 실행하지 않습니다. SessionRuntime은 메모리 객체이며 Session JSON에 저장되지 않고 RunManager가 실행 수명을 관리합니다.

ComponentData의 `configure/aconfigure`는 ProjectConfig의 해당 설정을 교체합니다. 저장 설정의 유일한 위치는 `component_configurations`입니다. 사용자 정의 핸들도 이 경계를 유지해야 합니다.

서비스를 직접 조합할 필요가 없다면 [LargeLanguageModel](../../README.md) → `projects` → `sessions` → `run` 경로를 사용하세요. 동기 API를 UI 루프에서 직접 호출하는 대신 비동기 Facade를 사용합니다.

[상위 안내](../README.md)
