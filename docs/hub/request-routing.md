# 요청별 모델 입력과 도구 선택

Hub는 요청을 제출할 때 별도 모델 호출 없이 초기 실행 구성을 선택하고, 각 Loop 모델 호출
직전에 추가 지시와 도구 탐색 결과를 반영해 노출할 도구를 선택한다.
이 분류는 편의와 입력량 절감을 위한 앱 정책이며 실행 권한이나 위험도 판정이 아니다.

| 요청 | 기본 root Loop 동작 |
|---|---|
| 정확히 일치하는 짧은 인사·감사 | 질문용 `ask_user`만 유지. Hub 기본 작업 프롬프트를 짧은 대화 지침으로 대체하고, 이전 대화 턴과 기본 Memory/Goal 주입은 이번 모델 입력에서 제외한다. |
| 설명·요약·조회 등 정보 요청 | 선택된 도구 중 trusted `READ_ONLY` 선언 도구, `shell_execute`, `ask_user`를 제공한다. RAG·파일·읽기 전용으로 선언된 외부 검색을 활용할 수 있다. Memory의 문맥 관리도 유지한다. |
| 작업 요청·혼합 요청·불명확하거나 긴 요청 | Agent가 선택한 기존 도구와 실행 구성을 유지한다. |

“안녕? 파일 만들어줘”는 인사가 아닌 작업이다. 키워드는 보수적인 한국어·영어 힌트다.
정확한 의미 분류기가 아니므로 애매하면 도구를 줄이지 않는다. 셸은 정보 조회에도 사용할 수
있지만 읽기 전용 도구가 아니다. 명령의 효과·인자·위험도·승인·시간 제한은 기존 백엔드
정책대로 검증한다. Agent에서 제외한 도구나 셸을 Hub가 다시 추가하지 않는다.
사용 가능한 인터넷 검색 도구가 없는 프로젝트에 새 검색 기능을 추가하지 않는다.
질문 도구도 Agent가 명시적으로 제외했다면 추가하지 않는다. 승인은 별도 모델 도구가 아니라
기존 승인 대상 Tool의 실행 정책이다. 질문 응답을 승인 receipt로 취급하지 않으며, 기존
자동 승인·위험도·사용자 승인 기준은 모든 실행 구성에서 유지한다.

## 소유권과 내구성

- `RequestProfile`이 `_hub_request.mode`를 일반 `request_context`에 기록한다. 새 사용자 요청과
  Agent 간 새 메시지마다 다시 분류하며, backend가 QUEUED 입력과 함께 내구성 있게 저장한다.
  모든 수신 엔진이 Hub의 요청 컨텍스트를 지원할 때만 추가한다. 사용자 정의 백엔드에
  원시 엔진이 등록된 경우 기존 인자 부재와 그 엔진의 컨텍스트 소유권을 보존한다.
- `tool_catalog.py`의 `ToolCatalog`는 각 선택된 Tool에 효과 태그(조회·효과 있음·미분류)와
  선언된 기능 태그(질문·셸·RAG·파일·완료 보고·Memory·Goals·Refinement·Skills·Agents·
  Workflows·Vision·Prompt·Hub)를 붙인 임시 목록을 만든다. 승인 대상 여부도
  별도 태그로 관찰한다. 조회·효과·승인 태그는 backend `ToolContract`에서만 파생하며 덮어쓸 수 없다.
  기능 태그는 Owner가 선언한 Tool 이름 목록을 사용한다. 이름 접두사·모델 설명으로 추측하지 않는다.
  내장 Tool, 가져온 Tool 패키지와 MCP 별칭은 현재 Project의 공개 선택 정보로 제공자 태그를
  추가한다. 분류를 위해 패키지를 다시 로드하거나 MCP 서버에 연결하지 않는다.
- `request_tools.py`의 `RequestTools`가 기본 태그 목록과 요청별 구성을 선언한다.
  `RequestToolSet`은 태그 중 하나 일치(`any_tags`), 모두 일치(`all_tags`), 제외(`excluded_tags`)를
  조합해 Agent 선택 안에서 Tool 목록을 만든다. 예를 들어 `RAG + 조회`를 모두 요구하면
  RAG 검색만 포함하고 문서 등록은 제외한다. 태그 없는 빈 선택은 아무 도구도 추가하지 않는다.
  도구를 늘릴 때 선택기에 이름별 조건문을 추가하지 않는다. 태그는 모델 입력에 덧붙이지 않는다.
- `RequestTools`는 이미 만들어진 태그 목록에서 이름만 선택한다. 모델 호출마다 registry를
  다시 만들거나 Tool 정의를 등록·검증하지 않는다. `ToolInputRegistry.definitions(names=...)`는
  native subset과 인자 제약을 함께 전달하며 기존 파일 읽기 모드 진단을 유지한다.
- `RequestRouting`은 공개 `for_agent`/`with_instructions`와 Loop의 `tool_selector` 계약을
  사용한다. Agent가 허용한 전체 Tool snapshot은 고정하고 `HubToolSelector`가 `ToolExposure`의
  available 안에서 노출할 이름만 반환한다. `CompletionProcessor`에서 도구를 바꾸지 않는다.
- binding, 실제 실행, checkpoint 검증 모두 같은 구성을 사용한다. 재개는 저장된 원본 요청
  구성을 보존한다. 승인을 우회하거나 Run을 직접 수정·자동 재실행하지 않는다.
