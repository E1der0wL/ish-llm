# Workflow 검증·추가 지시·접수 영수증·음성 계약

이 기능들은 Project → Session → Run → Step 및 기존 승인·명시적 재개 계약을 사용한다.
UI 관찰, 음성 스트림, 편집용 검증은 새로운 실행 도메인이나 권한을 만들지 않는다.

## 실행 없는 Workflow 초안 검증

```python
from llm.engines.graph.agent import AgentNode
from llm.engines.graph.tool import ToolNode

workflows = await project.components.aget("workflows")
report = await workflows.ainspect(
    draft,
    handlers={"agent": AgentNode(engines=engines), "tool": ToolNode()},
    declared_tools=host_tools,
)
print(report.valid, report.to_dict())
```

`handlers`는 선택된 GraphEngine의 `workflow_handlers()`에서 얻은 선언을 전달한다.
`declared_tools`는 이미 등록된 Host Tool의 선언용 ToolRegistry이며 선택 사항이다.
없으면 저장된 Project Tool의 메타데이터만 조회한다. Tool source를 import하거나
prepare하지 않고, handler의 실행용 validate/prepare/execute도 호출하지 않는다.

반환 타입은 `llm.components.workflows.WorkflowInspection`이다.
`diagnostics`는 공통 Diagnostic이고 details에 JSON pointer `path`, 진단 Owner,
가능한 `node_id`, `edge_index`, `workflow_path`가 있다. `references`는 조회한 정의의
ResourceRef이며 version은 해당 저장 Owner의 snapshot revision 또는 Host ToolContract revision이다.
`unverified`는 runtime_authorization/provider_availability/dynamic_inputs 및
스키마·참조 선언·revision이 없는 항목이다. **valid=True는 정적 오류가 없다는 뜻이며 실행 허가가 아니다.**

순수 helper `inspect_graph(definition, *, handlers, read_reference=None)`도 제공한다.
읽기 callback은 record-level ResourceTarget을 받아 `InspectedDefinition(data, revision)`을
반환한다. revision을 확인할 수 없으면 None을 명시한다. schema와 private 설정의 의미는
선택된 handler가 소유한다. `describe_config()`와 `definition_references(node)` 선언을
재사용하며, 선언이 없는 사용자 handler를 UI 편의를 위해 실제 실행하지 않는다.

한 검사 안에서는 같은 참조 snapshot을 공유한다. 검사 사이의 영속 검증 캐시나 validated
플래그는 만들지 않는다. 수집 가능한 독립 오류를 반환하지만 구조 검사 하나에서 최초 오류가
나면 모든 파생 구조 오류를 완전히 열거한다고 보장하지 않는다. 임의의 입력 크기·오류 개수
상한은 추가하지 않았다. 비동기 API는 StorageIO의 off-loop 경로를 사용하며, 취소 시 실행이나
저장을 남기지 않는다. 이미 진행 중인 동기 검증의 종료까지는 기존 StorageIO 정리 규칙을 따른다.

초안은 저장하지 않는다. 실제 저장은 기존 save/asave와 expected_version CAS를 사용한다.
실행·승인·재개 시 현재 권한, 예산, 인자 제약, 선택된 Agent/Workflow와 binding 검사는 유지한다.

## Loop의 Tool 호출 사이 추가 지시

한 모델 응답이 A/B/C 호출을 만들었더라도, A가 끝난 뒤 새 지시가 있으면 미시작 B/C를
ToolExecutor에 보내지 않고 다음 모델 요청에서 재판단하게 할 수 있다. A를 취소하거나 재실행하지 않는다.

미시작 호출은 각 원래 call ID에 대응하는 `not_executed` Tool 결과로 닫는다.
이는 실행된 Tool Step이나 실패 효과 영수증이 아니다. Loop checkpoint의 NOT_APPLIED 기록에는
지시 identity와 `not_executed` 근거가 들어가며, 재개 시 그 결과를 재사용한다.
이미 started/waiting/completed인 호출에는 이 생략 규칙을 적용하지 않는다.
사용자 지시는 모델 입력 checkpoint 저장 ACK 뒤에 applied로 확정한다.
빈 pending 확인은 새 빈 선택 checkpoint나 지시 소비를 만들지 않는다.

Graph의 즉시 전달·미시작 노드 예약은 기존 공개 target/route 계약을 그대로 사용한다.
Tool 도중 적용, Provider 응답 도중 내용 교체, 미사용 예약의 새 Run 자동 이전은 추가하지 않았다.

## 호출자 접수 키

선택적 `request_key`는 요청 접수를 중복하지 않기 위한 호출자 키다. Tool effect의
operation_key나 보안 승인 identity를 대체하지 않는다. 생략하면 기존 동작 그대로이며 Backend가
키나 TTL을 임의로 만들지 않는다.

```python
handle = await session.run.submit("요청", recipients=["assistant"], request_key=utterance_id)
receipt = await session.run.get_receipt(utterance_id)
# receipt is None: 이 Session에서 접수 증거가 없음. 조회 예외는 미접수가 아니다.
# 같은 key와 동일한 내용/대상으로 재접수하면 기존 Message를 반환한다.
```

