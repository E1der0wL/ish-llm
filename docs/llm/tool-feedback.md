# Tool 결과 처리와 비차단 질문

Tool 실행 사실은 ToolExecutor/Adapter, 다음 행동은 선택한 Engine, 제품 정책값은
Application이 소유한다. 오류 코드를 실행 권한이나 자동 재실행 허가로 사용하지 않는다.

## 실패와 거절

Loop의 명시적인 `policy.tool_feedback` 설정은 `failure`와 `declined` boolean,
`remaining_calls` (`defer`/`continue`)를 받는다. 피드백을 활성화하면 남은 호출 처리 방식을
반드시 명시한다. 미설정은 기존 오류 전파를 유지한다. `failure=true`는 예상 가능한
ToolExecutionError 중 전체 시도에 효과가 없다고 확인된 실패만 모델에 전달한다.
기존 retry/인자 수정은 먼저 끝나며 `retryable`/`recoverable`의 의미는 바뀌지 않는다.
취소·방향 전환·한도·Host 기술적 거부·저장 실패·알 수 없는 예외는 이 허가로 삼키지 않는다.

실패 Step은 FAILED 그대로다. Engine은 해당 실패 결과와 체크포인트를 저장한 뒤
공통 실패 처리 이벤트로 책임을 인수한다. 처리 기록이 없는 FAILED 또는 미완료 Step은
Run 완료를 막는다. 임의 metadata로 실패를 처리했다고 주장할 수 없다.

승인 거절은 효과가 발생한 실패가 아니라 미실행 결과다. 응답 저장은 실행이 아니다.
명시적 재개는 새 Run이며, 선택한 Engine이 거절 결과 처리를 지원할 때만 모델 피드백으로
소비한다. 기존 승인 binding/분류/제약 검증과 불확실 효과 재실행 금지는 유지한다.
모델이 제안한 새 호출은 다시 검증·승인한다. 거절을 영구 정책으로 자동 저장하지 않는다.
소비 Engine이 `InteractionBinding.resume_on_denial`을 선언하면 UI의 재개 계획도 거절을
입력으로 처리할 수 있음을 표시한다. 선택지는 계속 deny이며 이 선언은 실행 허용이 아니다.
중첩 Graph는 이 공통 선언만 전달하고 자식 Loop 정책을 해석하지 않는다.

`remaining_calls=defer`를 선택한 경우만 아직 시작하지 않은 나머지 호출을
미실행 결과로 닫고 모델에 재판단을 맡긴다. `continue`는 이미 요청된 다른 호출을 각각
검증·승인하여 진행한다. Backend가 처리 순서를 고정하지 않는다. 성공한 작업과 STARTED/WAITING 증거는
되감지 않는다. 호출 ID별 결과를 보존하며 재개 시 처리한 결과는 재사용한다.
사용량과 iteration은 초기화하지 않고 기존 명시적 한도를 사용한다.

## 질문

질문은 오류나 승인으로 취급하지 않는다. 등록자는 대기/비차단 방식을 명시한다.
비차단 질문의 Tool 결과는 등록 사실과 질문 ID이며, 나중에 온 답변으로 과거 결과를
수정하지 않는다. 질문 출처는 생성 Run/Step, 답변 기록은 공통 Interaction owner가 소유한다.
비차단 질문은 실행 중 및 완료 후에도 응답 가능하며 Run을 PAUSED로 만들지 않는다.

응답 저장과 전달은 분리한다. Application은 현재의 명시적 Engine 대상에 steer하거나
다음 submit에 답변을 연결한다. 대상 종료 시 다른 실행으로 자동 이전하지 않는다.
저장·접수·입력 반영 증거를 연결하여 중복 응답/전달과 완료 경합을 검증한다.
Run 완료는 질문 취소가 아니며 완료 Run을 다시 실행하지 않는다. 승인 응답은 이 경로로
변환하지 않는다. 추천값·무응답·시간 경과를 사용자 응답으로 만들지 않는다.

