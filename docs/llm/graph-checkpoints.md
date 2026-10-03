# Graph 체크포인트와 UI Workflow 저장

## 실행 계약

GraphEngine은 **Workflow 노드 경계**를 저장한다. LangGraph가 분기·반복·병렬 실행을
계속 담당하며, 체크포인트는 Engine 이벤트를 받은 서비스가 Run 아래에 기록한다.
LangGraph 네이티브 checkpointer의 내부 프레임이나 Python 객체를 직렬화하지 않는다.

* `completed`: 검증된 출력이 저장됐다. 재개에서는 처리기를 호출하지 않고 결과만 복원한다.
* `waiting`: `pause_before: true` 또는 Tool 승인 대기에 도달했다. 해당 효과는 실행하지 않았다.
* `started`: 실행 전 기록은 있지만 완료 기록이 없다. 실행/실패/중단 중 발생한 부작용은 불확실하다.

상태·분기 port·반복 회차별 결과를 보존한다. 중첩/병렬 실행의 키는 JSON 배열을 문자열로
인코딩한다. UI는 이 키를 해석해서 만들지 말고 조회 결과의 키를 그대로 전달한다.
`path`와 `node_id`는 사용자에게 표시할 이름이다.

Tool 승인 대기는 decisions의 approved가 필요하다. 대기 노드의 상태 편집은 명시적인
resume_schema를 가진 workflow confirmation에서만 허용한다. Tool 승인으로는 인자를
바꿀 수 없다. `pause_before`를 확인해도 Tool 권한은 부여되지 않는다. 호스트가 ASK하면
새 Run에서 별도의 Tool approval로 다시 대기한다. [공통 승인 API](interactions.md)를 참고한다.

노드 실행 전 started 저장이 실패하면 처리기를 호출하지 않는다. 처리기를 호출한 뒤
completed 저장이 실패하면 started로 남아 재실행 승인이 필요하다. 외부 부작용과 로컬
체크포인트는 하나의 트랜잭션이 아니므로 **정확히 한 번 실행을 보장하지 않는다**.

## UI에서 조회·재개

```python
request = await session.run.submit("설정 파일을 검토해줘", engine="graph", engine_options={"workflow": workflow_id})
run = await request.wait()  # completed / failed / interrupted / paused 중 하나
snapshot = await run.acheckpoint()

# pause_before에서 기다린 노드는 별도 재시도 승인 없이 이어갈 수 있다.
resumed_request = await session.run.resume(run.id, engine="graph")
resumed_run = await resumed_request.wait()
```

`resume`은 이전 Run을 수정하지 않고 **새 요청과 새 Run**을 만든다. 새 Run의
`metadata["resume"]["run_id"]`가 원본을 가리킨다. UI는 각 시도의 결과/사용량을 구분해
표시할 수 있다. 요청 큐/실행 직렬화/소유권 잠금은 submit과 같은 경로를 사용한다.
일시정지한 Run은 Session을 점유하지 않으므로 다음 요청도 처리된다.
이 시도의 미완료 root/제어 Step은 INTERRUPTED로 마감하고, 재개 Run에 새 Step을 만든다.
재사용 Step에는 원본 Run/Step 참조와 reused 표시가 남는다.

`paused` Run/Assistant 상태와 `RunEventType.PAUSED` 알림을 추가했다. 알림은 영속 상태
갱신 뒤 발행된다. `EngineEventType.CHECKPOINT`는 저장 완료된 체크포인트 변경을 관찰한다.
`run.checkpoint()`는 동기, `run.acheckpoint()`는 같은 저장소를 사용하는 비동기 조회다.

중단/실패한 노드가 있으면 UI에서 해당 노드가 수행한 작업을 확인한 뒤 선택해야 한다.

```python
snapshot = await run.acheckpoint()
controls = {"branch", "parallel", "join", "loop", "end"}
uncertain = {
    key: value for key, value in snapshot["records"].items()
    if value["status"] == "started" and value["node_type"] not in controls
    and not value.get("container", False)
}
# uncertain의 path, node_id와 원본 Run의 Step/Tool 이력을 UI에 보여준다.
# 사용자가 재실행을 승인한 키 목록을 전달한다. 자동으로 전부 승인하지 않는다.
resumed_request = await session.run.resume(
    run.id, engine="graph", retry_nodes=user_approved_keys,
)
```

