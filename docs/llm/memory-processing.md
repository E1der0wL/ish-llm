# Memory에서 장기 대화·작업 최적화

요약·기억 선택·Tool 결과 압축·기억 후보 추출은 모두 `llm/components/memory`가 담당한다.
Project/Session/Run/Step 모델과 Manager에는 Memory 전용 실행 로직을 추가하지 않는다.
LoopEngine은 `completion_processors`라는 범용 capability를 요청하고 처리기의
`session(context)` → `prepare(CompletionRequest)` / `finish(CompletionObservation)` 계약을 사용한다.
공통 계층은 매 모델 응답의 after_completion과 항상 실행하는 aclose도 제공한다.
Memory는 CompletionSession을 상속해 필요한 prepare/finish만 구현한다.
다른 컴포넌트도 같은 계약을 제공할 수 있다. 처리기가 없으면 빈 컬렉션으로 실행한다.
[공통 처리기 계약](completion-processing.md)에 확장 예제와 조합/수명 규칙을 설명한다.
Memory의 processing.priority는 기본 100이며 작은 값이 먼저 실행된다.

## 활성화와 설정

Memory 컴포넌트를 선택하면 기본적으로 관련된 **confirmed 기억 자동 검색**과 **큰 Tool
결과의 입력용 미리보기**를 제공한다. 추가 모델 호출이 필요한 요약·추출은 기본적으로 꺼져 있다.
아래 설정에서 사용하는 보조 모델은 메인 Loop 모델과 독립적으로 지정한다.

```python
project = await backend.projects.acreate("업무", components=["memory"])
memory = await project.components.aget("memory")
await memory.aconfigure({
    "processing": {
        "recall": True,
        "summarize": True,
        "extract": True,
        "completion": {"model": model_name, "api_key": api_key},
        "keep_turns": 8,
        "summary_after_chars": 12000,
        "summary_chars": 3000,
        "context_chars": 6000,
        "recall_limit": 8,
        "compress_tools": True,
        "tool_result_chars": 4000,
        "model_input_chars": 24000,
        "max_candidates": 5,
        "extract_scope": "session",
        "timeout_seconds": 60,
        "failure_mode": "raise",
    },
})
session = await project.sessions.acreate()
run = await (await session.run.submit("작업을 계속해줘", engine="loop")).wait()
```

`configure`는 전체 설정 교체다. 생략한 동작 설정에는 기본값을 적용하고 모르는 JSON 필드는
보존한다. `completion`에는 LiteLLM의 일반 인자를 전달할 수 있지만 messages/tools/stream/n은
Memory의 보조 호출 계약을 따른다. 기본 보조 호출은 자동 재시도하지 않는다. 보조 모델은
도구를 실행하지 않는다. `MemoryComponent(completion_fn=...)`로 모델 호출을 교체할 수 있다.

## 각 처리의 동작

**기억 검색:** 현재 입력으로 프로젝트 공통 기억과 현재 Session의 기억을 검색한다. candidate와
만료된 기억은 자동 주입에서 제외한다. 기억 ID/revision과 내용을 구분된 참고 데이터로
현재 사용자 메시지의 모델 입력 사본에 붙인다. system 지시로 승격하거나 원본 대화를 바꾸지
않는다. 검색은 키워드 방식이며 입력과 표현이 다른 의미적 연관까지 보장하지 않는다.

**증분 요약:** 최근 `keep_turns`개의 턴은 원문으로 유지하고 그 이전 턴을
요약한다. 실패·중단·일시정지한 과거 메시지는 상태를 붙여 전달해 성공으로 취급하지 않도록
지시하며, 미래 대기 입력은 포함하지 않는다. 원본 메시지 ID·내용·상태의
해시와 설정 해시가 맞을 때만 기존 요약을 재사용한다. 이미 처리한 구간은 보조 모델에 다시
넣지 않고, 이전 요약과 새 구간만 전달한다. 한 Engine 호출당 최대 한 배치를 요약하므로
누적된 구간이 크면 다음 요청에서 계속 처리한다. 완료된 사용자/응답 턴을 쪼개서 제거하지 않는다.

