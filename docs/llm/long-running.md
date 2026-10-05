# 장시간 실행·복구·보관 API

최신 보강: 독립 RAG 호출의 공통 사용량 예약, Run 단위 보관 정리/중단 복구,
Component maintenance 및 UI 동적 설정 스키마는 [운영 및 UI 설정](operations-and-ui-settings.md)에 설명한다.

정책값은 ProjectConfig, 컴포넌트 처리 설정은 해당 Component, 실행과 저장은 기존
RunManager/Repository가 담당한다. 새 실행 도메인이나 별도 Policy 클래스는 만들지 않는다.
기본 Session 동시 개수·Run 전체 시간·Project 사용량·보관 기간/용량은 무제한이다.
공급자 호출 슬롯과 개별 모델/Tool 실행 한도는 별도 자원 보호 계약으로 유지한다.

검증 목표는 평균 동시 Session 5개, 작업 3시간, 원본 기술문서 2GB다. 자동 회귀 테스트는
이 크기의 실제 운영 시험을 대신하지 않는다. 문서 색인/벡터/관계/작업 입력은 원본보다
추가 저장 공간을 사용하며, 2GB 코퍼스의 최대 RAM·지연 시간은 측정 후 판단해야 한다.

## Loop 체크포인트와 명시적 재개

```python
run = await (await session.run.submit("작업", engine="loop")).wait()
checkpoint = await run.acheckpoint("loop")
# 완료 여부가 불확실한 Tool은 외부 결과 확인 뒤에만 명시적으로 재실행한다.
uncertain = [key for key, value in checkpoint["records"].items()
             if key.startswith("tool:") and value["status"] == "started"]
request = await session.run.resume(run.id, engine="loop", retry_nodes=uncertain)
continued = await request.wait()
```

`iteration:N`은 모델 응답과 회차 완료를, `tool:N:call_id`는 개별 실행 상태/결과를 저장한다.
정상 반환 `None`도 JSON null 결과로 확정한다. 완료 응답과 Tool 결과는 재사용하고 다음 모델
호출로 이어간다. 모델 응답 저장 전에 중단되면 해당 모델 호출을 다시 수행할 수 있다.
실행된 Tool의 완료 기록을 쓰지 못했다면 자동 재실행하지 않는다. 재개는 새 Run이며 원본은
보존한다. 기존 Session 작업 키 원장도 계속 검사하므로 불확실한 외부 효과가 자동으로 풀리지 않는다.
재사용한 Tool은 새 Run의 Step에 reused와 source_run_id/source_step_id를 남기므로 UI와
원본 결과 조회가 같은 API를 사용한다. 실제 핸들러를 다시 호출하지 않는다.

Engine은 CHECKPOINT 이벤트만 생성한다. 파일은 RunRepository가
`runs/<run>/state/checkpoints/loop/`에 저장한다. 과거 대화는 ID로 참조하고 현재 작업의
모델 응답/Tool 결과만 체크포인트에 보관한다. 설정/Tool 정의 fingerprint가 바뀌면 재개를 거부한다.
실행 함수의 코드 변경은 프로젝트 engine 설정의 revision 같은 명시적 값을 변경해 표현한다.
런타임 클라이언트 객체의 내부 상태/함수 코드 자체를 직렬화하거나 동일성을 보장하지 않는다.

Loop 회차 체크포인트는 최상위 Loop와 Graph Agent 내부 Loop에 적용한다. 자식 체크포인트는
부모 Workflow 호출 경로에 연결하며 진행 중인 스트림의 임의 바이트 위치를 복원하지 않는다.
완료된 모델 응답의 after_completion 훅은 재실행하지 않지만 새 처리기 세션을 생성한다.
finish 등 외부 쓰기 훅은 재개/실패를 고려하여 멱등하게 구현해야 한다.

## Tool 조건부 재시도와 영속 승인