미완료 처리 노드 전부에 대한 승인이 정확하게 필요하다. 완료 노드/존재하지 않는 키,
중복 키는 거부한다. 미완료 loop/parallel 등 제어 노드는 완료된 자식 결과를 사용해
자동 복원하므로 승인 목록에 넣지 않는다. 병렬 경로 안에서 일시정지하면 실행 중인
형제 노드는 취소되며 started로 남을 수 있다. 깔끔한 승인 경계는 parallel 전이나 join 뒤에 둔다.

## 재개가 거부되는 경우

* 다른 Session의 Run, 실행 중/완료된 Run, 체크포인트가 없는 Run.
* 다른 Engine 이름 또는 체크포인트 재개를 지원하지 않는 Engine.
* 이미 재개 요청이 생성된 원본 Run. 그 재개 시도가 실패/중단/일시정지했다면 **최신 Run**을 재개한다.
* Workflow/Agent/Tool 정의, Project/Session 설정, 실행 제한 또는 Engine revision 변경.
* 체크포인트가 요청 수락 이후 변경됨(저장된 digest로 확인).
* 원래 대화가 없어짐. 메모리 저장 모드의 백엔드 종료 이후에는 재개할 수 없다.

admission 거부는 `RunRequestError.code == RunErrorCode.RESUME_REJECTED`다.
수락 후 정의가 변경되면 실행 시에도 다시 검사하여 새 Run을 실패시키고 부작용을 막는다.
그 시도에 상속한 체크포인트는 남으므로 정의를 원래대로 복원한 뒤 최신 Run을 재개할 수 있다.
Run 생성 직후 초기 체크포인트 복사 전에 종료된 시도는 원본 연결과 digest를 검사해 복원한다.
초기화 완료가 저장된 뒤 파일이 사라진 경우는 손상으로 거부한다. `metadata.checkpoints`의
초기화 표시와 `metadata.resume`은 서비스가 관리하며 사용자 이벤트 처리기로 덮어쓸 수 없다.
프로세스 재시작 시 stale Run은 INTERRUPTED로만 복구한다. 이전에 사용자가 명시적으로
수락시킨 **QUEUED 재개 요청**만 파일 큐의 일반 요청처럼 예약된다.

`GraphEngine(..., revision="1")`의 revision은 개발자 코드 버전이다. 처리기/Tool 코드나
사용자 capability의 의미가 바뀌면 개발자가 올려야 한다. 함수 본문 변경을 자동 감지하지 않는다.
외부 파일·RAG 코퍼스·네트워크 데이터·환경변수는 자동 고정하지 않으므로 필요하면 노드 입력에
버전/해시를 저장하고 해당 처리기가 검사한다. 복원할 상태는 `node.state`와 반환 JSON을
사용한다. 런타임 전용 `context.state`나 열린 프로세스/연결은 재개되지 않는다.

## UI가 정의한 Workflow와 노드의 저장

Workflow 하나가 **JSON 파일 하나**다. `nodes` 객체 안의 키가 노드 ID이고 `edges`는
그 노드 ID를 참조한다. 각 노드는 별도 파일이 아니다. 노드 실행 상태도 정의 파일에 쓰지 않는다.
`type`은 개발자가 `GraphEngine(handlers={...})`에 등록한 처리기 이름 또는 내장 제어 노드다.
UI에서 Python 함수 본문을 저장하면 실행되는 방식이 아니다.

