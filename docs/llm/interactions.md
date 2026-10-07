# 공통 사용자 요청과 승인

Project policies.tools.argument_constraints의 effective 값도 Tool 승인 action과 Engine checkpoint binding에
포함한다. fixed 인자는 승인 전에 실제 호출 인자로 확정한다. constraint가 변경되면 같은 인자가
계속 유효해도 이전 durable 승인을 재사용하지 못한다. 기존 InteractionRequest/Response,
ToolExecutor, receipt 소유 관계는 유지하며 별도 승인 엔진을 만들지 않는다.
[인자 제약 계약](tool-constraints.md)을 참고한다.

`llm.core.interactions`의 `InteractionRequest`, `InteractionOption`,
`InteractionResponse`는 `llm.llm`에서도 공개한다. ish에서는
`plugin.get("llm").InteractionRequest`와 같이 접근할 수 있다.
새 실행 도메인을 추가하지 않으며 요청과 응답은 기존 Run에 귀속된다.

| 클래스 | 주요 필드 |
| --- | --- |
| InteractionRequest | id, revision, title, description, kind, category, priority, risk, options, recommended_option_id, input_schema, source, action, binding, created_at, expires_at |
| InteractionOption | id, label, description, effect, value |
| InteractionResponse | request_id, request_revision, option_id, request_fingerprint, value, actor, created_at, policy_id |

`kind`는 approval/confirmation/choice/input, `priority`는 low/normal/high/urgent,
`risk`는 0 이상의 정확한 integer 또는 unknown을 뜻하는 null이다. 숫자는 `risk_scheme`과 함께 해석한다. category는 확장 가능한 문자열이다. label과 구간은 Application이 소유한다.
priority는 UI 표시 우선순위이며 실행 권한이 아니다. 추천 선택지도 승인으로 처리하지 않는다.
`source`는 호출 출처, `action`은 검토할 실행 대상, `binding`은 엔진 재개 위치다.
각 클래스의 JSON 직렬화와 역직렬화에는 `to_dict()`/`from_dict()`를 사용한다
(선택지는 요청 안에 포함되어 함께 직렬화된다).

## UI 사용

```python
# paused_run은 request.wait()로 받은 RunHandle이다.
requests = await paused_run.ainteractions(pending_only=True)
request = requests[0]

# request.title/description/options 등을 렌더링하고 사용자가 선택한 후 호출한다.
response = request.respond("approve")  # 거절: "deny"
await paused_run.arespond(response)

# 응답 저장만으로 Tool이나 Workflow를 실행하지 않는다.
new_request = await session.run.resume(paused_run.id, engine=paused_run.engine)
new_run = await new_request.wait()
```

엔진 이름은 계속 명시한다. UI는 Loop의 bool이나 Graph의 approved/state 형식을
알 필요가 없다. `EngineEvent.interaction`은 체크포인트 저장 뒤 알림으로 전달된다.
재접속/알림 유실 시 `ainteractions()`와 `ainteraction_responses()`를 다시 조회한다.
동기 API는 `interactions()`, `interaction_responses()`, `respond()`다.

요청 스냅샷 자체는 응답 후에도 변경하지 않는다. 요청의 `status="pending"`은
원본 상태이며 현재 응답 여부는 응답 목록으로 확인한다. `pending_only=True`는
현재 뷰의 can_respond가 참인 요청만 반환한다. 응답 완료·만료·취소·이미 재개된 요청과
Run 마감 전의 요청은 제외한다. 이미 재개된 원본 Run에는 응답할 수 없다.

## Tool 승인 연결

```python
from llm.llm import InteractionRequest, InteractionOption, ToolApprovalRequired, ToolPolicy
from llm.components.tools import ToolClassification

async def authorize(call):
    raise ToolApprovalRequired(request=InteractionRequest(
        title="파일 변경 승인",
        category="filesystem.write",
        priority="normal",
        description=f"{call.name} 실행 전 변경 내용을 확인하세요.",
        options=(
            InteractionOption("approve", "승인", effect="approve", value=True),
            InteractionOption("deny", "거절", effect="deny", value=False),
        ),
        recommended_option_id="deny",
    ))

def classify(call):
    # Application이 검토한 예시 분류. 실제 구현은 최종 인자를 기준으로 판정한다.
    return ToolClassification("filesystem.write", "my-app-v1", 40)

policy = ToolPolicy(authorize=authorize, classify=classify, revision="1")
# ServiceConfig(tool_policy=policy)를 백엔드에 전달한다.
```