`model_input_chars`는 요약 요청의 JSON 본문 문자 예산이며 이전 요약과 JSON 포장을 포함한다.
보조 시스템 지시는 별도다. 하나의 턴 자체가 예산보다 크면 그 턴 이후는 원문으로 남긴다.
모델이 보지 않은 원문을 요약 완료로 표시하지 않는다. 요약은 손실 있는 파생 자료이며
원문은 대화 저장소에 그대로 남는다. 현재 작업 Tool 교환 요약도 같은 원칙으로 배치 처리한다.
최근 턴을 이미 제거하는 ContextPolicy를 사용하면 Memory도 Engine에 전달된 범위만 볼 수
있다. 전체 과거 대화를 요약하려면 기본 full 정책을 유지하고 Memory가 압축하도록 구성한다.
요약 교체는 원본 메시지 ID/내용에 기반한다. 다른 처리기의 추가 메시지는 유지하며,
다른 처리기가 요약 대상 원문을 바꿨거나 제거했다면 해당 요청에서는 요약 교체를 생략한다.

**Tool 결과 압축:** 모델에 다시 보내는 큰 tool 메시지를 미리보기·원본 크기·Run ID·
tool_call_id·SHA256으로 바꾼다. Tool 호출/결과 메시지의 쌍, ID, 이름, 호출 인자는 보존한다.
완전한 결과는 기존 Step.output에 남는다. UI는 Run의 Steps에서 metadata.tool_call_id로 찾을
수 있다. 이 참조를 자동으로 역참조하는 새로운 LLM Tool을 추가한 것은 아니다. 상세 내용이
필요한 작업은 memory_tool_result의 Run/호출 ID 기반 부분 조회를 사용하거나 압축을 끌 수 있다.
실행 효과를 재시도하지 않는다. 현재 작업의 완료된 Tool 교환 요약은 compact_active와
active_keep_iterations로 조절한다. [장시간 작업 API](long-running.md)에 예제를 설명한다.

**기억 후보 추출:** 정상적인 최종 모델 응답 뒤에 현재 요청·답변과 제한된 기존 기억을
검토한다. 생성 결과는 항상 candidate이며, 기본 범위는 현재 Session다. 프로젝트 공통 지식으로
수집하려면 `extract_scope="project"`를 선택한다. 이미 존재하거나 삭제한 동일 내용은 다시
생성하지 않는다. 모델은 기존 ID/revision을 참조하는 `metadata.replaces`로 병합·모순 해결을
제안할 수 있다. 해당 revision을 저장 직전에 다시 확인한다. 제안만으로 기존 confirmed
기억을 덮어쓰거나 삭제하지 않는다. 사실의 정확성/의미적 중복 판별은 모델 품질에 달려 있다.

목표·결정·미완료 작업은 요약에 보존할 수 있고, 명시적으로 유지할 작업 메모는 Session 범위의
`kind="work_state"` 기억으로 CRUD할 수 있다. 실행 상태의 진실은 Run/Graph 체크포인트에 있으며
작업 메모를 보고 실패한 외부 작업을 자동 재실행하지 않는다.

## 예산·실패·중첩 실행

`context_chars`는 주입할 참고 데이터의 총 문자 상한이다. 정확한 토큰 상한도 필요하면
`MemoryComponent(token_counter=문자열_토큰_계수기)`와 `processing.context_tokens`를 함께 설정한다.
모델별 계수기를 주입해야 하며 임의의 문자→토큰 추정치는 사용하지 않는다.
그 뒤 기존 CompletionPolicy이 tools와 현재 Tool 호출/결과를 포함한 전체 요청을 다시 검사한다.
기억 추가 때문에 현재 요청이 예산을 넘으면 참고 데이터 주입과 요약에 의한 원문 제외를 함께
되돌린다. 현재 요청/Tool 인자 자체가 너무 크면 기존 예산 오류가 발생할 수 있다.

