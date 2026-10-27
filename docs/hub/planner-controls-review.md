# 보조 버튼과 기본 코딩 Workflow 검토

## 변경과 소유권

- `MouseBinding`이 보조 버튼의 호버 포커스 콜백을 선언하고 `MouseController`가 기존
  팝업/사이드바 범위 및 드래그 캡처를 확인한 뒤 호출한다. 비포커스 컨트롤은 그대로다.
  `AuxiliaryBar`는 최초 진입 전 컨트롤을 기억한다. 호버는 실행하지 않는다.
- 일반/드롭다운 버튼의 볼드체와 메뉴 색은 `HubTheme`/`ThemeStyle`이 소유한다.
  메뉴는 일반 글꼴, 선택 행은 강조 색 4 배경/강조 색 5 전경, 손잡이는 강조 색 1이다.
  검색 강조는 메뉴 선택 색과 분리하여 기존 표현을 유지한다.
- `HubDefaults`는 `planner`와 `hub-code-workflow`의 생성 식별자와 Agent 정의를 소유한다.
  `asset/coding_workflow.py`는 공개 `WorkflowGraph`로 저장 데이터를 만들며, 단계 지침은
  `asset/prompts/coding-workflow/`의 영어 자산으로 관리한다.
- `backend/project/starter_workflow.py`는 새 Project 생성에서만 공개 Agent/ComponentData
  CRUD를 호출한다. 기존 Project를 열거나 설정을 읽을 때 기본 정의를 재주입하지 않는다.
  Workflow 컴포넌트와 Graph의 AgentNode가 없거나 초기 Agent의 Engine을 실행할 수 없으면
  기본 리소스를 만들지 않는다. 명시한 `planner` ID와 Workflow Agent도 덮어쓰지 않는다.

Graph는 실행 흐름만 소유한다. 각 AgentNode는 초기 behavioral Agent의 모델·연결·도구 선택을
사용하므로 모델명·키·주소를 새로 채우지 않는다. 요청의 cwd/env, 승인, Step, 체크포인트,
명시적 재개 및 효과 영수증은 기존 llm 실행 계약을 따른다. UI 기본 수신 대상을 바꾸거나
일반 요청에 Workflow 실행을 강제하지 않는다. 저장 Agent/Workflow의 참조 보호도 유지한다.

## 검증 범위

Linux Python 3.12.14와 Linux 파일시스템의 해시 기록 복사본을 사용한다.
보고서와 실행 스크립트는 `tests/hub/reports/planner-controls/`에 보관하며 배포/자동 discovery에서 제외한다.

- `final-focused.txt`: UI·마우스·팝업 자동완성·기본 Workflow 집중 검사 46개 통과.
- `recheck.txt`: 기본값/재열기와 Workflow 검사 7개 통과.
- `consumers.txt`: Agent 카탈로그·삭제·스킬 전달 검사 4개 통과.
- `component-recheck.txt`: 저장 참조를 유지한 컴포넌트 선택/CRUD 검사 2개 통과.
- `multi-agent.txt`: 호스트 Engine을 사용하는 다중 Agent 검사 3개 통과.
- `hover-return.txt`: 닫힌 드롭다운 복귀를 포함한 마우스·보조 버튼 검사 33개 통과.
- `last-checks.txt`: RAG/Vision 선택 검사 1개 통과.
- `remaining.txt`: 생성/재열기·프리셋 계약 검사 6개와 명령 등록 검사 3개 통과.
  순차 입력 검사는 본문 완료 직후 아직 실행 중인 Run에 다음 입력을 보내는 경합을 재현했다.
  실행 중 선택 팝업을 유지하고, 순차 입력 테스트가 Run 종료 관측까지 기다리게 수정했다.
- `slash-final.txt`: 수정 후 명령·순차 전송 검사 4개 통과. 최종 소스·테스트 1,105개 파일의
  해시는 `slash-final-snapshot.json`과 일치한다.

전체 Hub discovery(`full.txt`)는 702개를 실행했으며 9개 X11 통합 검사는 Xvfb/xclip 부재로
건너뛰었다. 중간 소스의 실패/오류는 새 기본 Agent 참조·카탈로그 기대값, 스킬 줄바꿈,
Graph가 실행할 수 없는 사용자 정의 Engine, 위 순차 입력 경합에서 발생했다. 해당 구현과
테스트 준비를 수정하고 관련 범위를 재검사했다. 전체 검사가 처음부터 통과한 기록은 아니다.
집중 검사들은 범위가 겹치므로 합산하지 않는다.

Graph 통합 검사는 고정된 Provider 응답과 실제 임시 파일/Python/Bash 실행을 결합한다.
공개 승인/재개 후 파일 생성 → 결과 확인 → 수정 → 검증을
거쳐 마지막 AgentNode의 본문이 Run 응답으로 남는지 확인한다. 네 번의 승인과 여덟 번의
모델 요청을 확인하여 재개 때 완료한 모델 단계를 반복하지 않는지 검사한다.
별도의 기존 `test_agent_graph` 회귀 검사도 승인 전 파일 미생성 및 승인/재개 뒤 효과 1회를 확인한다.
이는 모델의 자율적인 디버깅 품질이나 나머지 언어 툴체인 설치/실행을 검증한 결과는 아니다.
외부 모델 API는 호출하지 않았다. `llm/` 소스는 변경하지 않았다.
