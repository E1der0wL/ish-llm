# Data — Project 자료 패키지

선택한 Project에만 `data/<id>/data.json`과 `data/<id>/files/`를 만든다. 정의는
title/description/metadata와 관리되는 files 목록을 가진다. JSON metadata는 Application
부가정보이며 실행 권한을 만들지 않는다. 파일의 실제 내용은 JSON에 넣지 않는다.

```python
data = await project.components.aget("data")
await data.acreate({"title": "실험 자료"}, identifier="experiment")
snapshot = await data.asnapshot("experiment")
snapshot = await data.awrite_file("experiment", "examples/input.csv", "name,value\na,1\n",
                                  expected_revision=snapshot["version"])
raw = await data.aread_file("experiment", "examples/input.csv",
                           expected_revision=snapshot["version"])
```

- create/load/list/save/update/delete와 a 접두사 API는 기존 Component 계약을 따른다.
- list_files/read_file/write_file/import_file/delete_file과 비동기 API를 제공한다.
- import_file의 source는 명시한 로컬 파일 경로다. 바이너리 파일도 buffer 단위로 복사한다.
- 파일 변경은 snapshot의 version을 expected_revision으로 전달해야 한다.
- read_file은 bytes를 반환하며 offset/length로 부분 조회할 수 있다. 생략하면 전체를 읽는다.
- 파일 이름은 정규화된 상대 POSIX 경로다. symlink, 장치, 경로 탈출은 거부한다.
- 정의와 파일 해시는 같은 저장 트랜잭션에 반영한다. files를 직접 위조/수정할 수 없다.
- 복제·백업은 파일을 포함한다. 삭제는 해당 패키지 전체를 제거한다.
- temporary 같은 metadata나 tmp/라는 파일 이름만으로 자동 삭제하지 않는다.
- 실행·모델 학습·RAG 색인·자동 캐시·과거 버전 보관은 수행하지 않는다.

설정은 선택적이며 기본은 빈 config/policy다. Data는 필수 Component가 아니다.
이 Component 자체는 모델용 CRUD Tool을 등록하지 않습니다. 앱은 공개 API를 사용하고,
모델은 Skill에서 명시한 자료를 `skill_read_resource`로 읽을 수 있습니다.
Skill이 참조하는 파일·패키지는 삭제할 수 없습니다. 내용을 바꾸면 기존 참조는 stale 상태가
되며 Skill에서 기대 revision을 명시적으로 갱신해야 다시 사용할 수 있습니다.
Memory에서 명시적으로 이 패키지를 Tool/MCP 원문 저장 대상으로 선택할 수도 있습니다.
Memory가 요약 요청·원문 참조를 소유하고 Data는 파일·manifest·CAS만 소유합니다.
Memory의 참조가 남아 있으면 삭제를 차단합니다. 명시적으로 참조를 해제한 뒤 Data API로
삭제해야 하며 자동 정리는 없습니다. 모델의 부분 조회 Tool은 Memory가 제공합니다.
패키지를 읽을 때 manifest의 모든 파일을 검증하므로 큰 자료 집합에는 검증 I/O 비용이 있습니다.
실행 중 취소된 파일 쓰기는 기존 StorageIO 계약대로 저장 작업의 완료/롤백을 기다립니다.
따라서 취소 통지를 받았더라도 파일이 완전히 저장됐을 수 있으며 snapshot으로 확인해야 합니다.

## 첨부 파일의 명시적 격리

`inspect_quarantine`은 유효한 원본 정의·패키지 목록과 현재 첨부 바이트를 진단한다. 격리 허가는
아니다. `quarantine`은 최신 `expected_revision`, Skill/Memory 등의 저장된 참조, Project의
모든 Session 실행 상태와 미완료 Resume 참조를 같은 workspace 잠금 안에서 다시 검사한다.
비활성 Component의 참조도 보호한다. 정의·목록·참조·실행 증거가 손상됐거나, 원래 정의에
첨부 선언이 없거나, 출처를 확인할 수 없는 패키지 항목이 있으면 이동하지 않는다.

```python
inspection = await data.ainspect_quarantine("experiment")
receipt = await data.aquarantine("experiment", expected_revision=inspection["version"])
# 격리 자료의 명시적 영구 삭제도 현재 참조·실행 상태와 보존된 바이트를 재검증한다.
await data.adelete_quarantined("experiment", expected_revision=receipt["version"])
```

Data owner는 `data/<id>/quarantine/files/`에 관측한 원본 바이트를 복사·검증하고
`receipt.json`과 기존 `files/` 제거를 하나의 트랜잭션으로 확정한다. 원래 `data.json`과
패키지 목록은 보존한다. 영수증은 독립 schema version, 격리 ID, Project/Component/패키지
식별자, 원래 정의의 revision, 실제 파일 hash/크기, 격리 시각을 필수로 가진다.
`quarantine_snapshot`은 이 증거와 현재 바이트를 검증하고 data/version을 반환한다.
자동 복구·복원·대상 선정은 제공하지 않는다. 실패와 실제 프로세스 중단 후에는 원본 또는
완전한 격리 상태만 남는다. 취소 통지를 받았어도 저장 작업은 확정되었을 수 있다.

격리된 패키지는 재시작 후에도 일반 읽기·수정·재생성·복제·백업·일반 삭제가 거부된다.
영수증/격리 트리가 소실돼도 원래 첨부 선언 때문에 빈 패키지로 읽히지 않는다.
`delete_quarantined`는 현재 영수증 revision과 바이트를 검증한 명시적 삭제만 제공하며,
같은 이름의 새 격리 자료에 오래된 영수증을 적용할 수 없다. 상위 Component/Project 삭제로
이 보호를 우회할 수 없다. 승인·사용량·외부 효과·Checkpoint 기록은 이 API의 대상이 아니다.

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | DataComponent 공개 import |
| [component.py](component.py) | 정의·격리 영수증 owner와 명시적 격리/삭제 API |

공유 storage 구현은 [files.py](../files.py), revision 계약은
[revisions.md](../../../docs/llm/revisions.md)를 참고한다.
