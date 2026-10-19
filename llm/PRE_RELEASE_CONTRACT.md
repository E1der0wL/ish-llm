# 출시 전 단일 계약 (R1 설계)

이 문서는 R2~R5 구현의 목표 계약이다. 실제 구현·검증 상태는 감사표와 최종 보고서를
따른다. 이전 개발 버전 API/저장 자료는 지원하지 않으며 자동 변환하지 않는다.

## 소유 관계와 호출

Project가 Agent 정의·revision을 소유한다. Project→Sessions→Session은 Messages와
Runs를 소유하고 Run은 Steps를 소유한다. 호출은 Run→선택 Agent→Engine→Provider다.
Graph/agent_run 자식은 같은 Run 안의 Step이다. AgentComponent/AgentData/자동 legacy
Agent는 없다. 실제 agent_run Tool은 Backend의 명시적 등록 기능으로 유지한다.

## 모델·처리기·Tool

- CompletionAdapter: 입력 `ModelRequest`, Decoder 최종 결과 `ModelResponse`, 중간
  `CompletionUpdate`. marker 없는 구현도 같은 typed 계약을 구현해야 한다. dict를
  성공 처리하는 자동 감지·fallback은 없다. Adapter가 options schema/검증/retry를 소유한다.
- BaseEngine/LoopEngine/Memory에는 `completion_adapter=`만 주입한다. SDK callable을
  시험해야 한다면 실제 `LiteLLMCompletionAdapter` 안에서 주입한다. OperationAdapter도
  complete/embed/rerank의 typed 결과만 반환하며 raw SDK 호출은 Provider의 private 구현이다.
- SDK wire Codec의 encode는 typed 입력만, decode는 SDK 입력만 받는다. 공통 모델
  저장의 from_dict와 SDK decode는 서로 다른 경계다. opaque·서명·미완료 인자·usage의
  unknown 의미를 보존한다. 소비자는 정규 stop/termination을 해석한다.
- `CompletionMessage.message`는 ModelMessage다. dict 생성자/`.value`는 없다.
  `CompletionRequest.to_model()`이 유일한 입력 변환이다. parameters 안 messages/tools
  우회는 거부한다. Observation은 invocation/answer/messages만 제공한다.
  Recovery도 ModelRequest 사본과 editable index만 제공한다. 원본 지시·Tool 권한·출처
  검사와 Processor별 사본 분리는 그대로 유지한다.
  Adapter의 `validate_input_ownership()`은 SDK 옵션을 통한 입력·Tool 우회를 검사한다.
  `auxiliary_parameters()`는 별도 분석 요청에서 Tool·출력 형식·SDK 자동 retry 옵션을
  제거한 사본을 만든다. Memory와 Hub recovery는 선택 Adapter의 이 계약을 사용한다.
  CompletionPipeline은 선택 Adapter의 validate_processing을 명시적으로 받는다.
  독립 처리기 사용자는 자신의 검증 함수를 지정한다. 기본 SDK 검증을 추측하지 않는다.
- Counter는 ModelRequest를 받고 nonnegative int를 반환한다. Memory 요약 digest와
  저장 payload는 공통 메시지의 현재 JSON을 기준으로 한다.
- Tool.definition은 ToolDefinition, Registry.definitions()는 typed tuple이다.
  prepare는 dict 인자만 받고 문자열 JSON은 Provider가 완성·검증한 뒤 전달한다.
  Worker/Graph는 같은 typed 정의의 현재 JSON을 사용한다. ToolContract, classification,
  승인 binding, operation key/효과 영수증, 중복 실행 거부는 삭제 대상이 아니다.
- BaseEngine.stream_completion은 ModelRequest와 선택적 `list[ModelResponse]` capture를
  받는다. dict response나 kwargs request는 거부한다. 부분 스트림 이후 자동 retry 금지,
  close/cancel/timeout/usage admission은 유지한다.

## Agent·Session·Run

```python
identifier = await project.agents.acreate({"engine": "loop"}, identifier="chat")
await session.agents.aattach(identifier)
request = await session.run.submit("요청", agent_id=identifier)
run = await request.wait()  # 정확히 한 recipient일 때만 허용
reply = await run.aresponse()

request = await session.run.submit("각자 검토", recipients="all")
runs = await request.wait_all()
known_runs = await request.aruns()
snapshot = await project.agents.asnapshot(identifier)
await project.agents.arevise(identifier, {"engine": "loop", "purpose": "검토"},
                            expected_revision=snapshot["revision"])
```