처리 과정과 보조 Completion 사용량은 소유 Run의 내부 Memory Step에 기록한다. 보조 텍스트는
사용자 답변으로 출력하지 않는다. 요약 Step에는 생성한 요약과 원문 범위도 남긴다.
기본 `failure_mode="raise"`는 처리 실패를 Run 실패로 전달한다. `"continue"`를 명시하면 해당
처리의 오류를 `degraded=True`인 fallback Step에 기록하고 기본 작업을 계속한다. 이때 Step은
완료 상태이지만 오류와 기능 저하가 명시된다. 취소는 항상 전파하며 계속 진행하지 않는다.
보조 모델 요청도 같은 백엔드의 ProviderCalls 한도와 Run의 실행 기한 안에서 실행한다.

Graph의 Loop Agent에는 동일한 검색·Tool 압축이 적용된다. 다른 노드의 입력을 Session 전체 대화와
섞지 않도록 중첩 Engine의 요약·자동 추출은 기본적으로 꺼져 있다. Pipeline처럼 대화 문맥을
그대로 유지하는 중첩 구성에서는 `nested_processing=True`로 활성화할 수 있다. 이 경우에도
원문 해시가 일치하는 요약만 사용한다. 순수 Graph ToolNode는 모델 입력을 만들지 않으므로
completion 처리기를 실행하지 않으며 기존 Memory Tools를 사용할 수 있다.

자동 요약·추출의 실행 권한은 프로젝트의 processing 설정으로 부여한다. 이는 Tool 호출이
아니므로 ToolPolicy로 끄는 기능은 아니다. 모델이 직접 호출하는 Memory Tools에는 기존
ToolPolicy가 그대로 적용된다. 양쪽 모두 소유 Step과 출처를 기록한다.

## UI용 조회와 저장 구조

```python
summary = await memory.asummary(session.id)  # 없으면 None
if summary:
    print(summary["content"], summary["metadata"]["through_message_id"])
    # 요약만 초기화; 다음 실행에서는 원문으로 다시 생성한다.
    await memory.aclear_summary(session.id, expected_revision=summary["revision"])

review = await memory.areview(session_id=session.id)
# review["duplicates"]: 같은 범위의 정규화된 동일 내용 ID 그룹
# review["expired"]: 만료된 기억 ID
# review["proposals"]: metadata.replaces가 있는 검토 대상 기억
```

```text
<project>/memory/
  identity.json                 # 삭제·재생성 중인 저장소를 구분하는 세대 ID
  records/<id>.json             # 일반 기억과 revision 이력
  contexts/<session-id>.json       # 현재 파생 요약, 고정 크기 원문 범위 해시, revision/generation
```

Session 요약은 최신 캐시를 원자 교체한다. 전체 메시지 ID 목록이나 매번 커지는 요약 이력을
중복 저장하지 않는다. 이전 요약 결과는 해당 Run의 Step에서 확인한다. generation과 revision을
함께 비교하므로 캐시를 지우고 재생성한 뒤 오래된 요약 작업이 덮어쓰는 것도 거부한다.
프로젝트의 선택 해제·삭제·저장소 재생성·설정 변경 후에는 준비 중이던 결과를 쓰지 않는다.
백업은 Session와 요약 캐시를 같이 보존하고 검증한다. 프로젝트 복제는 새 Session ID를 만들므로
파생 캐시를 복사하지 않고 다시 계산한다. Session 범위의 일반 기억은 원래 session_id를 유지하며
다른 Session으로 자동 확산하지 않는다. 별도 Session으로 옮기려면 UI/API에서 새 기억을 명시적으로 생성한다.

기억 여러 개의 후보 등록은 파일별 원자 쓰기다. I/O 실패 시 일부 후보가 남을 수 있으며
실패를 자동 재시도하지 않는다. 이미 저장된 후보와 출처를 조회해 확인할 수 있다.

컴포넌트 설정은 `project.json`의 `config.component_configurations`에만 저장한다.
ComponentData.configure 편의 API도 이 설정을 갱신한다.
