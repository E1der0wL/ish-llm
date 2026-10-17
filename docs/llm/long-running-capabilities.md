# 장기 실행 API: 연결, 문서 진행, 명시적 압축과 재개

Project → Session → Run → Step과 기존 소유권을 유지합니다. 애플리케이션은 연결 선택,
압축 시점·방식·목표, 재개 옵션을 결정합니다. Backend는 검증·저장·실행·취소를 담당합니다.
분할 요약은 하나의 명시적 요청으로 관리합니다. Memory가 파생 요청과 체크포인트를,
선택한 Data 패키지가 Tool/MCP 원문 파일을 소유합니다. 큰 사용자 입력을 자동으로 요약하거나
임의의 저장 위치·모델·처리 예산을 선택하지 않습니다.

## MCP

[MCP Component](../../llm/components/mcp/README.md)의 `MCPConnector`는 선택적 SDK 어댑터입니다.
`aprepare(server_id)`는 명시적으로 연결해 Tool schema를 발견하고 저장합니다.
`alist_tools(server_id)`는 저장 목록을 읽습니다. Project의 `parameters.components.mcp.config.tools`
에서 alias → `{server, tool}`을 지정하면 Loop/Graph의 공통 tools capability로 제공합니다.
Agent의 `resources.mcp`는 기존 실행 범위 연결 계약을 그대로 사용합니다.

표준 어댑터는 remote annotations를 실행 권한으로 해석하지 않습니다. ToolContract의
approval_required와 Project 승인 정책을 사용하고, 승인 응답을 저장한 뒤 명시적으로 재개합니다.
모델 응답·MCP 실패 때문에 effect를 자동 반복하지 않습니다. 원격 실행 후 연결이 끊기면
효과는 uncertain입니다. stdio/HTTP 수명과 SDK 오류는 providers/mcp.py가 소유하며,
공통 ToolExecutor가 Step·승인·retry·operation receipt를 계속 소유합니다.

## RAG 목록과 진행

```python
rag = await project.components.aget("rag")
job = await rag.aenqueue_document(identifier="manual", title="작업 설명서",
    description="프로그램 실행 절차와 설정 파일 형식", content=markdown_text)
# 애플리케이션이 arun_job을 task로 실행하고 기다리거나 취소합니다.
await rag.arun_job(job["id"])
progress = await rag.ajob_progress(job["id"])
page = await rag.alist_catalog(limit=20)
```

`rag_list_documents(limit, cursor)`는 같은 목록을 제공하며 모델 호출이 없습니다.
description은 명시적으로 제공하는 데이터이며 실행 지침/권한이 아닙니다.
목록은 corpus와 함께 공개되는 파생 catalog이고, 검색/문서 원본은 기존 corpus가 소유합니다.
cursor는 corpus identity/generation을 포함합니다. 문서 갱신/삭제 후 오래된 cursor는 거부합니다.

진행 정보는 `OperationProgress`입니다. `phase`는 현재 실제 단계이며 count가 있는 단계만
`completed / total * 100`으로 표시합니다. total=None이면 애플리케이션이 미확정 상태를
표시해야 합니다. 임베딩 재사용도 완료 chunk에 포함됩니다. 추출 count는 성공률이 아니라
처리한 batch 수이며 graph_complete/graph_diagnostics가 결과의 완전성을 나타냅니다.
validating/indexing/writing_metadata/publishing은 기존 동기 publish 잠금 범위이므로 polling이 기다릴 수
있습니다. 전체 완료는 active generation publish 후에만 기록합니다.

## 컨텍스트 조회와 압축 요청

```python
memory = await project.components.aget("memory")
view = await memory.aget_context(session.id)
schema = memory.describe_compaction()
request = await memory.arequest_compaction(session.id, {
    "method": "summary",
    "keep_turns": 2,
    "summary_chars": 4000,
    "model_input_chars": 12000,
    "max_summary_calls": 4,
    "prompt": "과거 대화의 결정, 미완료 작업, 명시적 제약을 요약하세요.",
    "completion": {"model": summary_model},
    # 필요하면 provider retry/timeout_seconds/target_tokens도 명시합니다.
}, expected_revision=view["revision"])
```