```python
from llm.llm import ToolPolicy, ToolExecutionError, ToolApprovalRequired, ServiceConfig

async def authorize(call):
    raise ToolApprovalRequired("이 작업을 승인해 주세요")

async def tool_handler(arguments):
    # 외부 효과가 전혀 발생하지 않았음을 핸들러가 보장할 수 있을 때만 사용한다.
    raise ToolExecutionError("아직 실행하지 못함", effect="none", retryable=True)

services = ServiceConfig(tool_policy=ToolPolicy(authorize=authorize))
await project.aconfigure_policies({"tool_retry": {"max_retries": 2, "delay_seconds": 1}})

# 승인 대기인 Loop Run은 PAUSED로 종료되어 Session worker를 해제한다.
request = await session.run.resume(paused_run.id, engine="loop",
    decisions={"tool:1:call_1": True})  # False는 거절하며 Tool은 실행되지 않는다.
```

일반 예외·timeout·취소는 재시도하지 않는다. retryable=True이며 effect=none이거나,
호스트 ToolPolicy.retry_safe_tools에 해당 이름이 등록된 경우에만 재시도한다.
모델이 안전성을 선언할 수 없다. 재시도는 동일한 논리 호출/작업 키/총 Tool 기한을 사용하고
각 시도 전 Step 업데이트를 저장한다. 정상 결과 검증/직렬화/완료 저장 실패는 재시도 대상이 아니다.
SDK/외부 서비스의 중복 방지는 어댑터가 idempotency_key를 실제 전달해야 한다.

승인 결정은 재개 요청과 Run에 저장된다. 승인 중인 callback Future를 복원하지 않는다.
기존 awaitable authorize도 사용 가능하며, 영속 대기를 원할 때 ToolApprovalRequired를 사용한다.
Graph ToolNode도 동일한 승인 대기를 지원하며 decisions의 키는 체크포인트 노드 키다.
Agent 내부 Loop도 완료 응답/Tool 영수증과 승인 대기를 부모 Graph에 저장한다.
경로에는 Workflow·노드·분기/반복 위치·자식 Engine·로컬 호출 키가 포함된다.
UI는 체크포인트에서 `approval_required`인 레코드를 조회하고 그 키로 승인한다.
키를 직접 조립하지 않는 것이 좋다.

```python
checkpoint = await paused_run.acheckpoint()
pending = {key: value for key, value in checkpoint['records'].items()
           if value.get('approval_required')}
# UI에서 각 pending 레코드의 name/arguments/prompt를 검토하고 선택한 결정만 전달한다.
await session.run.resume(paused_run.id, engine='graph', decisions={
    selected_key: {'approved': True},
})
```

모든 대기 승인에 결정이 필요하며 False는 실행 없이 거절한다. 자식 승인으로 부모 입력을
수정할 수 없다. Agent의 누적 Tool 한도는 복원하고 이미 예약한 승인 대기를 이중 계수하지 않는다.
효과가 불확실한 자식 Tool은 정확한 경로를 retry_nodes로 승인해야 한다. 완료된 다른 호출은
반복하지 않는다. EngineCheckpointScope 계약이 없는 사용자 Engine은 기존 노드 경계를 따른다.

작업 키를 사용하는 Tool이 `effect='none'` 오류로 끝나면 원장에 `not_applied`를 저장한다.
명시적 재개는 이전 시도 영수증을 보존하고 새 시도를 시작할 수 있다. 앞선 재시도에서 효과가
불확실했거나 영수증 저장에 실패했다면 이 상태로 완화하지 않는다.

## Workflow 검토 후 상태 수정

```python
# 대기 노드 정의에 허용할 수정 필드를 선언한다.
node = {"type": "review", "pause_before": True,
        "resume_schema": {"type": "object", "properties": {"draft": {"type": "string"}},
                          "additionalProperties": False}}
await session.run.resume(paused.id, engine="graph", decisions={
    '["review"]': {"approved": True, "state": {"draft": "사용자가 수정한 내용"}},
})
```

state 변경은 waiting 노드에서만 허용하며 닫힌 resume_schema로 검증한다. 완료된 노드의
입출력은 그대로 재사용한 뒤 대기 노드 직전에 변경값을 적용한다. 변경 내용은 새 Run의
재개 메타데이터와 체크포인트에 기록한다. LangGraph 임의 프레임 수정 API는 제공하지 않는다.

## 사용량 상한과 공급자 재시도

