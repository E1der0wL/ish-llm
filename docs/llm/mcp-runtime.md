# MCP 연결·상호작용 계약

llm은 MCP 기능과 검증을 제공하고 Application은 사용 시점과 정책값을 명시한다.
Provider는 SDK 연결/인증/메시지를, MCP Component는 정의/발견 자료/공개 API를 소유한다.
Tool 실행은 기존 ToolExecutor, Project 승인, operation receipt 경계를 통과한다.

## 설정과 연결

- timeout 미설정은 Backend deadline 없음과 SDK 인자 생략이다. 명시적 null은
  지원되는 timeout을 해제하고, 숫자는 양의 유한값만 허용한다. SDK 기본값을 복제하지 않는다.
- 연결 재사용은 명시적인 async context 수명이다. 전역 pool, 숨은 TTL/동시성/재연결
  횟수는 없다. 연결과 작업은 같은 async task에서 열고 닫는다.
- 연결 정의/connector revision/원격 Tool contract 변경은 재검증한다. 재연결은
  이전 Tool 호출의 재실행을 뜻하지 않는다. 효과가 불확실한 호출은 자동 반복하지 않는다.
- 상태/서버 기능/서버 지침 조회는 관찰이다. 서버 지침과 annotations를 권한으로 쓰거나
  모델 prompt에 자동 삽입하지 않는다. Application이 사용할 자료와 시점을 선택한다.

## 리소스

서버가 지원하는 resources/templates의 목록과 resource 읽기를 공개한다. 조회는 서버의
capability와 요청을 검증하며, Application이 명시적으로 연결한 범위에서만 수행한다.
자료는 SDK가 반환한 구조를 보존한다. 기본 모델 주입/요약/다운로드/Tool 등록은 없다.
일반 리소스 API는 신뢰한 Application API이고 Agent의 Tool 권한을 대체하지 않는다.

## OAuth

인증 흐름과 프로토콜 검증은 MCP SDK에 위임한다. Application/Host가 client metadata,
token storage, 로그인 URL 전달 및 callback 응답 함수를 주입한다. llm은 자격증명을
Project/Run/Step이나 새 비밀정보 저장소에 저장하지 않는다. 로그인은 명시적으로 선택한
인증 handler를 통해서만 시작한다. SDK 토큰 갱신과 Tool 효과 재시도는 구별한다.
조회 결과와 관찰 이벤트에 토큰/헤더를 포함하지 않는다.
MCPOAuth는 명시적인 server_url에 바인딩하며 다른 서버 정의에 재사용하면 연결 전에 거부한다.

## 서버 질문

Elicitation은 원격 요청에 묶인 입력 요청이다. 지원은 Application이 handler를 주입한
경우에만 광고한다. Application은 form/URL 내용을 표시하고 accept/decline/cancel을
명시적으로 반환한다. form 응답은 서버의 schema로 검증한다. URL을 자동으로 열거나
승인을 새로 부여하지 않는다.

기록된 일반 질문과 달리 원격 요청이 닫힌 뒤 답변을 전송할 수 없다. 연결/호출 취소는
질문 대기도 취소하며, 과거 답변으로 새 호출을 만들지 않는다. 로컬 재시작 뒤 원격
요청이 살아 있다고 추측하지 않는다. sampling과 전용 UI renderer는 이 계약에 없다.

## Memory 실패 경계

`failure_mode=continue`는 명시적으로 구분된 보조 모델 응답 검증 실패에만 적용한다.
실행/사용량 제한, 설정 오류, 저장·CAS 오류, 취소와 알 수 없는 예외는 전파한다.
실패 Step과 diagnostic은 유지한다. Engine이 제공한 실패 처리 checkpoint가 저장된
뒤에만 계속하며 별도 성공 Step으로 위장하지 않는다. 명시적 압축 요청도 같은 선택을
지원하고, 미설정에 continue를 삽입하지 않는다. 원문/Tool receipt/승인 증거를 바꾸지 않는다.