이는 요청 저장 API입니다. 활성 Loop의 다음 prepare 경계 또는 다음 Loop 요청에서 처리하며,
API 호출 자체는 Run이나 모델 호출을 만들지 않습니다. 처리에는 기존 Memory processor와
내부 Memory Step을 사용합니다. 요약 방식은 현재 summary를 제공하고 모델·프롬프트·크기는
앱이 선택합니다. 자동 압축 임계값, 모델 또는 횟수를 backend가 정하지 않습니다.
이미 명시적으로 설정한 자동 Memory 처리 기능은 유지되지만 이 요청을 자동 생성하지 않습니다.

조회는 마지막으로 관찰한 입력 준비 경계입니다. 저장 원본 전체의 실시간 토큰 계산이나 SDK의
실제 사용량은 아닙니다. Loop CompletionPolicy의 counter가 있을 때 전체 요청의 토큰 수를
표시하며 없으면 null입니다. target_tokens는 실행 Engine의 계수기가 필요합니다.
목표와 기존 입력 예산을 모두 만족해야 새 요약을 적용합니다.

현재 요청, system/developer, 진행 중 Tool 교환은 제거하지 않습니다. 과거 턴의 ID와
내용 hash를 검증하여 입력 사본만 줄이고, 원본 대화/Tool 결과/승인/체크포인트는 보존합니다.
요약은 참고 자료이며 효과 완료의 증명이 아닙니다. 다른 processor나 대화 편집으로 원본이
바뀌면 적용을 거부합니다. 호출 예산 소진은 partial, 모델 실패는 failed, Run 취소/소유 Run 종료는
interrupted로 남고 기존 문맥을 유지합니다. 저장 실패가 발생하면 Run도 실패할 수 있습니다.
명시적으로 요청하지 않으면 부분·실패 작업을 다시 실행하지 않습니다.

요청 상태는 pending → running → partial/applied/failed/interrupted/stale 또는 cancelled입니다. CAS revision이
변했거나 이전 요청이 pending/running이면 새 요청을 거부합니다. scope=None은 최상위 Loop,
중첩 실행은 해당 Engine checkpoint scope를 지정합니다. 서로 다른 scope의 요약을 공유하지
않습니다. 원본이 없는 복제/다른 Session에는 요약을 적용하지 않습니다.

### 조각 저장·재개·취소

분할마다 원문/설정 fingerprint, 처리 offset, 누적 summary, total_chars, completed_chunks,
무결성 digest를 저장합니다. 최신 누적 체크포인트가 완료된 모든 조각을 대표하며 파일을
조각 수만큼 만들지 않습니다. 원문·설정이 바뀐 커서는 재사용하지 않습니다.
모델 대기 중에는 저장 트랜잭션을 잡지 않고 조각 저장과 최종 공개만 짧은 트랜잭션으로 처리합니다.
전체 처리와 목표 예산 검증이 끝나야 applied 결과와 완료 상태를 함께 원자적으로 저장합니다.
중간 결과는 모델 문맥에 적용하지 않습니다. 공개 저장에 실패해도 마지막 완료 커서는 남습니다.

`model_input_chars`는 **이전 요약과 JSON payload를 포함한 문자 예산**이며 system prompt나 SDK
전체 토큰 수가 아닙니다. `summary_chars`는 누적 요약의 최대 문자 수입니다.
`max_summary_calls`는 한 번의 명시적 처리/재개에서 허용하는 모델 호출 수입니다.
필수 값이며 기본값을 주입하지 않습니다. 다음 조각조차 들어가지 않으면 호출 없이 partial로 남습니다.
`offset / total_chars`로 처리한 원문 비율을 표시할 수 있지만 남은 시간 추정치는 아닙니다.

```python
view = await memory.aget_context(session.id)
if view["request"]["status"] in {"partial", "failed", "interrupted"}:
    await memory.aresume_compaction(session.id, expected_revision=view["revision"])
    # 다음 Loop 입력 경계에서 같은 요청 ID/옵션/커서를 이어갑니다.

view = await memory.aget_context(session.id)
await memory.acancel_compaction(session.id, expected_revision=view["revision"])
```