```python
workflows = await project.components.aget("workflows")
definition = {
    "schema_version": 1,
    "entry": "write",
    "initial_state": {},
    "inputs": {"request": "/prompt"},
    "nodes": {
        "write": {
            "type": "agent", "agent": "writer",
            "inputs": {"request": "/request"},
            "outputs": {"draft": "/text"},
            "ui": {"position": {"x": 80, "y": 120}},
        },
        "verify": {
            "type": "validate", "pause_before": True,
            "inputs": {"draft": "/draft"},
            "ui": {"position": {"x": 320, "y": 120}},
        },
        "end": {"type": "end"},
    },
    "edges": [
        {"source": "write", "target": "verify"},
        {"source": "verify", "target": "end"},
    ],
    "ui": {"zoom": 1.0},
}
await workflows.acreate(definition, identifier="draft-review")
loaded = await workflows.aload("draft-review")
# UI에서 loaded를 수정한 뒤 전체 그래프를 저장한다.
await workflows.asave("draft-review", loaded)
```

위 예제는 별도로 `agents`에 writer 정의를 저장하고 `agent: AgentNode(engines={"loop": LoopEngine()})`와
`validate: 비동기 검증 함수`를 Engine에 등록해야 실행된다. Agent 참조는 해당 정의를
가리키는 ID다. 저장 시 그래프 구조·입출력 계약을 검증하고 원자적으로 교체한다.
실행 직전에는 처리기 등록과 Agent/Tool 참조도 검사한다. 잘못된 그래프는 기존 파일을
덮어쓰지 않는다. `ui`처럼 알려지지 않은 JSON 필드는 유지된다. 동시 UI 편집에 대한
revision/CAS 충돌 검출은 아직 제공하지 않으므로 다중 편집기에서는 별도 정책이 필요하다.

```text
projects/<project-id>/
├─ project.json
├─ agents/
│  └─ records/writer.json
├─ workflows/
│  └─ records/draft-review.json   # nodes, edges, inputs/outputs, UI 배치
└─ sessions/<session-id>/
   ├─ session.json
   ├─ conversation.jsonl         # file 모드일 때만
   └─ runs/<run-id>/
      ├─ run.json                # 상태, engine, 원본 Run 연결
      ├─ steps/<step-id>/step.json
      └─ state/checkpoints/graph/
         ├─ checkpoint.json     # 정의/설정, 초기 상태, 대화 ID, 상속 기록
         └─ records/<hash>.json  # 노드 실행 키, 상태, 입력/출력, 원본 Step/Run
```

체크포인트는 Run별 최신 상태이며 시도별 이력은 이전 Run과 Step에 남는다. 변경될 때마다
모든 결과를 다시 쓰지 않고 해당 노드만 교체한다. 재개 Run의 초기 헤더는 원본 결과를
상속하므로 이전 파일을 수정하지 않는다. 파일을 직접 조작하는 대신 서비스 API를 사용한다.

## 후속 보강

Agent Tool 단위의 승인/결과 재사용, resume_schema 검토 입력, Workflow CAS와 보관 정책은
구현되어 있다. 승인 UI 투영과 취소 정리 제한은 [domain-hardening.md](domain-hardening.md)를
참고한다. 실제 배포 Linux의 장시간·대용량 부하는 별도 검증이 필요하다.

LangGraph 네이티브 `interrupt(value)`/`Command(resume=...)`는 이 API와 연결하지 않았다.
현재는 노드 실행 전 `pause_before`를 사용한다. 처리기 내부의 임의 지점에서 멈추고 재개하거나
실패한 노드를 건너뛰어 성공으로 취급하는 기능은 제공하지 않는다.

## 검증

Python 3.12.14 전체 454개 테스트가 통과했다(347.012초). 체크포인트 전용 19개에는
별도 프로세스 강제 종료, 병렬 분기의 완료 결과 재사용, 반복 회차 구분, 중복 재개 거부,
초기 복사 실패 복구, 저장 실패 전/후의 부작용 경계, 파일/메모리 저장 계약이 포함된다.
외부 모델 API는 호출하지 않았다. 최종 로그는 저장소 루트의
tests/llm/reports/history/graph-checkpoint-final-suite-python312.txt다.

```powershell
.\.venv312\Scripts\python.exe -m unittest tests.llm.test_graph_checkpoints -v
.\.venv312\Scripts\python.exe -m unittest discover -s tests/llm -t . -v
```

컴포넌트 설정은 `project.json`의 `config.component_configurations`에만 저장한다.
ComponentData.configure 편의 API도 이 설정을 갱신한다.