간단한 경우 `ToolApprovalRequired("확인이 필요합니다")`도 같은 클래스로 변환한다.
ToolExecutor가 실제 호출의 source/action을 채우므로 표시용 요청이 Tool 인자나
호출 출처를 덮어쓰지 못한다. Tool 승인 선택지는 bool 값과 일치하는 approve/deny
effect를 사용하며 승인 응답으로 실행 인자를 변경할 수 없다.
category/risk_scheme/risk도 최종 ToolClassification으로 덮어쓴다. 표시용 요청에
위험값을 넣는 것으로 신뢰 분류를 대체하지 않는다.
이미 효과가 발생할 수 있는 Tool 핸들러 내부에서 승인 예외를 발생시키는 것은 계속 거부한다.

Graph의 `pause_before`는 confirmation 요청, ToolNode와 Loop의 승인 예외는
approval 요청이 된다. 중첩 Agent 요청은 부모 Graph의 경로로 연결된다.
confirmation은 **노드를 계속 진행한다는 확인**이며 Tool 실행 권한이 아니다.
ToolNode는 확인 이후에도 호스트 ToolPolicy.authorize를 거친다. 호스트가 거절하면
`tool_denied`로 실패하고, ASK이면 별도의 approval 요청으로 다시 PAUSED가 된다.
Tool approval에 응답한 뒤 명시적으로 재개해야 효과/실행 worker가 시작된다.
두 요청이 같은 노드 키를 사용해도 원본 Run·요청 ID·action은 구분된다.
Graph 확인 노드에 `resume_schema`를 정의한 경우만
`request.respond("approve", value={"review": "accepted"})`처럼 검토 입력을 전달한다.
공통 클래스의 choice/input은 사용자 정의 엔진에서도 사용할 수 있는 데이터 계약이며,
엔진은 자신의 체크포인트와 재개 값에 대한 검증을 제공해야 한다.

## 저장과 검증

요청은 `Run/state/checkpoints/<엔진>/records/<키 해시>.json`의 waiting 레코드에
`interaction`으로 포함된다. 응답은 `Run/state/interactions/<request_id>.json`에
원자적으로 저장한다. 별도 대화 사본이나 Project 결과 파일은 만들지 않는다.
파일 대화 모드에서 백엔드를 재시작해도 응답을 읽고 명시적으로 재개할 수 있다.
메모리 대화 모드는 기존과 같이 프로세스 종료 뒤 원본 대화가 없어 재개할 수 없다.

- 요청 ID/버전/지문이 달라지면 오래된 UI 응답을 거부한다.
- 같은 응답 재전송은 멱등이며, 다른 선택으로 이미 저장한 응답을 변경할 수 없다.
- 응답 저장과 재개 큐 등록은 소유권 잠금 안에서 수행한다.
- 만료는 응답, 재개 접수, 큐에서 실행 시작 시 검사한다.
- 저장한 답과 저수준 `resume(decisions=...)`가 충돌하면 거부한다.
- 기존 엔진의 설정/Tool 계약/호스트 정책 revision 검증을 그대로 적용한다.
- 추천 선택과 risk/priority만으로 자동 승인하지 않는다. 선택적 Project approval 정책도
  같은 risk_scheme의 Project 규칙만 적용하며 미설정이면 자동 승인하지 않는다. Host 기술적 거절은 우회하지 못한다.

Workflow `pause_before`를 입력 변경 없이 직접 `resume()`하는 기존 명시적 확인 방식도
유지하며 이 경우에도 확인 영수증을 남긴다.

현재 UI 상태는 `ainteraction_views()`로 조회한다. 취소/갱신, 재개 미리보기와 자동 승인
설정은 [보강 API 문서](domain-hardening.md)를 참고한다.

## UI 알림과 저장 완료 경계

- Engine CHECKPOINT/PAUSED 알림의 interaction은 이미 저장된 요청이다. 하지만
  Engine PAUSED 직후에는 Run 마감이 진행 중일 수 있다. 응답 버튼은 Run의 PAUSED
  알림 또는 `ainteraction_views().can_respond`를 확인한 뒤 활성화한다.
