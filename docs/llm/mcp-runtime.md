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
# Prompts 조회와 명시적 선택

`MCPData.alist_prompts(identifier, cursor=...)`와
`MCPData.aget_prompt(identifier, name, arguments=...)`는 한 번의 연결로 외부 데이터를 반환한다.
같은 연결을 재사용하려면 `async with data.connect(identifier) as access:` 안에서
`access.alist_prompts()` / `access.aget_prompt(name, arguments=...)`를 호출한다.

서버가 prompts capability를 선언해야 하며 선택 connector가 지원하지 않으면 명확히 실패한다.
cursor를 생략하면 SDK에 pagination 옵션을 만들지 않는다. arguments도 생략과 명시적 null을
구분하며, 지정할 때는 MCP 계약인 문자열 → 문자열 object를 사용한다. 서버 고유 인자의 의미와
필수값은 서버가 검증한다. Backend는 이름을 자동 선택하거나 전체 페이지를 자동 순회하지 않는다.

결과의 messages/description/metadata는 외부 자료다. Engine 입력, Agent 지침, Tool 권한으로
자동 주입하거나 영속 Prompt 정의로 자동 변환하지 않는다. Application이 조회·선택·적용을
별도로 결정한다. 기존 Project 정의 revision 검사, 연결 종료·취소 회수, 승인 경계는 그대로다.
모델 sampling·자동 재연결·자동 재시도는 이 확장에 포함하지 않는다.

`MCPToolError.result`는 신뢰된 호스트가 원격 `isError` 결과를 살펴볼 때 사용한다.
기본 진단에는 원문을 추가하지 않고, 명시적인 ProviderDiagnostics.error_details 및
max_detail_chars 계약으로만 서버 오류 텍스트를 제한해 포함한다. `isError`만으로 효과 없음이나
안전한 재시도를 추정하지 않는다.
