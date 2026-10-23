# Goals

Goal은 Project가 선택하는 목표 리소스다. 실행 소유 계층은 Project → Session → Run → Step이다.
`goals/records/<id>.json`이 목표 상태의 원본이며 `run_refs`는 같은 Project의 Run 참조다.
Run을 생성하거나 재개·완료시키지 않는다. 목표 상태 변경은 과거 Run을 수정하지 않는다.

목표 상태는 `records.py`의 `GoalStatus`가 소유하며 `data.py`는 같은 타입을 재노출한다. 레코드 Schema, 상태 변경과 문맥 선택,
목록 Tool의 허용 값은 같은 선언을 사용하며 저장·반환 값은 기존 문자열이다.

```python
goals = project.components.goals
identifier = await goals.acreate({
    "title": "기능 완성", "objective": "구현과 검증을 완료한다",
    "scope": {"type": "session", "id": session.id}, "status": "active",
    "success_criteria": ["통합 테스트 통과"],
})
view = await goals.asnapshot(identifier)
await goals.alink_run(identifier, session.id, run.id,
    relation="contributes_to", expected_version=view["version"])
```

공개 API: create/load/list/snapshot/save/update/delete/link_run/unlink_run/complete/pause/resume.
각각 a 접두 비동기 API가 있다. 변경에는 snapshot.version 내용 해시를 expected_version으로
전달한다. scope·Run 참조는 주입된 기존 저장소로 확인하며 다른 Project로 연결할 수 없다.
한 Run을 여러 Goal이 참조할 수 있다. 삭제는 목표 리소스만 지우며 Run은 유지한다.

`goal_list`, `goal_read`는 현재 Session 또는 Project 목표만 읽는다. `goal_update_progress`,
`goal_link_run`은 policy.write_tools=true일 때만 제공하며 기존 ToolExecutor와 호스트 승인
어댑터를 요구한다. 별도 승인 시스템은 없다. 읽기만 사용하는 Project는 승인 어댑터가 필요 없다.
직접 CRUD는 신뢰한 호스트/UI API다. Tool은 목표 생성·완료를 임의 결정하지 않는다.

자동 문맥 주입은 `parameters.components.goals.policy.inject=true`와 `config.priority`를
명시해야 한다. config.identifiers로 목표 ID를 좁힐 수 있다. user 메시지의 참고자료로만
삽입하며 system 지시로 승격하지 않는다. 중첩 실행은 config.nested_agent_ids로 지정한 Agent만
허용한다. 미설정은 주입하지 않는다. Goal이 선택되지 않은 Project는 기존과 동일하다.

Project clone은 project-scope 목표만 복제하고 run_refs를 비운다. Session-scope 목표는
복제하지 않는다. 원본 위치는 metadata.cloned_from으로 남는다. Component 선택 해제는
자료를 보존하고 재선택하면 다시 사용할 수 있다. 영구 제거는 기존 Component API를 따른다.

- records.py: Goal 정의·상태·scope·Run 참조·설정의 선언과 검증·Projection. 저장 형식과 생략 의미를 보존한다.
- component.py: Owner schema 연결, Application metadata, clone, capability 제공.
- data.py: 잠금·버전 검사·진행 상태·검증된 Run 참조.
- processing.py: 명시적 completion_processors 참고자료 주입.
- tools.py: 현재 Run 범위의 읽기/승인 대상 쓰기 Tool.
- tool_values.py: Tool 인자·공개 이름·전송 schema·효과 계약 선언. 저장 Schema의 표시용 필드를 전송 인자에 추가하지 않는다.

필수 필드 조회는 Owner의 지연된 Reader를 사용한다. 누락을 기본값으로 복구하지 않는다.
설정은 기존 config/policy envelope만 읽으며 새 설정 경로나 암묵적 opt-in을 만들지 않는다.
실제 Run 검증은 승인 전 저장 0회, 승인 후 저장 1회, 오래된 version/다른 Session scope의
저장 0회와 오류 이후 추가 모델 호출 0회를 확인한다. 기존 원본·삭제·예산·Checkpoint 계약도 유지한다.