```python
await project.aconfigure_policies({
    "usage": {"max_calls": 100, "max_tokens": 200000,
              "project_max_calls": 1000, "project_max_tokens": 2000000,
              "period_seconds": 86400, "counter": "model_default"},
})
# 등록 이름이 loop인 Engine만 이 재시도 설정을 사용한다.
config = (await project.aget_data()).config
config.parameters.setdefault("engines", {}).setdefault("loop", {})["provider"] = {
    "max_attempts": 3, "delay_seconds": 1, "max_delay_seconds": 10,
}
await project.asave(config=config)
```

usage 상한은 명시했을 때만 적용한다. Completion 시작 이벤트를 저장하기 전에 상한을 검사한다.
토큰 한도를 켜면 completion.max_tokens 또는 max_completion_tokens를 명시해야 한다.
usage.counter로 선택한 계산기의 입력 토큰 + 출력 상한을 예약하고, 완전한 실제 usage를 받으면 실제 토큰으로
바꾼다. usage가 없거나 중단된 호출은 예약을 유지한다. 과거의 미계수 호출이 집계 범위에
있으면 usage_unknown으로 추가 호출을 차단한다. 계수기와 공급자의 출력 상한 준수가
전제이며 실제 요금 청구의 정확한 상한을 보장하는 결제 원장은 아니다.

프로젝트 집계는 Run의 Completion 원본에서 계산한다. 기본 전체 기간 또는 period_seconds의
이동 창을 사용한다. 변경 감지 캐시로 같은 Run의 JSON 재파싱과 조회 로그 쓰기를 줄인다.
많은 Run의 파일 stat·기간 집계 비용은 여전히 기록 수에 비례한다. 동시 호출은
동일 workspace 잠금으로 예약해 한도를 중복 배정하지 않는다. 재개 Run은 새로운 Run 한도를
갖지만 Project 기간 한도는 공유한다. Project Component API로 호출한 RAG 모델은 컴포넌트
영수증으로 같은 Project 한도에 참여하며, Tool 안의 호출은 현재 Run 한도에도 참여한다.

Engine의 provider는 BaseEngine completion 경로에서 첫 chunk 이전의 일시적인 공급자 오류만 재시도한다.
부분 응답 이후에는 재시도하지 않는다. 각 시도는 별도 Completion ID와 사용량 예약을 가지며
Run 기한 안에서 수행한다. max_attempts는 최초 호출을 포함한다. 명시적 SDK retry가 켜져 있으면
외부 시도는 1회로 제한해 중첩을 막는다. 미설정이면 외부 재시도하지 않는다. Memory 등 다른
호출자의 provider 설정은 해당 Component에서 별도로 지정한다.
동기 SDK 스레드 강제 종료/자동 모델 fallback은 제공하지 않는다.

## 현재 작업 문맥 압축

```python
await project.components.memory.aconfigure({'policy': {'processing': {'summarize': True, 'compact_active': True, 'active_keep_iterations': 2, 'summary_after_chars': 12000}}, 'config': {'processing': {'summary_chars': 3000, 'completion': {'model': auxiliary_model, 'max_tokens': 1500}}}})
```

Memory의 자동 요약은 기본적으로 꺼져 있다. 켜면 현재 작업에서 완료된 오래된 Tool
호출/결과 교환을 요약하고 최근 교환은 그대로 남긴다. 공통 처리기는 제거한 호출 ID와
쌍의 완전성을 검증한다. 원본 대화/Step/체크포인트를 삭제하지 않는다. 이 설정은 해당
컴포넌트 configuration을 교체하므로 다른 설정을 유지하려면 현재 설정과 병합해서 전달한다.

요약 입력과 길이는 제한한다. 과거 대화는 완전한 턴 단위로 처리하며, 현재 Tool 교환은
이전 요약에 완료된 교환을 배치로 합친다. 모델에 전달하지 못한 구간은 원문으로 남긴다.
단일 턴/교환이 한도를 넘으면 자동으로 잘라 없애지 않으므로 설정 조정이 필요할 수 있다.
현재 작업의 요약 캐시는 최신 접두 구간 한 개만 유지한다. 요약은 원문과
동등한 정확성을 보장하지 않는다. memory_tool_result Tool로 현재 Session의 Run/호출 ID에
해당하는 원본 결과를 부분 조회할 수 있다. API 경로·파일명을 모델 인자로 받지 않는다.
압축 후에도 현재 요청이 CompletionPolicy를 초과하면 모델 호출 전에 실패한다.

