# Infrastructure — 공통 저장·잠금·로그

도메인 서비스와 Component가 공유하는 파일 I/O, 트랜잭션, Linux 프로세스 관리와 관찰 기반입니다. 업무별 실행 정책이나 모델 프롬프트는 이 폴더에서 결정하지 않습니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | 공통 기반 서비스 패키지 설명입니다. |
| [storage.py](storage.py) | atomic JSON, append, 파일 작업과 순차 비동기 StorageIO 경계입니다. |
| [transaction_records.py](transaction_records.py) | undo envelope·operation의 단일 Reader/Writer와 journal 선언입니다. |
| [durability.py](durability.py) | 저장·트랜잭션이 공유하는 directory fsync 구현입니다. |
| [transactions.py](transactions.py) | 여러 파일 변경의 undo journal, fsync, commit·rollback·복구를 구현합니다. |
| [record_transactions.py](record_transactions.py) | 명시적 기록 묶음의 staged 읽기와 durable redo 확정을 담당합니다. |
| [record_journal.py](record_journal.py) | 확정 저널 보유, 파일별 동기화 병합, 완료 지점과 순서에 따른 복구를 담당합니다. |
| [locking.py](locking.py) | Linux 파일 잠금과 workspace 소유권 범위를 관리합니다. |
| [output_records.py](output_records.py) | 출력 event·필수 source manifest·재생성 가능한 index의 Reader/Writer와 typed projection입니다. |
| [journal.py](journal.py) | JSONL 증분 읽기·projection·조회 인덱스입니다. |
| [logging.py](logging.py) | 도메인별 로그 경로와 주입 가능한 로그 출력 계약입니다. |
| [observability.py](observability.py) | 누적 운영 통계와 기존 owner 상태를 모은 조회 스냅샷입니다. |
| [activity.py](activity.py) | Project 단위 activity/event 참조 인덱스입니다. Run/Step 원본을 대체하지 않습니다. |
| [processes.py](processes.py) | Linux process group, parent-death gate와 종료·회수의 공통 처리입니다. |
| [process_state.py](process_state.py) | /proc의 PID·시작 identity·종료 상태를 읽습니다. |
| [process_environment.py](process_environment.py) | 명시 자식 환경의 검증·사본·공개 Schema를 소유합니다. |
| [process_command.py](process_command.py) | 격리 명령의 절대 executable·argv 판별을 공유합니다. |
| [process_result.py](process_result.py) | 명령 종료 코드·출력의 decoding과 결과 직렬화를 담당합니다. |
| [captured_text.py](captured_text.py) | 임시 파일에 프로세스 출력을 수집하고 Unicode 문자 cursor를 유지합니다. |
| [text_stream.py](text_stream.py) | 고정 I/O buffer로 UTF-8을 해독하고 명시한 문자 범위만 반환합니다. |
| [linux_sandbox.py](linux_sandbox.py) | Bubblewrap 인자·보호 경로·mount·명시 환경 배치를 소유합니다. |
| [backups.py](backups.py) | Project 백업 manifest, 무결성 확인, 복원 경계를 담당합니다. |
| [OBSERVABILITY.md](OBSERVABILITY.md) | 누적 통계의 의미, UI 조회와 계측 위치를 설명합니다. |
| [ACTIVITY.md](ACTIVITY.md) | Project activity 목록의 저장·조회·복구 계약을 설명합니다. |

## 저장과 관찰 원칙

변경 가능한 JSON은 원자적으로 교체하고 대화·출력 journal은 append합니다. 여러 파일을 일관되게 변경해야 하는 경우 기존 서비스의 트랜잭션 범위에 참여합니다. worker나 Engine에서 임의 파일 쓰기로 이 경계를 우회하지 않습니다.

StorageIO는 이벤트 루프 밖에서 저장을 수행하고 기존 소유권·취소 정리를 유지합니다. atomic write 하나만으로 모든 외부 효과가 트랜잭션이 되는 것은 아닙니다. Tool의 외부 작업은 별도의 operation receipt 계약을 사용합니다.

`run_records()`는 기록 쓰기를 한 복구 묶음으로 확정합니다. 준비 중에는 공통 record 읽기 함수를
사용하고, raw 파일·트리 변경은 기존 `run()`을 사용합니다. 확정된 묶음의 파일 반영이 실패하면
다음 읽기 전에 복구하며 Tool 실행을 재생하지 않습니다. 파일별 fsync는 일반 undo 변경/소유권 해제
경계에 합치고 그때까지 저널을 보유합니다. 완료 통지는 저널 영속화와 파일 반영 이후이며,
승인·효과 앞의 저장 완료를 생략하지 않습니다. `workspace_locked(records=True)`는 같은 실행기에서
owner가 JSON/JSONL 전용 작업임을 선언하는 내부 계약입니다.

기록 묶음은 유지 중인 저널에 길이·sequence·checksum이 있는 frame으로 추가합니다. 일반적인 후속
확정은 파일 fsync 한 번이며 새 저널 생성 시에는 디렉터리도 동기화합니다. 미완료 마지막 frame과
완성 frame 손상을 구별하고, 전체 저널 검증 후에만 복구합니다. 기록 수·시간에 따른 숨은 flush
설정은 없습니다. 내부 저널 형식 교체 전에는 기존 코드의 정상 종료로 보유 저널을 정리해야 합니다.

Observability는 누적 통계만 소유합니다. active/waiting/queue 상태는 기존 실행 owner에서 읽습니다. Activity는 검색을 위한 파생 인덱스이며 결과의 원본은 Run/Step입니다.