질문 취소와 삭제는 명시적으로 처리한다. 원본 대화/Run 참조 없이 질문을 실행 근거로
복원하지 않는다. 파일/메모리 대화 저장소의 기존 재시작 보장 차이는 유지한다.
Application이 표시·전달 대상·실행 시작·명시적 만료를 선택하며 Backend는 숫자 기본값이나
자동 실행 정책을 만들지 않는다. Graph는 질문의 실제 Agent 출처를 유지하며 방송하지 않는다.

## 공개 API 사용

아래는 Application이 선택하는 예시다. Backend가 이 값을 생성하거나 저장하지 않는다.

```json
{
  "parameters": {
    "engines": {
      "loop": {
        "policy": {
          "tool_feedback": {
            "failure": true,
            "declined": true,
            "remaining_calls": "defer"
          }
        }
      }
    }
  }
}
```

Host는 `llm.components.tools.QuestionHandler(factory, mode=..., revision=...)`를
일반 `Tool`의 handler로 등록할 수 있다. mode는 `blocking` 또는 `nonblocking`을 반드시
지정한다. 동기 factory는 인자 사본에서 `InteractionRequest(kind="input"/"choice", ...)`를
만들며 Tool의 이름·인자 schema·표시 내용·명시적 만료는 Host/Application이 소유한다.
질문 옵션은 `select`만 허용하고 approval/deny 권한을 넣을 수 없다.
handler는 기존 resumable container 계약을 사용하므로 business operation key를 붙이지 않는다.

`blocking`은 기존 `run.interactions()` → `run.respond()` → 명시적 `session.run.resume()`을
사용한다. `nonblocking`은 질문 ID를 Tool 결과로 반환하며 다음 공개 API를 제공한다.

```python
views = await run.aquestions()  # 출처 Run/Step, pending/answered/expired/cancelled
question = views[0].request
await run.aanswer_question(question.respond("answer", value="src/main.py"))

# Application이 전달 내용·시점·대상을 선택한다. 둘 중 하나만 선택한다.
instruction = await run.deliver_answer(question.id, "파일은 src/main.py입니다.",
                                      mode="steer", run_id=active_run.id, targets=target_ids)
# 또는: queued = await run.deliver_answer(question.id, text,
#                                       mode="submit", recipients=agent_ids)
receipt = await run.get_delivery(question.id)
```

접수 receipt는 기존 Conversation의 client-input 멱등성을 재사용한다. 동일 질문 revision은
한 번만 전달되며 같은 내용/대상 재전송은 기존 receipt를 반환한다. 다른 내용/대상으로 재전송하면
실패한다. `get_delivery`는 접수 여부이고 실제 반영은 반환된 request/steering 상태로 조회한다.
새로운 실행 이력이나 delivery 큐를 만들지 않는다. 선택한 대화 저장소가 memory라면 이 접수
receipt도 shutdown 후 사라진다. Run에 저장된 질문/답변 자체는 기존 Run 저장 수명을 따른다.
원본 요청/Assistant 메시지가 없거나 대화가 삭제되었다면 늦은 전달은 거부한다. memory 저장소의
재시작 후 과거 답변을 새로운 입력처럼 다시 접수하여 멱등성을 잃지 않도록 하기 위한 경계다.

미응답 질문은 `run.acancel_question(question)`으로 취소할 수 있다. 이미 저장된 답변은
불변이며 전달하지 않는 선택은 Application이 한다. 만료는 새 응답 접수를 막지만 이미 저장한
답변을 지우지 않는다. 질문 파일과 Run의 질문 manifest가 다르면 무결성 오류로 처리한다.
Run/Session 백업·삭제는 기존 소유 subtree를 사용한다. 질문이 있다는 이유로 완료 Run의
재개를 만들거나 삭제를 추가 차단하지 않는다. 질문을 갱신하려면 새 질문을 명시적으로 등록한다.

## 검증 경계

실패와 거절의 보존, opt-in 여부, 실제 무효과 확인, 처리되지 않은 실패 차단,
완료 결과 재사용, 승인 binding 변경, 저장 실패, 취소, 다중 호출, 중첩 Agent,
질문 중 계속 실행, 늦은 응답, 중복/상충 응답, 전달과 종료 경합, 재시작과 삭제를 검증한다.
코드가 확인하지 못한 효과를 모델 추측으로 무효과 처리하지 않는다.
