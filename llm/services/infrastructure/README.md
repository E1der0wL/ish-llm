# Infrastructure — 공통 저장·잠금·로그

도메인 서비스와 Component가 공유하는 파일 I/O, 트랜잭션, Linux 프로세스 관리와 관찰 기반입니다. 업무별 실행 정책이나 모델 프롬프트는 이 폴더에서 결정하지 않습니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | 공통 기반 서비스 패키지 설명입니다. |
| [storage.py](storage.py) | atomic JSON, append, 파일 작업과 순차 비동기 StorageIO 경계입니다. |
| [transactions.py](transactions.py) | 여러 파일 변경의 undo journal, fsync, commit·rollback·복구를 구현합니다. |
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
| [linux_sandbox.py](linux_sandbox.py) | Bubblewrap 인자·보호 경로·mount·명시 환경 배치를 소유합니다. |
| [backups.py](backups.py) | Project 백업 manifest, 무결성 확인, 복원 경계를 담당합니다. |
| [OBSERVABILITY.md](OBSERVABILITY.md) | 누적 통계의 의미, UI 조회와 계측 위치를 설명합니다. |
| [ACTIVITY.md](ACTIVITY.md) | Project activity 목록의 저장·조회·복구 계약을 설명합니다. |

## 저장과 관찰 원칙

변경 가능한 JSON은 원자적으로 교체하고 대화·출력 journal은 append합니다. 여러 파일을 일관되게 변경해야 하는 경우 기존 서비스의 트랜잭션 범위에 참여합니다. worker나 Engine에서 임의 파일 쓰기로 이 경계를 우회하지 않습니다.

StorageIO는 이벤트 루프 밖에서 저장을 수행하고 기존 소유권·취소 정리를 유지합니다. atomic write 하나만으로 모든 외부 효과가 트랜잭션이 되는 것은 아닙니다. Tool의 외부 작업은 별도의 operation receipt 계약을 사용합니다.

Observability는 누적 통계만 소유합니다. active/waiting/queue 상태는 기존 실행 owner에서 읽습니다. Activity는 검색을 위한 파생 인덱스이며 결과의 원본은 Run/Step입니다.

프로세스 worker와 process group 관리는 runtime isolation입니다. filesystem/network sandbox는 명시적 별도 runner 정책이 필요합니다. [저장 트랜잭션](../../../docs/llm/storage-transactions.md)을 참고하세요.

[상위 안내](../README.md)


## 로그·관찰·잠금 선언

LogConfig/DomainLogger가 설정 검증과 필터를, OperationalLogRecord가 허용 payload의
Writer를, FileLogSink/LoggingFormat이 기존 파일 출력 경계를 소유한다. 로그 상태 허용
목록은 기존 Session/Message/Run 선언을 참조하며 자동으로 새 상태를 허용하지 않는다.
주입 sink·handler·Application 회전 정책과 경고·after-commit 순서는 유지한다.

WorkspaceLock은 기존 영구 잠금 파일과 OS 잠금 기술값을 선언한다. WorkspaceOwnership의
참조 수·프로세스 identity·중첩 잠금·트랜잭션/관찰 scope 순서를 바꾸지 않는다.
Transaction의 예약 경로에 남은 동일 잠금 파일명은 저장 계층 후속 연결 대상으로 추적하며
현재 이 문서의 검토가 저장·트랜잭션 파일 전체 완료를 뜻하지 않는다.


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
같은 projection을 사용한다. 공통 트랜잭션과 출력 저널의 전수 정리는 후속 범위다.


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
