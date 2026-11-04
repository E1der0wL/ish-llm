# MCP 연결과 Memory 실패 처리

## MCP 사용 경로

1. `/mcp`에서 신뢰하는 서버 정의 JSON을 가져온다. 저장·목록·편집은 연결을 열지 않는다.
2. 서버를 선택하고 **연결·조회**을 누른다. stdio는 로컬 서버 프로세스를 실행하며
   streamable_http는 지정한 서버에 연결한다. 이 동작은 도구 실행이 아니다.
3. 공개 `MCPData.aprepare()`로 발견 자료를 저장하고 연결을 닫는다. 적용 가능한 Agent와
   도구 목록을 같은 보통 크기 팝업에 표시한다. Tab으로 영역 이동, ↑↓와 Space로 도구 선택,
   버튼 줄에서는 ←→와 Enter를 사용한다.
4. 확인하면 선택한 Agent의 `resources.mcp[server]`만 공개 `arevise(expected_revision=...)`로
   변경한다. 다른 서버·모델·권한·Project 기본값은 보존하며 다음 Run에 적용한다.
   기존 별명은 보존하고 새 도구에는 충돌을 검사한 고정 별명을 만든다.

예시 정의(서버 주소·명령·인증 정보는 사용자가 지정):

```json
{"transport": "stdio", "command": "/path/to/server", "args": []}
```

```json
{"transport": "streamable_http", "url": "https://server.example/mcp"}
```

Agent의 Engine이 공개 스키마에서 MCP 리소스를 허용해야 선택 목록에 나타난다.
Graph orchestrator에 도구를 강제로 추가하지 않는다. Workflow 안에서 실제 도구를 사용하는
Agent를 선택한다. Agent의 기존 도구 선택과 정책은 유지된다.

정의·발견 자료가 달라지면 다시 연결·선택하며, Agent revision이 바뀌면 기존 내용을
덮어쓰지 않는다. 발견 목록에서 사라진 기존 선택은 ‘사용 불가’로 표시하고 명시적으로
선택 해제해야 적용할 수 있다. 원격 실행 시점의 검증과 승인은 여전히 Backend 책임이다.
Hub의 사전 비교는 여러 소유자의 원자적 트랜잭션이나 원격 계약 고정 보장이 아니다.

## 연결·로그·오류 경계

- 기본 구성에 표준 MCPConnector를 연결하되 SDK import와 연결은 실제 사용 시점까지 미룬다.
  사용자 component factory가 제공한 MCP 구현은 그대로 우선한다.
- 같은 async task에서 연결을 열고 사용·닫는다. 전역 연결 풀·자동 재연결·원격 Tool 재실행은 없다.
- 기본 connector는 timeout 인자를 생략한다. SDK 기본값을 복제하거나 None으로 바꾸지 않는다.
  사용자 connector의 명시적 null/양수 선택도 변경하지 않는다.
- stdio stderr는 터미널 대신 연결 범위의 임시 파일로 받고, 종료 시 끝 8 KiB를 기존 provider
  진단 로그에 기록한다. 임시 파일은 닫으면서 삭제한다. MCP 관리 작업은 workspace의 기존
  provider 로그 범위에 Project ID를 연결하며, Run 실행은 기존 Run 로그 범위를 유지한다.
- 연결·인증·권한·timeout·원격 실행 오류의 native 코드를 기존 오류 표시기로 안내한다.
  annotations를 권한으로 해석하지 않으며 불확실한 실행을 자동 재시도하지 않는다.
- 진행 상태는 백엔드 작업 시그널과 팝업 최하단 한 줄에 표시한다. UI 스레드에서 서버에
  연결하지 않으며, 팝업을 닫아도 늦은 응답이 팝업을 다시 열지 않는다.

OAuth 로그인·토큰 저장, 서버 elicitation, resource/template 탐색 UI는 이번 범위가 아니다.
기록형 질문과 달리 MCP elicitation은 살아 있는 원격 연결에 종속되므로 일반 질문 API로
대체하거나 연결 종료 뒤 전송하지 않는다.

## Memory

Memory의 새 실패 계약에 맞춰 응답 검증 실패와 그 외 실패를 구분한다. 실행 판정은
Backend가 수행하며 Hub는 설정과 원본 상태를 관찰한다. `STEP_HANDLED` 때 Step 캐시를
무효화하고 native 처리 근거가 있을 때만 ‘오류 처리됨 · 작업 계속’을 표시한다.
원래 실패 상태·진단은 남고 Run 완료 여부는 Run의 실제 상태를 따른다.

큰 결과는 기존 [누적 문맥 관리](working-context.md)의 원문 참조와 CompletionPolicy 검사로
처리한다. 요약의 응답 형식 오류를 처리한 뒤에도 원문·Tool receipt·승인을 바꾸지 않는다.
요약에 실패했다는 이유로 예산을 초과한 요청을 보내거나 원래 명령을 다시 실행하지 않는다.
기존 Project를 열 때 설정을 재주입하지 않으며 명시적 저장 시 사용자 값이 우선한다.

## 검증 상태

질문·승인·MCP UI는 사용자 확정 완료다. 실제 PTK 렌더러에 예시 데이터를 넣어 캡처했고,
실제 서버/모델 호출·사용자 workspace 변경은 하지 않았다.
검사는 MCP 정의 CRUD 무접속, task 수명, native 발견·선택·CAS·허용 도구,
원격 효과 미실행, Memory의 검증 실패 처리와 미분류 오류 전파, 큰 결과 참조와 상태 표시를
포함한다. 실행 결과는 [질문·승인 통합 검증](tool-feedback-questions.md#2026-11-05-검증-결과)에 기록한다.