`session.run.steer(..., request_key=...)`, `reserve_instruction(..., request_key=...)`,
`RunHandle.respond(..., request_key=...)`도 같은 원칙을 따른다.
결과 `InputReceipt`는 호출자 key, project_id/session_id, kind, fingerprint, message_id,
선택적인 run_id 및 interaction_id/revision을 포함한다. 일반 요청은 수신 Agent별 Run이
나중에 생기므로 접수 영수증이 특정 Run을 선택하지 않는다.

기존 ConversationStore의 Message metadata에 접수 결과와 같은 record transaction으로
저장한다. 조회 map에는 Project/Session/key의 hash를 사용하며 원래 key도 영수증에 보존한다.
key는 Project/Session 범위이며 다른 내용·대상·응답에 재사용하면 실패한다.
승인 응답 중복 조회는 실제 저장된 InteractionResponse까지 확인한다. policy/user 승인 저장은
여전히 explicit resume 이전이고, 영수증만으로 실행·권한 확대·Tool 효과 재시도는 허용하지 않는다.
soft-deleted message도 중복 조회에 포함한다. 영구 삭제/retention 뒤까지 별도 영수증 원장을 유지하지 않는다.
file 저장은 재시작 후 조회할 수 있고 memory 저장은 프로세스 종료 시 소실된다.
호출자는 결과가 불확실하면 같은 키로 조회해야 하며 조회 실패를 미접수로 취급하면 안 된다.

## 프로세스 관찰

Builtin ComponentData에 아래 읽기 API가 있다.

```python
builtin = await project.components.aget("builtin")  # 실제 등록 name 사용
observations = await builtin.alist_processes()
page = await builtin.aread_process(
    process_id, stdout_offset=0, stderr_offset=0, max_chars=500,
)
```

현재 명시된 output 목적지에 bound된 native 세션만 runtime 관찰로 반환한다.
저장된 완료 출력은 retained로 구분한다. 이미 생성된 observer만 열거하며 새 toolkit이나
OS process를 만들지 않는다. 현재 owner가 다르면 거부한다. 재시작 후 PID가 같아도
프로세스를 되살리지 않고 기존 저장 출력만 읽는다. 문자 cursor와 Data의 byte offset을 혼동하지 않는다.
허용 범위·출력 정책·삭제 보호는 기존 소유자가 유지한다. 관찰은 Tool 실행 권한이 아니다.

## 선택적 음성 Provider

`llm.providers.audio`는 AudioFormat/AudioChunk/Transcript, 순서·종료 검사,
Transcriber/SpeechSynthesizer 계약을 제공한다. PCM은 signed 16-bit little-endian이다.
마이크·스피커와 VAD, 자동 전송/확인, 읽기 시작 시점, 문장 분할 규모는 Application 책임이다.

| 구현 | 설정 소유자와 제약 |
| --- | --- |
| `local_speech.WhisperTranscriber` | 설치된 model_path, max_audio_frames, partial_frames 필수. model_options/transcription은 SDK에 전달. 입력은 16kHz mono. |
| `local_speech.PiperSynthesizer` | 설치된 voice_path 필수. load_options/synthesis는 SDK에 전달. 출력 형식은 모델이 제공. |
| `server_speech.RealtimeTranscriber` | 명시적 ws/wss URL과 transcription.model 필수. OpenAI Realtime transcription 프로토콜, 24kHz mono. connection_options는 WebSocket SDK에 전달. |
| `server_speech.ServerSynthesizer` | client_options.base_url, speech.model/voice 필수. OpenAI 호환 streaming speech endpoint, 24kHz PCM. SDK 설정은 그대로 전달. |

일반 chat-completions 호환만으로 음성 API 지원을 가정하지 않는다. 설치·서버 선택·모델 다운로드를
자동 수행하지 않는다. 로컬 모델은 이미 존재해야 하며 faster-whisper의 local_files_only=True는
외부 다운로드를 막는 adapter 불변식이다. PCM 형식과 client의 한 발화당 commit은 프로토콜 불변식이다.
일반 SDK 옵션을 복제한 timeout/retry/model/device 기본값은 추가하지 않는다.

SDK import/로컬 추론은 off-loop로 수행한다. 호출 admission은 기존 ProviderCalls scope를 공유한다.
speech_admission은 추가 retry를 하지 않고 일반 SDK 오류는 기존 ProviderError 분류/진단을 사용한다.
취소는 취소로 유지한다. 동기 native 모델 kernel은 즉시 선점할 수 없으며 종료까지 기다린 뒤
iterator와 자원을 정리한다. 실제 취소 반응 시간은 모델/장치에도 의존한다.

진단은 operation, 상태, 경과·첫 chunk 시간, chunk 수, 제공된 usage만 남긴다.
미제공 usage는 None이며 0 token/0 cost로 계산하지 않는다. 오디오나 transcript 본문을 기본 로그에
넣지 않는다. SDK 상세 실패는 기존 명시적 ProviderDiagnostics 정책을 따른다.
음성 연결 자체는 새 Run/Step을 만들거나 승인 응답을 자동 생성하지 않는다.

장치 연결과 사용 예제, 실제 검증 범위는 [Hub 기능 연결](../hub/interactive-features.md)에 정리한다.
