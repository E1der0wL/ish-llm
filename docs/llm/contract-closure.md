# 전수 계약 소유권 최종 교차 점검

2026-10-25, 관리 화면 소단계 `86cae96` 이후의 현행 계약 기준이다. `llm/`을 우선 완료한 뒤
Hub와 실행 assets까지 같은 규칙으로 전환했다. 이전 단계·실패·검증 기록은
[전수 실행 기록](whole-ownership-verification.md)에 그대로 남긴다.

639개 제품 파일·assets, 15,124개 정적 후보를 현행 hash와 대조했다. 미검토·stale·미분류·
확정 위반·미완료 계약·동적 경로 미검토·읽기 실패·감사 issue는 각각 0이다. 후보의 개수는
위반 개수가 아니다. 적법한 선언과 순수 언어 표현/독립 예제의 개별 근거는
[개별 검토](contract-coverage-reviews.json), 실제 파일 목록은
[전수 목록](contract-coverage-index.json)에 연결한다. 파일/발생 일괄 면제는 추가하지 않았다.

마지막 18개 공유 계약은 예전에 Hub 소비자가 남아 있어 미완료로 유지했던 항목이다.
현재 184개 실제 직접 소비 모듈의 305개 Owner 연결을 import/심볼 사용 위치와 현행 hash로
대조하고, 아래의 이전 미전환 경로를 직접 확인했다. 동적 호출·간접 소비는 기존 전체 파일
검토와 실제 호출 코드/독립 관측/집중 시험을 재사용했다. import 개수만으로 완료 처리하지 않았다.

| Owner | 마지막 실제 소비 경로·보존 책임 |
|---|---|
| `llm/core/implementation_parameters.py` | Hub retry now delegates through EngineSelection.completion → ImplementationParameters.read/LoopParameters; defaults, naming, import and Tool parameters use the same envelope Owner. Dynamic implementation names remain open data. |
| `llm/core/schema.py` | All actual Hub form/resource/question/setting consumers use SchemaKeyword/Type/Boolean and Owner traversal. Schema vocabulary is shared while domain schemas remain with their own Owners; UI descriptions do not become validation authority. |
| `llm/core/agents.py` | Binding consumers use AgentBinding properties/read; definitions use AgentDefinition validation/projections in startup, selected-engine resolution, runtime, resources and explicit UI drafts. Admission/Resume validation remains at the execution boundaries. |
| `llm/services/runtime/subscription_state.py` | HubRuntime subscribes with EventChannel/EventDelivery/EventOverflow and reads immutable SubscriptionStats.dropped_count; no duplicated channel/statistics field interpretation remains. |
| `llm/services/schema.py` | ProjectConfiguration/ProjectSchemaDescription/ProjectFormSchemas are consumed by Bootstrap, presets, settings, RAG and real UI pages/screen/JSON editors. Effective observations remain detached and are never persisted as definitions. |
| `hub/config/workspace.py` | CLI and HubConfig both use WorkspaceSelection.default_workspace; preference/restart/draft and destination validation consume the same Owner. Explicit selection and existing absence semantics are preserved. |
| `hub/config/general.py` | GeneralForm/global schema/pages and stored preferences consume GeneralSettings fields and validated draft APIs; host editor selection is observed through the declared settings. No duplicated Backend default is introduced. |
| `hub/config/profile.py` | GlobalSettingsSchema derives identity names from UserProfile attributes and SettingMetadata; GlobalPage delegates schema creation and constructs the same profile type on save. Preferences/presets/runtime/View share that type. |
| `hub/config/notifications.py` | ThemeStyle alert selectors, Icon mappings, GeneralSettings filtering, activity/history/live producers and SessionToasts consume NotificationKind. Display selection is separate from Run execution status authority. |
| `hub/config/sections.py` | Preferences, preset records, service dispatch, live state, navigation, global schema and all settings pages/drafts/screen use PreferenceSection values. No independent section identities remain. |
| `hub/config/preference_records.py` | Bootstrap/runtime/service/live/drafts/restart consume PreferenceSnapshot and explicit mutation/projection; SidebarWidth retains observed presence and fallback semantics. UI preview never rewrites stored defaults. |
| `hub/config/theme_style.py` | Theme, all actual renderers and widgets consume ThemeSelector/ThemeStyle, including image/markdown adapters, selection, search, transcript and chart paths. Protocol/style formatting is owned at its actual boundary. |
| `hub/config/theme.py` | GlobalSettingsSchema derives appearance fields/defaults from HubTheme and enum choices from ThemeMode; settings/preference/renderer consumers use the same declarations and validated theme construction. |
| `hub/ui/input/keys.py` | Runtime key registration, shortcuts, application entry, settings navigation, every management widget and all output/transcript readers consume KeyName/ShortcutAction/ShortcutLabel. Generic dynamic shortcut maps remain open mappings. |
| `hub/widget/forms/settings.py` | Global/general schemas, JSON editor and Project/Global pages consume ChoiceField, FieldDisplay, SchemaForm and shared path helpers; callers no longer recreate fixed input-value meanings. |
| `hub/widget/feedback/notifications.py` | HubView constructs SessionToasts and Live delegates alerts/waiting reconciliation/push through its public API; SessionAlertKey and NotificationKind own identities/categories while Backend Run status remains authoritative. |
| `hub/config/preset_records.py` | PresetStore, PresetService, Bootstrap and PresetUI consume PresetDefinition/Index/Project/Selection; startup reads typed selected-project properties and UI retains original edit/CAS evidence. |
| `hub/backend/project/settings_contracts.py` | Service dispatch and all real Project/settings/preset/live/usage consumers use SettingsOperation/Dispatch and response observations, including SettingsBatch/Catalog, ProjectSummaries and ProjectUsageObservation. No UI policy calculation substitutes for Backend authorization. |

