# 공통 모델 호출 값

[A Provider 단계](clean-refactor.md)의 공통 호출 계약은
`llm/core/model_protocol.py`가 소유한다. A-c에서 실제 Adapter·Engine·Processor·Memory·RAG·Vision·worker·Hub
호출 경로에 연결했다. A 전체 완료와 원격 CI 통과는 [검증 기록](provider-refactor-verification.md)의
별도 상위 게이트 결과를 기준으로 판단한다.

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
| CompletionUpdate | text/reasoning delta와 공통 usage/identity/종료 관찰 |
| EmbeddingResult | 검증한 단일 벡터, nullable usage와 opaque SDK 증거 |
| RankedDocument / RerankResult | 유한 score와 중복 없는 nonnegative index, nullable usage와 opaque 증거 |

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
사용량과 예약의 확정은 기존 서비스 owner가 담당한다. Decoder가 finish에서만 제공한
usage/identity/종료도 CompletionResult에 기록하고, 미상 사용량은 기존 예약을 유지한다.

## LiteLLM wire 변환

`providers/wire_codec.py`는 실제 LiteLLM 경계의 message/Tool 정의/request 인코딩과 SDK
message/Tool 정의 디코딩을 소유한다. 실제 LiteLLM 스트림·비스트리밍 Adapter가 이를 사용한다.
SDK dict를 읽는 이 경로를 영속 공통 record의 Reader나
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

재시도 기본 해석은 기존 RetryPolicy를 재사용한다. Backend max_attempts가 생략되면 최초
1회만 허용하고 명시하면 그 상한을 사용한다. 실제 SDK 전송 경계는 생략된 SDK 재시도를 0으로
보장하되 원본 요청/저장 설정·명시한 SDK 설정·주입 client의 계약을 보존한다. 명시 SDK 재시도에
Backend 재시도를 중첩하지 않는다. Backend 시도 수와 하위 HTTP 요청 수는 구별하며 실제 HTTP
횟수 검증을 포함한다. SDK별 적용은 [Provider 계약](../../llm/providers/README.md)에 있다.


## 실행·저장 경계

ToolRegistry는 공통 ToolDefinition을 제공하고 완성된 argument object를 검증한다. ToolContract의
승인·효과·operation key·retry_fields 선언은 별도이며 모델 제안으로 대체하지 않는다.
Loop는 전체 호출 묶음을 먼저 검증하고 after_completion 뒤 RESPONDED 증거를 저장한다.
공통 정의는 binding 계산 전에 명시적으로 직렬화하므로 객체 타입만으로 승인 identity가 합쳐지지 않는다.

Loop Checkpoint는 `loop-iterations-v2`와 엄격한 ModelResponse record를 사용한다. 구 형식과
필수 필드가 소실된 증거를 재개하지 않으며 기존 파일을 변환하지 않는다. CompletionResult의
nullable termination도 현재 Reader의 필수 필드다. 다른 저장 owner의 버전과 합치지 않는다.
Memory의 derived 요약 signature는 공통 메시지 직렬화를 사용하며 원본 transcript를 수정하지 않는다.

토큰 계수기의 입력도 ModelRequest다. 기본 LiteLLM 계수기만 실제 SDK wire로 변환한다.
Hub는 정규 StopReason으로 분류하고 raw_finish_reason은 증거로만 표시한다. QuestionSource는
Hub 질문의 choices 필드를 소유하며 SDK choices와 관계없는 UI 계약이다.