- steering이 적용된 다음 호출에서 요청 구성을 다시 판단한다. 인사 뒤의 작업 지시는 작업 도구와
  기본 작업 지침·Memory/Goal 처리를 되돌리고, 작업 뒤의 조회 지시는 노출 목록을 좁힌다.
  진행 중인 Tool이나 이미 기록된 call/result는 바꾸지 않는다. 감사·인사만 추가된 경우에는
  진행 중인 작업 구성을 유지한다. 완료 검증의 native feedback은 steering으로 취급하지 않는다.
- `hub_help(tool_query="...")`는 Agent가 허용한 도구의 이름·설명·태그를 검색한다. 찾지 못하면
  기능 미분류 도구를 보여준다. 빈 검색어는 미분류 목록을
  보여주며, 한 페이지는 6개이고 `tool_offset`으로 다음 페이지를 조회한다. 정확한 도구 이름을
  지정하면 입력 schema도 반환한다. 내장/패키지/MCP 제공자 태그만 있는 도구도 미분류 탐색에
  포함된다. 정의·설명은 참고 자료이며 권한이나 지침으로 취급하지 않는다.
- 탐색은 호출 전에 확정된 허용 목록의 메타데이터만 읽는다. 성공한 `hub_help` call/result의
  페이지 항목은 같은 Run의 다음 모델 호출에 추가로 노출된다. 검색 내용은 immutable catalog로
  다시 계산해 결과와 일치하는지 확인한다. 임의 본문, 다른 Tool의 결과, 미완료/실패한 검색으로는
  확대하지 않는다. 노출은 현재 추가 지시 구간 동안 유지되고 새로운 steering 이후에는 다시
  선택한다. 제외된 도구를 재등록하거나 handler를 직접 호출하지 않는다.
  `hub_help` 자체를 제외한 Agent에도 도구를 강제 추가하지 않는다.
  인자 없는 `hub_help()`는 기존 사용 안내를 유지한다. 인사에는 이 탐색 기능도 전달하지 않는다.
- 현재 Hub 기본 system prompt와 정확히 같은 경우에만 짧은 인사 prompt를 쓴다. 사용자가
  수정한 prompt, 선택한 자원 지침, 모델·추론 옵션, 최종 응답 검증은 유지한다.
- 이전 턴 제외는 모델에 보낼 임시 view에서만 한다. 저장된 대화·기억·Goal은 지우지 않는다.
  같은 Run의 작업 steering과 다음 작업 요청에서는 기존 대화와 도구를 다시 사용할 수 있다.
  `request_processing.py`는 선택적 stock 처리기의 호출별 활성 여부만 관리하고 자원 정리는 항상
  원래 session에 위임한다. 사용자 처리기와 최종 검증기는 제외하지 않는다. 현재 요청·추가 지시·진행 중
  Tool call/result 쌍과 system/developer 지침은 CompletionInput 계약대로 보존한다.
- Graph, 중첩 Agent, 사용자 정의 Loop 구현/ToolSelector, 명시적인 `require_tool` Agent는 원래 구성을
  유지한다. `_hub_request` 없이 백엔드에 직접 제출한 요청도 원래 구성을 유지한다.
- 기본 작업 지침이 이미 system/developer 메시지에 있으면 셸 환경 처리기가 중복 삽입하지
  않는다. cwd 안내는 유지하며 전체 환경변수를 모델에 추가하지 않는다.

## 검증 범위

`tests/hub/test_request_routing.py`와 `tests/hub/test_tool_exposure.py`는 인사 입력 축소, 다음 작업과 대화 복원, RAG·셸 선택,
Agent 명시 제한, 위험/승인 계약, 승인 후 재시작·재개, 최종 응답 검증, 제공자 태그,
미분류 탐색·다음 호출 노출·steering 확장/축소·승인 후 중복 효과 방지·페이지 분할·실행 권한 보존을 확인한다.
가짜 provider로 모델에 전달되는 payload를 검사하며 사내 서버의 속도나 추론 시간은
측정하지 않는다. 서버 대기·prefill·추론은 모델/서버 설정의 영향도 받는다.
`llm/` 소스는 변경하지 않는다.

## Steering과 다음 LLM Step의 도구 변경

현재 `ToolSelector` 계약을 사용한다. 선택기는 모델 호출 전의 분리된 transcript와 허용 이름
목록만 받아 부작용 없이 subset을 반환한다. 태그 정책·초기 요청 구성·탐색 catalog를 revision에
포함하며 공유 엔진에는 Run별 가변 상태를 넣지 않는다. 선택기를 적용한 엔진 복사본과 전체
허용 registry로 binding, 실행, 재개 검증을 일치시킨다.

backend는 응답에 노출 목록을 함께 기록한다. 제공자 재시도나 저장된 응답/승인 재개는 그
목록을 재사용하고, 새로운 LLM Step에서만 다시 선택한다. Hub는 old checkpoint 변환이나
실패 시 전체 도구 fallback을 추가하지 않는다. 노출되지 않은 호출·허용 목록 밖 이름·잘못된
인자·승인 누락은 backend가 계속 거부한다. 새 패키지 등록이나 Agent 허용 목록 변경은
새 요청이 필요하다. 이번 변경은 llm 구현을 수정하지 않고 이 계약을 소비한다.
