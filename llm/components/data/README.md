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

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | DataComponent 공개 import |
| [component.py](component.py) | 닫힌 정의 schema, data 디렉터리와 capability 선언 |

공유 storage 구현은 [files.py](../files.py), revision 계약은
[revisions.md](../../../docs/llm/revisions.md)를 참고한다.
