# 공통 모델 호출 값

[A Provider 단계](clean-refactor.md)의 공통 호출 계약은
`llm/core/model_protocol.py`가 소유한다. 현재 A-a는 순수 값과 Reader/Writer 검증까지이며,
기존 Adapter·Engine·Processor·RAG·Vision·Hub의 전환 완료를 뜻하지 않는다.

## 소유권과 값

영속 대화 `Message`, 승인된 실행 snapshot 및 사용량/예약을 기록하는 `CompletionResult`는
각 기존 owner의 계약을 유지한다. `ToolCall`은 완전히 해석된 모델의 제안이며 실행 승인이
아니다. `ModelResponse`의 사용량 정보만으로 원장의 예약량을 해제하지 않는다.

| 값 | 계약 |
|---|---|
| ContentPart | 명시한 kind와 text 값, nullable media_type, JSON opaque 상태 |
| ToolDefinition | 이름·설명·object 인자 Schema, nullable boolean strict, opaque 상태 |
| ToolCall | 비어 있지 않은 ID/이름과 완성된 JSON 인자 object; 부분 문자열은 거부 |
| ToolResult | 호출 ID에 연결된 text 결과와 nullable 이름 |
| ModelMessage | 명시 role, text/typed content parts/null, assistant 호출 묶음 또는 Tool 결과 |
| ModelRequest | typed 메시지·Tool 정의, 선택 모드와 Adapter 소유 parameters |
| ModelResponse | assistant 메시지, 정규 종료 의미, nullable 사용량/identity와 원래 종료 증거 |

role/content kind/종료 의미/Tool 선택 모드는 각각 ModelRole·ContentKind·StopReason·ToolMode가
단일 선언한다. SDK 필드나 signed/opaque 의미는 해석하지 않는다. Adapter가 미완성 조각을
모아 검증한 뒤 호출 제안을 만든다. 공통 값 생성은 SDK 호출·Tool 실행·저장 효과를 일으키지 않는다.

요청의 Tool 교환 검증은 호출 ID 중복·중첩, 결과 누락/중복/불일치와 교환 도중 다른 메시지
삽입을 거부한다. 명시한 `complete=False`는 생성 중인 마지막 미완료 교환의 구조 확인용이다.
이 옵션은 실행 허가나 저장된 필수 결과 복원을 의미하지 않는다. 실행 경계는 완성된 교환을 검증한다.

`parameters`는 런타임 client/callback의 identity를 보존하면서 dict/list/tuple과 공통 값을
분리 복사한다. 공통 messages/tools를 parameters에 넣는 것은 거부한다. SDK별 옵션·retry·
입력 권한은 선택 Adapter가 별도로 검증해야 한다. 기존의 예산·승인 경계를 대체하지 않는다.

## 현재 형식의 Reader와 Writer

ModelMessage·ModelRequest·ModelResponse는 각 클래스가 VERSION을 소유한다. 현재 값은 각각
정수 1이며 별도의 형식이다. `_record`는 해당 클래스의 선언과 `type(version) is int`를 함께
검사하고, dataclass에 선언된 모든 필드와 version만 허용한다. bool/float/문자열 버전,
누락·추가 필드는 거부한다. 중첩 ContentPart·ToolCall·ToolResult·ToolDefinition도 nullable/
optional 필드를 포함한 모든 현재 필드가 있어야 한다. 생성자 기본값은 명시 생성에만 사용하고
Reader가 소실된 opaque/strict/result 등을 기본값으로 복원하지 않는다.

Writer는 새 계약 전체를 출력하고 Reader는 같은 형식 하나만 읽는다. SDK message dict와
구형 record를 추측하는 Reader, alias, 호환 투영은 없다. 유효하지 않은 입력을 수정하거나
저장하지 않는다. 이 값의 버전은 Run/Checkpoint/작업 원장의 버전과 합치지 않는다.

opaque/인자/저장 parameters는 유한한 JSON이어야 한다. 비문자열 key·비유한 수·순환 구조·
런타임 객체는 문자열이나 다른 값으로 강제 변환하지 않고 거부한다. 런타임 client는 호출용
사본에 남을 수 있지만 `to_dict()`로 영속화할 수 없다. 멀티모달 내용의 text 교체는 원본을
소실시킬 수 있으므로 거부한다. text/result 본문 교체는 호출 ID와 opaque 증거를 유지한다.

사용량 `None`, 빈 관찰 `{}`, 확인된 0은 구분한다. 알려지지 않은 사용량을 0으로 만들지
않는다. 알려진 카운트는 중첩 object의 비음수 정수이며 bool·float·null 카운트는 거부한다.
공통 값의 테스트 통과는 실제 Adapter 연결·Tool 효과·재개 안전성 완료 증거가 아니다.
해당 전환은 이후 A 소단계와 상위 전체 게이트에서 별도로 검증한다.

## LiteLLM wire 변환

`providers/wire_codec.py`는 실제 LiteLLM 경계의 message/Tool 정의/request 인코딩과 SDK
message/Tool 정의 디코딩을 소유한다. 현재 A-b의 codec 검증은 기존 스트림 Adapter와
Engine 연결 완료를 의미하지 않는다. SDK dict를 읽는 이 경로를 영속 공통 record의 Reader나
구 API 호환 진입점으로 사용하지 않는다.

SDK의 signed/opaque 확장, 원래 인자 문자열, multimodal part와 누락/null/빈 값의 차이를
Adapter 상태에 보존한다. 완성된 Tool 인자는 유한한 JSON object로 검증하며 falsey 잘못된
container나 부분 문자열을 빈 호출로 바꾸지 않는다. 원래 인자 문자열 재사용은 JSON 타입과
값이 모두 같을 때만 가능하다. Python의 False==0 같은 동등성으로 변경된 인자를 되돌리지 않는다.

공통 role/content/Tool ID/인자를 opaque 확장이 덮어쓸 수 없다. 알려진 Adapter Tool/정의
상태의 필수 필드 소실·잘못된 presence flag·손상된 원래 content 구조는 거부한다. 의미를
모르는 SDK content는 원래 타입과 opaque payload를 운반하고 공통 계층에서 해석하지 않는다.
원래 Adapter 상태가 없는 지원하지 않는 content kind를 임의 wire 형태로 생성하지 않는다.

Tool 선택의 공통 모드와 SDK tool_choice를 동시에 선언하면 거부한다. SDK별 retry·admission·
토큰 예약·실행 권한과 스트림 완료 검증은 연결 단계에서도 기존 owner가 집행해야 한다.
codec은 SDK 호출·Tool 실행·영속 쓰기를 수행하지 않는다.