## 보관·복구·UI 조회

```python
await project.aconfigure_policies({"retention": {
    "max_bytes": 5_000_000_000, "max_tokens": 2_000_000,
    "max_age_seconds": None, "counter": "model_default",
}})
plan = await project.aretention()
# candidates/protected와 예상 용량을 검토한 뒤 명시적으로 적용한다.
await project.aretention(apply=True, expected_version=plan.version)

await session.run.shutdown()
await run.areconcile_output()
await backend.projects.arecover_deletions()
view = await run.aview(after=last_sequence, limit=200)
```

기본 자동 삭제는 없다. 기본 보관 정리는 완료된 비활성 Session 전체 단위이며 오래된 것부터
기간/용량/대화 본문 토큰 조건을 평가한다. 토큰은 API 사용량과 다르다. Tool 작업 원장,
대기/실행/실패/중단/일시정지 Run, retain 메타데이터, 비파일 대화, 현재 Project 사용량
집계에 필요한 Session은 보호한다. 따라서 지정 용량까지 줄일 수 없을 수 있으며 사유를 반환한다.
용량은 Session 도메인 기록/산출물 대상이며 운영 로그는 logger의 회전 설정으로 관리한다.
Memory/RAG 등 프로젝트 컴포넌트 데이터는 이 API로 지우지 않는다. 적용은 다중 Session 원자
트랜잭션이 아니며 중간 실패 시 새 계획을 조회한다.

`retention.unit="run"`은 Session을 유지하고 완료 Run/대화만 정리한다. 이때 보관 삭제 저널이
남으면 새 실행을 막고 `project.arecover_retention()`으로 이미 승인된 정리를 완료한다.
컴포넌트별 자료 정리는 `project.amaintenance()`의 별도 계획을 사용한다.

출력 복구는 저장된 출력 저널을 기준으로 Conversation 본문을 replace 이벤트로 맞춘다.
Run 성공 상태나 Tool 효과를 재현하지 않는다. stale file Run 복구에도 적용되며 사라진
메모리 대화를 새로 만들지 않는다. 삭제는 소유 디렉토리의 .deletions에 의도를 먼저 기록하며
recover_deletions는 비활성 workspace에서 이미 시작한 Project/Session 삭제만 마무리한다.
컴포넌트 내부의 삭제 복구는 소유자가 같은 storage 복구 함수를 사용한다.

view는 같은 잠금 아래 Run 상태·전체 출력·cursor·이벤트 페이지를 반환한다. outputs를
화면의 기준 스냅샷으로 사용했다면 cursor 이후부터 수신한다. 이벤트 페이지를 적용하는
방식을 선택했다면 마지막 적용한 이벤트 sequence를 사용한다. UI 위젯/알림 렌더링은 ish 책임이다.

## 편집 충돌

```python
snapshot = await project.components.workflows.asnapshot("flow")
definition = snapshot["data"]
await project.components.workflows.asave("flow", definition, expected_version=snapshot["version"])
view = await project.aconfiguration()
await project.aconfigure_policies({"context": {"max_turns": 20}}, expected_version=view["config_version"])
```

공통 ComponentData snapshot/save/update/delete/configure와 Project 설정 저장은 선택적인
expected_version을 지원한다. 불일치하면 edit_conflict로 거절하며 열린 JSON 형식은 유지한다.
Memory/RAG처럼 자체 revision 계약을 가진 전용 API는 기존 expected_revision을 사용한다.

## RAG 영속 작업과 증분 색인

```python
rag = project.components.rag
job = await rag.aenqueue_document(title="설명서", content=markdown, identifier="manual")
await rag.arun_job(job["id"])
jobs = await rag.ajobs()
await rag.acancel_job(other_job_id)
await rag.arun_job(failed_job_id, retry=True)
# 또는 현재 queued 작업을 순차 처리한다.
await rag.arun_jobs(limit=10)
```