Backend 저장 Reader/Writer·승인·예산·효과·Checkpoint·Resume·CAS·삭제 규칙은 바꾸지 않았다.
열린 metadata·Schema 문법·동적 ID map은 허용된 열린 데이터이며, 고정 의미 부분은 각각의
Owner에 연결했다. 조회 Projection은 실행 권한이나 원본 정의의 새 저장본이 아니다.

Hub README는 전체 원문 980줄과 과거 검증 수치를 그대로 보존하고 현행 Agent CLI·native
Skill·계약/Owner 경로 안내를 앞에 추가했다. 과거 main 사용법을 현재 API로 오해하지 않게 했다.
마지막 관리 화면의 번역 key 집합 실패도 원래 Assertion을 유지해 수정했고, 원래 실패와
수정된 소스의 재실행 증거를 별도로 기록했다.

제품 SHA `3652bbba8878f0658a66fff3213b1976da075687`의 Gate57 Level 3와
[정확한 SHA 원격 CI](https://github.com/E1der0wL/ish-llm/actions/runs/40173512551)를 실제 확인하여 DC3까지 완료했다.
로컬/원격 각각 전체 LLM 2,502개·Hub 588개 통과, failure/error/Skip/누락/중복 0이다.
명령·환경·hash·원격 결과와 기록 커밋의 검증 구분은 [실행 기록](whole-ownership-verification.md)에 남겼다.
기존 전체 LLM/Hub·S1–S7·R3·Provider/실환경 검증은 유지한다. 여기에
`python -m tests.llm.check_contract_coverage --reviews docs/llm/contract-coverage-reviews.json --require-complete --output artifacts/contract-coverage.json`
검사를 GitHub Actions 양쪽 필수 Job에 추가했다. 새 파일·변경 후 오래된 근거·미완료 계약이
있으면 실패하며 결과 artifact도 보존한다. 기존 Job/시험/Assertion/발견 기준은 완화하지 않았다.

현재 소스·시험/fixture·환경/의존성을 기준으로 Level 1·2 증거를 재사용하고 최종 Gate57에서는
전체 LLM/Hub를 각각 한 번의 엄격 실행으로 검증한다. 다른 부분 실행을 합쳐 전체 통과로
표현하지 않는다. 실제 인증 모델·전원 손실·손상 디스크 등 기존 실환경 한계는 검증 정책을 따른다.