- `arespond`/`acancel_interaction`/`arenew_interaction`의 INTERACTION_CHANGED는
  트랜잭션 확정 후 예약한다. rollback된 변경은 알리지 않는다. metadata의
  `interactions`는 변경 당시의 InteractionView 사본이며 최신 상태 보장은 재조회로 한다.
- 응답 저장과 실행 재개는 별개다. `answered`는 아직 실행되지 않은 승인이다.
  재개 후 원본 요청은 `submitted`가 되고 `execution_status`로 새 Run 상태를 표시한다.
- UI는 engine 채널과 run 채널을 함께 구독한다. 재개/실행 종료는 Run 알림을 받아
  원본 Run의 `ainteraction_views()`를 다시 읽는다. 모든 상태 변경마다
  INTERACTION_CHANGED가 발생하는 것은 아니다.
- `delivery="queued", overflow="drop_oldest"`는 알림을 유실할 수 있다.
  구독의 `stats["dropped"]` 증가 또는 재접속 시 요청/응답/뷰/Run/Steps를 다시 조회한다.
  출력은 `aoutput_events(after=sequence)`로 복원한다. sequence는 출력 저널 전용이며
  승인·Run 알림의 공통 커서가 아니다. `events.flush()`도 저장 완료나 Run 완료 API가 아니다.

구독자는 알림을 UI 큐에 전달하는 관찰자다. 콜백 안에서 같은 Run의 완료/재개를 기다리지
말고 UI 동작에서 `arespond()`와 `session.run.resume()`을 호출한다. 추천 선택지는 자동
응답하지 않는다. 읽기 API의 스냅샷은 조회 시점 기준이며 실시간 알림과의 교차 순서는
요청 ID·Run ID·출력 sequence로 구분한다.

## 재개 값의 공통 해석 경계

기존 `InteractionRequest`가 선택지 값과 추가 입력의 해석을 소유한다.
별도 Manager나 승인 저장 형식은 추가하지 않는다.

- `request.decision_for(option_id, value=...)`: 선택한 option과 입력을 Engine 값으로
  변환하고 입력 schema를 검증한다. `InteractionResponse.decision()`도 이 변환을 사용한다.
- `request.select_decision(decision)`: Engine 값을 저장된 유일한 option과 추가 입력으로
  분리한다. Loop bool과 Graph approved/state를 별도 사전 규칙으로 추측하지 않는다.
- `same_interaction_value(left, right)`: JSON-safe 값을 비교하며 중첩된 `true`/`1`,
  `1`/`1.0`을 구분한다. 저장된 응답 충돌과 Tool 인자 연결에도 같은 비교를 사용한다.

이 메서드들은 순수 해석이며 승인이나 응답 영수증을 만들지 않는다. UI는 계속
`request.respond()`와 Run의 `arespond()`를 사용한다. 만료·취소·갱신·요청 지문과
저장 응답 충돌은 서비스/InteractionResponse가 검증하고, Engine은 자신의
binding·재개 값 형식·`retry_nodes`를 검증한다. ToolExecutor의 승인 전 효과 금지,
operation receipt, retry, Step lifecycle 책임도 그대로 유지한다.

응답 누락, 거절(`False`), 빈 confirmation(`{}`)은 서로 다르다. 빈 confirmation의
승인 해석은 명시적 확인 경로의 `confirm_empty=True`에서만 허용한다. 일반 approval과
choice에는 적용하지 않는다. 여러 option에 동일한 값이 있으면 저수준 raw decision은
모호하므로 거부한다. UI 응답 생성은 기존처럼 option ID를 사용하며, raw decision을
사용하는 실행 전략은 선택지 값을 유일하게 정의해야 한다.

중첩 Agent의 체크포인트 bridge는 부모 경로와 자식 원본 요청의 연결을 검증한 뒤
선택지 **ID**로 자식 값을 복원한다. 부모 값에서 무조건 `approved`를 꺼내지 않는다.
서비스가 갱신된 요청의 응답을 검증하므로 bridge는 원본 체크포인트의 옛 만료 시각을
다시 승인 기준으로 삼지 않는다. 부모/자식 요청과 source Run은 변경하지 않는다.
Graph의 기존 approved/state 계약과 중첩 Tool 승인에서 부모 state 편집 금지는 유지한다.