Agent의 유일한 필수 실행 설정은 engine이다. 모든 Engine은 동기·무효과
`for_agent(definition)`으로 호출별 실행기를 만든다. BaseEngine은 최소 정의를 처리하는
정규 구현을 제공하고, 옵션을 해석하는 사용자 Engine은 이를 명시적으로 구현한다.
구 `for_request/resolve_request` fallback은 없다.

submit은 agent_id/agent_ids/recipients 중 정확히 하나를 받는다. 수신자는 Session에
attach되어 있어야 한다. 생략·engine=·engine_options=는 거부한다. 하나의 User를 형제
Run들이 input_message_id로 참조한다. User.run_id 단수 투영은 없고 Assistant.run_id는
유지한다. Session은 agent_ids/current_run_ids, RuntimeView는 active_run_ids만 제공한다.
Request의 run/result/aget_run/aresult 단수 투영은 제거하고 runs/aruns/wait_all을 쓴다.
wait는 제품 편의 기능으로 유지하되 다중 수신자는 거부한다.

Resume/Restart는 저장한 Agent/Engine identity를 사용하며 engine= 재지정을 받지 않는다.
Agent/Engine/resource/revision 변경, 승인 불일치, 소유권·digest 손상은 계속 거부한다.
같은 Agent FIFO, 다른 Agent 병렬, max_parallel_runs, 미시작 증거가 있는 요청만 자동
복구하는 규칙을 유지한다. 불확실한 Tool 효과는 자동 재실행하지 않는다.

## Hub의 현재 요청 문맥

Hub의 Engine/Workflow 선택은 관리 가능한 **실제 Project Agent 정의**와 Session 참가
참조로 표현한다. submit 시 engine=를 숨겨 번역하는 factory를 만들지 않는다.
기존 Agent 관리 화면/Tool도 Project.agents schema·revision/CAS를 직접 사용한다.

현재 셸 environment/turn snapshot은 요청마다 달라지므로 Agent 정의에 덮어쓰지 않는다.
기존 engine_options 안의 이 두 호스트 자료를 정규 `submit(..., metadata={...})`의
별도 application 영역으로 옮긴다. 입력은 JSON 사본이고 reserved admission/transfer/
Agent binding을 덮을 수 없다. User journal에 접수와 함께 확정하고 Run의 동결 문맥과
binding에 반영한다. Resume/Restart는 원래 값을 보존한다. 이 재배치는 기존 셸 환경
기능을 보존하기 위한 것으로 새 Engine 선택·승인 권한 부여 경로가 아니다.
Hub EnvironmentEngine은 선택 Agent를 실행하는 명시적 factory이며 저장 요청 문맥을
읽는다. 모델 Tool 입력으로 호스트 environment 또는 admission을 바꿀 수 없다.

## 저장·안전성·검증

Agent envelope는 한 가지 현행 형태(id/definition/revision/storage_version)만 받는다.
Run/Session은 breaking cleanup의 현재 저장 버전으로 생성하며 이전 버전, 빠진 필드,
extra 필드, Agent 없는 Run을 명확히 거부한다. version 숫자는 무결성 header로 유지한다.
Agent envelope/binding은 버전 2, Run/Session은 버전 3을 사용한다. RequestAdmission은
현재 유일한 버전 2를 유지한다. 형태를 바꾸지 않는 영수증·Step·공통 모델의 현재 버전은
불필요하게 올리지 않으며, 각 reader는 정확히 한 버전만 허용한다.
Checkpoint/ModelMessage/Tool descriptor는 각자의 단일 현재 encoding만 처리한다.
과거 파일을 읽으며 수정하거나 migration/upgrade를 수행하지 않는다.

새 fixture는 현재 구현 SHA에서 생성하며 archive/file hash와 생성 절차를 고정한다.
구형 성공 판독 테스트를 제거하기 전에 동등한 승인 대기·Graph·Tool receipt 재사용·
실제 효과 직후 프로세스 종료·fsync/부분 JSON/digest 실패 테스트를 확보한다.
F01~F14/C01~C12는 현재 경계에서 계속 실행한다. 검토 훅의 accept/continue/block,
internal 후보, bounded attempt/timeout/usage, 저장 결정 재사용도 그대로 유지한다.

GitHub CI는 portable/host-ui/sandbox 결과를 구별한다. 같은 Runner에서 실제 격리가
입증되면 통합할 수 있지만 보안 게이트 실패가 일반 회귀 실행을 가리게 하지 않는다.
모든 발견 테스트 ID가 한 실제 게이트에 포함되는지 감사한다. network 격리 해제,
process 강등, Skip 확대, sudo bwrap 대체는 허용하지 않는다. 로컬 성공과 Actions
성공은 별도 증거다. 실제 인증 모델 및 커널 short-write 주입은 수행하지 않으면 미검증이다.