프로세스 worker와 process group 관리는 runtime isolation입니다. filesystem/network sandbox는 명시적 별도 runner 정책이 필요합니다. [저장 트랜잭션](../../../docs/llm/storage-transactions.md)을 참고하세요.

[상위 안내](../README.md)


## 로그·관찰·잠금 선언

LogConfig/DomainLogger가 설정 검증과 필터를, OperationalLogRecord가 허용 payload의
Writer를, FileLogSink/LoggingFormat이 기존 파일 출력 경계를 소유한다. 로그 상태 허용
목록은 기존 Session/Message/Run 선언을 참조하며 자동으로 새 상태를 허용하지 않는다.
주입 sink·handler·Application 회전 정책과 경고·after-commit 순서는 유지한다.

WorkspacePaths는 workspace 잠금 파일·transaction journal·예약 경로를 선언하고 WorkspaceLock은 이를 참조하며 OS 잠금 기술값을 소유한다. WorkspaceOwnership의
참조 수·프로세스 identity·중첩 잠금·트랜잭션/관찰 scope 순서를 바꾸지 않는다.
Transaction의 target 검사와 복구 Reader도 같은 WorkspacePaths 예약 경로 판정을 사용한다.


## 삭제 의도·백업 manifest

DeletionIntent가 기존 삭제 이름·operation·파일 identity의 Writer/Reader를 소유한다.
recover_deletions는 검증된 이름을 소비하고 기존 protected/allowed 검사와 삭제·fsync·
intent 정리 순서를 유지한다. StorageFormat/StorageDrain은 기존 파일 형식·원자 쓰기와
취소 drain의 기술값을 선언하며 새 삭제나 재시도를 만들지 않는다.

BackupManifest가 기존 manifest의 버전 검증·파일 목록 projection과 Writer를 소유한다.
DirectoryBackups는 파일 재검사·staging·게시·실패 정리를 조율하고 ProjectManager는
같은 Owner의 project_root로 archive 내부를 조회한다. 주입된 backups에 새 메서드를
요구하지 않는다. opaque metadata·원본/저장 형식·도메인별 검증 책임은 유지한다.
SnapshotFile.total_bytes가 필요한 바이트 합산을 소유하며 RAG 보관/Checkpoint 계획도
같은 projection을 사용한다. 공통 트랜잭션과 출력 저널의 Owner 구분은 아래 절을 따른다.


## 출력 원본과 파생 인덱스

OutputEventRecord는 기존 delta/output envelope를 읽고 실제 값의 검증은 EngineDelta/
EngineOutput에 위임한다. OutputSource는 필수 manifest의 완전한 필드·버전·바이트/sequence/
digest 증거를 소유한다. OutputJournal은 전체 원본 bytes·마지막 sequence와 newline을
검사한 뒤에만 조회/append한다. 누락 manifest나 손상된 원본은 재생성·절단하지 않는다.

OutputIndex/OutputAnchor/OutputIndexEnvelope는 파생 인덱스의 검증·위치·진행 갱신·
직렬화를 소유한다. 내부 서비스는 속성과 명시 변경 API를 사용한다. 인덱스 손상은 원본 검증
이후에만 재생성할 수 있고, prefix 전체 digest·파일 identity/mtime·sequence 검사를
유지한다. 원본과 인덱스의 버전은 별도 계약이며 같은 숫자라는 이유로 합치지 않는다.
기존 JSONL/manifest/cache 형식·index stride·공개 read/iter_events/append 계약은 유지한다.


## 트랜잭션 기록과 내구성

UndoEnvelope가 현재 버전·operation checksum·정확한 JSON Writer/Reader를 소유하고,
UndoOperation이 kind·target·offset·backup의 완전한 검증과 속성을 소유한다. Manager는
파일 읽기와 복구를 조율하며 고정 딕셔너리 필드를 다시 해석하지 않는다. TransactionJournal은
entry/backup/undo/commit/GC 이름·마커·파일 기술값의 단일 선언이다. 기존 _entry의 raw
입력은 저장 경계이며 별도 호환 Reader를 두지 않는다. 알 수 없는 opaque 필드를 보존한다.

복구는 전체 기록을 검증한 뒤에만 역순으로 적용한다. 실패·프로세스 중단 후 원본 또는 완전한
새 상태를 유지하고, commit marker의 fsync가 불확실하면 게시된 마커를 되돌리지 않는다.
복구 완료 마커·재복구·GC·관찰 callback 격리 및 메모리 객체 rollback 순서는 그대로다.
shared directory fsync는 durability.py 한 곳에 있다. 같은 트랜잭션의 반복 append/replace는
원본 undo를 재사용하고, 변경 파일·디렉터리의 동기화는 commit marker 전에 모아서 수행한다.
후속 undo가 참조하는 중간 파일은 먼저 동기화한다. 트랜잭션 밖의 저장과 승인·효과 전 ACK,
파일 핸들 정리와 각 경로 guard 계약은 유지한다. 상세 순서는 저장 트랜잭션 문서를 따른다.

record batch의 범위는 하나의 완료 응답/실행 허용을 증명하는 기록으로 owner가 명시한다.
Session 접속 복구와 Run 종료도 이 경로를 사용한다. 초기 checkpoint를 첨부한 Engine lifecycle
이벤트는 한 번의 저장 ACK를 공유한다. 핵심 기록의 journal fsync는 유지하며 Activity의
best-effort after-commit 실패를 필수 저장 실패로 바꾸지 않는다.
Run 종료는 `StorageIO.checkpoint()`를 통해 최종 파일을 함께 동기화하고 저널을 회수한 뒤 알린다.
주기나 용량의 숨은 기본값을 도입하지 않고 기존 종료 시점의 정리 경계를 유지한다.
