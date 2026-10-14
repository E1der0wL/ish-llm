# 작업 중 컴포넌트 사용과 리소스 생성

Hub는 llm의 공개 ComponentData API와 ToolExecutor를 사용합니다. `llm/`의 저장 형식,
Run 소유권, 승인·체크포인트 계약은 변경하지 않습니다.

## 대화 명령과 관리 창

Tool, RAG, Workflow, Skill, Agent만 `/이름 config`, `/이름 help`를 제공합니다.
Tool·Workflow·Skill·Agent는 단수와 복수 이름을 모두 받습니다. 그 외 컴포넌트는
슬래시 후보에 나타나지 않습니다. list/get/create/update/delete/settings/install 명령은 제거했습니다.

관리 창에서 파일 경로를 자동완성하여 가져오고, 목록에서 편집·삭제합니다.
설정 버튼은 해당 컴포넌트의 프로젝트 설정으로 이동합니다. Agent는 JSON 정의를
가져오며 공개 snapshot/revise API로 버전을 검사합니다. 오래된 편집으로 덮어쓰지 않습니다.

## 모델 도구

| 컴포넌트 | Hub가 연결하는 사용 방식 |
| --- | --- |
| Memory | memory_create/update/delete/get/search 및 키워드 기반 자동 회상 |
| Goals | goal_list/read/update_progress/link_run과 Hub의 goal_create/complete |
| Refinement | 근거·대상 조회, 개선안 제안, 승인 후 적용·되돌리기 |
| Skills | 기존 skill_list/read와 skill_schema/create |
| Workflows | workflow_schema/list/read/create |
| Agents | 기존 agent_run과 agent_schema/list/read/create |
| RAG | 기존 rag_search와 rag_add_document |

생성 도구는 현재 Run의 Project에만 저장하며 저장 디렉터리를 모델 인자로 받지 않습니다.
정의는 컴포넌트 스키마로 검증하고 기존 식별자를 덮어쓰지 않습니다. 생성 출처에는 실제
Session/Run/Step을 기록합니다. 새 실행 정의는 다음 Run부터 사용합니다.
생성과 Goal 변경, Refinement 적용은 기존 Tool 승인 흐름을 사용합니다.

workflow_schema는 공통 그래프 스키마와 실제 등록된 Graph 핸들러의 필드 정의를
`x-hub-action-handlers`로 반환합니다. 스키마를 제공하지 않는 사용자 핸들러는 null로
표시하여 모델이 계약을 추측하지 않도록 합니다.

RAG 등록은 공개 문서 API를 통해 임베딩·색인을 준비합니다. 임베딩 모델 설정은 필요하며
모델을 임의로 지정하지 않습니다. 파일 내용을 등록할 때는 일반 파일 도구로 읽은 후
rag_add_document에 전달합니다. 원문 저장과 검색 확인 결과를 구분해 보고하도록 지시합니다.

## 기본 지침과 정책

새 프로젝트에 다음 스킬을 기존 코드 수정·진단·문서 스킬과 함께 저장합니다.

- hub-create-rag: 자료 읽기, 문서 등록, 검색 검증
- hub-create-workflow: 스키마와 참조 확인, 그래프 저장·검증
- hub-create-skill: 지침 작성, 저장, 사용 범위와 검증 절차 명시
- hub-create-agent: 목적·도구·입출력 계약 작성, 저장과 실행 검증 구분

기본 시스템 지침은 필요한 스킬을 읽고 도구로 실제 결과를 확인하도록 안내합니다.
Memory는 새 사실을 candidate로 저장하고 관련 기억을 참고 자료로 회상합니다.
별도 보조 모델 호출을 요구하는 요약·추출은 자동 활성화하지 않습니다.
Goals는 현재 세션의 복합 작업을 추적하고 활성 목표를 다음 호출의 참고 자료에 포함합니다.
Refinement는 실제 실행 근거를 바탕으로 제안하며 대상 변경에는 승인이 필요합니다.

이 정책과 스킬은 새 프로젝트 생성 템플릿입니다. 명시한 사용자 설정이 우선하고,
기존 프로젝트의 컴포넌트 선택·정책·사용자가 수정하거나 삭제한 스킬은 재열기 시
자동으로 덮어쓰지 않습니다. 기존 프로젝트에 적용하려면 llm의 공개 프로젝트/컴포넌트
API로 선택과 정책을 저장하거나 새 Hub 프로젝트를 생성합니다. 관리 팝업은 이미 선택된
컴포넌트만 노출합니다.

## 검증

test_resource_tools는 실제 Loop/Tool/승인·재개 경로를 실행하며 모델만 결정적으로 대체합니다.
생성·저장·읽기, 승인 전 미적용, RAG 검색, Memory 회상, Goal 문맥 주입과 Refinement 적용,
저장된 Agent 위임과 생성한 Workflow의 Graph 실행을 검증합니다. 실제 외부 모델이 모든
상황에서 스킬을 선택할지는 모델의 판단에 달려 있습니다.
