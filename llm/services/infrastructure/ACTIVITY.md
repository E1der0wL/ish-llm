# Project Activity 조회

Project → Session → Run → Step 소유권은 그대로입니다. Activity는 새로운 도메인이
아니며, 확정된 lifecycle을 가리키는 **읽기 전용 파생 인덱스**입니다.
Run 상태/오류의 원본은 `run.json`, Step 상태/Diagnostic의 원본은 `step.json`입니다.

```python
events = await project.aactivity(limit=100)
# 동기 API; 최근 100건을 오래된 것부터 표시
events = project.activity(limit=100, newest_first=False)
for event in events:
    print(event.time, event.event, event.source.run_id, event.code)

# 실패 상세는 기존 API에서 조회한다.
ref = events[-1].source
session = await project.sessions.aload(ref.session_id)
run = await session.run.aload(ref.run_id)
result = await run.aresult()
steps = await run.steps.alist()
```

반환은 `tuple[ProjectActivityEvent, ...]`이며 각 값과 `ResourceRef`는 frozen snapshot입니다.
`ProjectActivityEvent`는 `llm.llm` 플러그인 공개 API와 `llm.core.contracts`에서 사용할 수 있습니다.
`limit=None`(생략)은 전체, `0`은 빈 결과입니다. 음수/boolean limit은 거부합니다.
숨겨진 건수/기간 필터는 없습니다. 뒤쪽 JSONL을 블록 단위로 읽어 최신 limit건만 반환하며,
`newest_first=False`도 **최근 limit건**을 선택한 다음 표시 순서만 바꿉니다.
순서는 after-commit append 순서입니다. 여러 프로세스의 wall-clock 시각을 재정렬하지 않습니다.
조회는 workspace 소유권 잠금과 비동기 StorageIO를 공유하며 Run/Step 전체를 로드하지 않습니다.

## 기록 경계

파일은 `ProjectPaths.logs / "activity.jsonl"` 하나입니다. 기존 `service.log`와 별개이며
기존 operational logging은 유지합니다. 사용자 설정/SDK 기본값/도메인 저장 버전은 바꾸지 않습니다.

| 확정 경계 | Activity | code |
|---|---|---|
| RunManager `_begin` | `run.started` | null |
| RunManager `_finish` | `run.completed`, `run.failed`, `run.interrupted`, `run.paused` | 저장된 Run.error_code |
| 공통 Engine 이벤트 저장에서 StepEventRecorder 성공 | `step.failed` | EngineEvent.failure_code, 없으면 `step_failed` |

예약은 저장 transaction 내부에서, append는 기존 `after_commit()`으로 수행합니다.
rollback에는 기록하지 않습니다. callback은 workspace 잠금 안에서 실행되어 여러 Session의
쓰기를 직렬화합니다. Engine·Component·Step 저장소에 Activity 호출이나 오류 분류를 추가하지 않습니다.
Step Diagnostic의 관찰용 code를 안정적인 Activity code로 승격하지 않습니다.
일반 복구/Run 정리에서 직접 종료한 Step은 Engine STEP_FAILED 이벤트가 아니므로 별도 복제하지 않습니다.
기존 저장 데이터의 소급 인덱싱이나 stale Run recovery 이벤트의 합성도 하지 않습니다.

필드는 `time`, `event`, `source`, `status`, `code`뿐입니다. 본문, prompt, Tool 인자/결과,
evidence, 오류 원문, traceback, provider attempt를 기록하지 않습니다.
Provider retry 및 성공 Step의 상세 정보는 기존 관찰/도메인 조회를 사용합니다.

## 실패와 제한

- UTF-8 JSONL append + flush/fsync를 사용합니다. 경로/파일 symlink와 비정규 파일을 거부합니다.
- 생성·직렬화·append·fsync 실패는 payload 없는 warning만 남기며, warning 처리기 오류도 격리합니다.
- 파일이 없으면 빈 결과입니다. 손상/읽기 실패는 Activity 조회에만 오류를 반환하며
  Project 로드와 Run 실행은 그대로 가능합니다. 부분 tail은 자동 절단/수정하지 않습니다.
- 제한 조회는 읽은 최근 구간만 검증합니다. 이전 구간의 손상은 그 구간 조회 시 보고합니다.
- domain commit 직후 process crash 또는 append 실패 시 이벤트가 누락될 수 있습니다.
  Activity는 exactly-once 감사 원장이나 복구 자료가 아닙니다. 재시도/복구/승인은 원본으로 판단합니다.
- rotation, retention, 삭제, rebuild, 자동 재실행은 없습니다. Project backup의 파일 무결성
  정책도 변경하지 않으며 Activity 때문에 도메인 storage_version을 올리지 않습니다.
- 8 KiB 읽기 블록은 I/O 구현 선택이며 결과 수나 실행을 제한하지 않습니다.