cancel은 예약을 취소하거나 진행 중 결과를 다음 저장 경계에서 거부합니다. 실행 중인 외부 SDK의
동기 네트워크 호출을 강제로 종료하는 API는 아닙니다. 전체 Run을 중단하려면 기존
`session.run.interrupt()`를 사용합니다. 취소한 요청은 재개하지 않으며 새 요청이 필요합니다.
새 요청·취소 이후 늦게 도착한 결과가 새 상태를 덮을 수 없습니다.

적용된 요약과 미완료 요청은 보관 정책이 참조한 원본 메시지를 제거하지 못하게 합니다.
대화 숨기기(delete_turn)의 기존 계약은 유지하며 숨긴 원문에 대한 새 압축은 stale로 거부합니다.
명시적 취소는 이전에
적용한 요약을 지우지 않습니다. 실행이 끝나고 요청도 완료/취소한 뒤
`adelete_compaction(session.id, expected_revision=..., scope=...)`으로 파생 상태와 참조를
해제할 수 있습니다. 원본 대화 삭제는 기존 대화 API와 승인·재개 보호 검사를 거칩니다.

## 큰 Tool/MCP 결과의 참조·요약

기존 CompletionPipeline의 Memory processor에서 **완료된 Tool 결과의 모델 입력 사본**을
처리합니다. 일반 Tool과 MCP는 동일한 경로를 사용합니다. ToolExecutor, 승인 binding,
원본 Step 결과, Loop 재개 체크포인트와 operation receipt는 바꾸지 않습니다.
Graph는 직접 입력을 축약하지 않으며 그 안에서 실행하는 Loop Agent가 같은 처리기를 사용합니다.

```python
data = await project.components.aget("data")
await data.acreate({"title": "Tool 원문"}, identifier="tool_sources")
# 아래 값은 애플리케이션이 선택한 예시입니다. 기존 Memory 설정에 명시적으로 병합하세요.
await memory.aconfigure({
    "config": {"processing": {
        "priority": 0,
        "tool_result_mode": "summary",
        "tool_data": {"component": "data", "identifier": "tool_sources"},
        "tool_summary_prompt": "오류, 근거, 경로와 미완료 작업을 보존하여 요약하세요.",
        "completion": {"model": summary_model},
        "summary_chars": 2000,
    }},
    "policy": {"processing": {
        "tool_result_chars": 8000,
        "model_input_chars": 12000,
        "max_summary_calls": 4,
        # 필요하면 failure_mode/provider/timeout_seconds를 명시합니다.
    }},
})
```

`tool_result_mode`가 없으면 새 경로는 비활성입니다. `reference`는 모델 호출 없이 원문 참조만
만듭니다. `summary`는 분할 요약이 완성된 후 참조와 요약을 적용합니다. 두 모드 모두 명시적인
priority, tool_data, tool_result_chars가 필요하고 summary에는 위 모델·지침·예산도 필요합니다.
기존 `compress_tools` 미리보기와 동시 선택은 거부합니다. 선택한 파일 Component는 공개
snapshot/write_file/read_file 계약을 제공해야 하며 패키지를 사전에 생성해야 합니다.

원문 파일과 참조 요청은 같은 저장 트랜잭션에서 생성됩니다. 참조는 Session, Tool call ID,
원문 hash와 처리 옵션에 연결하며 모델이 다른 Session을 선택할 수 없습니다.
`alist_tool_summaries(session.id)`에서 요청 상태·조각 커서를 조회하고,
`aresume_tool_summary(session.id, identifier, expected_revision=...)` 또는
`acancel_tool_summary(...)`로 조절합니다. 일반 Run 재개만으로 실패/부분 요약을 다시 예약하지
않습니다. 명시적 요약 재개 뒤 해당 Tool 결과가 다시 입력에 들어오는 경계에서 이어집니다.
Run 재개 시 완료 Tool 효과는 재실행하지 않습니다.