rag/jobs/<id>/ 아래 입력·작업 상태·준비 결과를 저장한다. 백엔드 시작만으로 모델을 호출하지
않으며 UI/호스트가 worker API를 시작한다. failed 작업이나 죽은 worker의 running 작업만
명시적으로 재개한다. 기본 ingestion.max_active=1은 컴포넌트 configuration으로 조절한다.
워커 생존 여부는 PID가 아니라 OS 파일 잠금으로 판정한다. 같은 프로세스에서 중단된
coroutine도 잠금이 해제되면 명시적으로 재개할 수 있다. 취소 요청은 배치 경계와 공개 전에
검사한다. 모델 호출을 강제 종료하지는 않는다.
준비 완료 문서는 재사용하며, 공개 성공 후 작업 완료 기록만 실패한 경우 동일 문서를 확인한다.

임베딩은 RAGComponent.embedding_batch_size(기본 128), 관계 추출은 extraction_batch_size
(기본 32개 chunk) 단위로 호출한다. 작업의 jobs/<id>/batches에 완료 배치를 원자적으로 저장해
준비 중 실패해도 재사용한다. 배치 입력/설정 해시가 달라지면 새 작업을 요청해야 한다.
모델 응답 이후 영수증 저장 전 장애는 해당 모델 배치를 다시 호출할 수 있다. 사용자 prepare
재정의는 기존 계약을 유지하며 배치 저장을 강제하지 않는다. 완료 후 준비 사본은 정리한다.
새 검색 세대는 기존
Chroma/Kuzu를 복사한 뒤 변경 문서의 벡터/관계만 갱신하고 원자적으로 공개한다. 실패하면
기존 세대가 유지된다. 첫 색인은 전체 생성이다. 사용자 _build 재정의는 그대로 사용한다.
지원하는 Linux 파일시스템에서는 reflink로 DB 데이터 블록을 공유하고 수정 시 분리한다.
미지원 파일시스템은 일반 복사한다. BM25 모델은 불변 세대별로 재사용하며 search_cache_chars
(기본 원문 1,000,000글자, 0은 캐시 끄기)로 컴포넌트의 전체 캐시 한도를 조절한다. 이 값은
실제 RAM 바이트 한도가 아니다. 원본 corpus JSON 읽기/쓰기와 일반 DB 복사 비용, 검색/공개 중
공유 저장 잠금은 남아 있다. 대용량 코퍼스 전체를 디스크 스트리밍 방식으로 처리하지는 않는다.

Run/Step 페이지 조회는 변경 파일의 작은 메타데이터를 캐시하고 선택된 기록만 완전히
로드한다. 커서 없는 유한 페이지는 필요한 상위 항목만 정렬하며, 커서 조회는 기존 정렬을
유지한다. 파일 stat 비용은 남으며 첫 조회는 전체 스캔이다. catalog_size로 메모리
항목 수를 조절할 수 있다. 캐시는 권위 있는 저장소나 외부 파일 변조 검증기가 아니다.

## 반복 운영 측정

Linux Python 3.12.14에서 다음 명령으로 API 키 없이 동시 실행과 반복 대화를 측정한다.

```bash
python -m examples.llm.operational_probe --seconds 60 --sessions 5
# 같은 도구로 3시간 측정하려면 --seconds 10800
```

기존 프로젝트를 사용하지 않고 새 임시 workspace를 만들며 결과 자료를 보존한다.
stdout JSON의 workspace 경로에서 Run/Step/대화 기록을 확인할 수 있다. 요청 결과를 매번
검사하고 정상 종료 후 새 백엔드에서 마지막 저장 결과를 다시 조회한다. 최대 이벤트 루프
지연, 최근 최대 10,000건의 요청 지연 p95, RSS/FD/스레드와 디스크 용량을 보고한다.
--sessions, --seconds, --payload-chars, --interval로 부하를 조절한다. 오류 발생 시 성공 보고서를
만들지 않고 예외로 종료한다. 보존된 자료 삭제는 사용자가 별도로 결정한다.

이 측정은 합성 Engine으로 저장/스케줄링을 검증한다. 공급자 호출·RAG 2GB 색인·프로세스
강제 종료를 포함하지 않으며, 짧은 실행 성공을 3시간 운영 보장으로 해석하지 않는다.
저장 실패·승인/체크포인트 ACK·프로세스 종료·재개 회귀는 전체 테스트에서 별도로 확인한다.