모델은 `memory_read_source(identifier, expected_revision, offset, limit)`로 원문의 문자 범위를
읽습니다. 앱은 `memory.aread_source(session.id, identifier, ...)`를 사용합니다. offset/limit는
명시적이며 원문 파일의 revision이 달라지면 거부합니다. 읽기 결과를 다시 참조/요약으로 바꾸지
않아 재귀적인 읽기 루프를 방지합니다. 이 API는 모델에 보내는 범위를 줄이지만 현재 Data의
manifest 검증과 UTF-8 원문 읽기 비용을 제거하는 스트리밍 파일 API는 아닙니다.

참조가 남아 있으면 원문 파일·패키지 삭제와 해당 Component 제거를 막습니다. 원문 수정은
기존 Data CAS 계약을 따르며 수정 뒤 옛 참조는 stale입니다. 완료/취소 후 실행 중인 Run이 없는
상태에서 `adelete_tool_summary(session.id, identifier, expected_revision=...)`로 참조를 해제하고,
Data API로 원문을 별도로 삭제합니다. 자동 삭제·자동 이동·자동 형식 변환은 하지 않습니다.
Session 영구 삭제도 이 Project 자료를 자동 삭제하지 않습니다. 남은 요청은 해당 session_id로
계속 조회·취소·정리할 수 있지만 소유 Session이 없으므로 재개는 거부합니다.

부분 요약은 원래 입력을 유지하므로 여전히 입력 토큰 제한에 걸릴 수 있습니다. 앱은 처리 예산을
명시적으로 정하고 필요 시 요약을 재개하거나 reference 모드를 선택해야 합니다. 이 기능은
SDK 전체 토큰 한도 충족이나 요약의 사실 정확도를 보장하지 않습니다.
Tool 실행 단계의 기존 출력 한도·허용 인자·승인 제한도 그대로 집행됩니다. 그 단계에서 거부된
결과를 Memory가 복구하거나 제한을 우회하지 않으므로 앱은 해당 한도와 처리 임계값을 함께 정해야 합니다.

## 중단 전 부분 출력을 전달하는 재개

```python
handle = await session.run.resume(interrupted_run.id, engine="loop", partial_output=True)
resumed = await handle.wait()
```

기본은 부분 출력 미전달입니다. True를 명시하면 서비스는 미확정 원본 출력과 미완료
소유 Step을 `ResumeOutput` 사본으로 `EngineContext.resume_outputs`에 전달합니다.
서비스는 Step의 Engine 전용 메타데이터를 해석하지 않습니다. Loop는 자신의 checkpoint
이름·scope·iteration이 같은 llm Step의 텍스트만 reference data로 붙입니다. Graph 안의
Loop도 동일합니다. custom Engine은 [공통 계약](shared-contracts.md)의 원본 자료를
자신의 안전 경계에서 선택합니다.

완료된 모델 응답/Tool 결과는 기존 체크포인트로 재사용하고, 부분 출력은 완료 응답이나
Tool 호출로 복원하지 않습니다. 승인·retry_nodes·uncertain effects 검사를 우회하지 않습니다.
새 Run을 만들며 원본 출력이나 대화는 변경하지 않습니다. 대상이 없는 경우 아무 내용도
추가하지 않습니다. 부분 출력 자체가 너무 크면 기존 명시적 입력 예산 검사를 그대로 받습니다.

## 검증 범위

MCP 검사는 로컬 SDK 서버(stdio/Streamable HTTP), 일반 테스트는 fake model을 사용합니다.
실제 사내 모델/인증 서버 및 장기 네트워크 장애 검증을 대신하지 않습니다.
기능별 회귀는 test_mcp_runtime, test_rag_components, test_memory_processing, test_large_data,
test_summarization, test_long_running, test_nested_graph에서 확인할 수 있습니다.
분할 크기 탐색은 작은 후보부터 확장하여 매 조각마다 남은 원문 전체를 측정하지 않습니다.
압축 상태 조회·변경·삭제는 같은 잠금 안에서 복구된 상태를 재사용하며 중복 JSON 조회를 피합니다.
Hub의 test_summary_api는 기존 runtime 주입 경로와 공개 Component/Session API로 설정·예약·
취소·부분 재개·원문 조회·참조 해제를 검증합니다. Hub의 제품 정책이나 새 UI는 구현하지 않습니다.
