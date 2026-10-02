# Infrastructure — 공통 저장·잠금·로그

도메인 서비스와 Component가 공유하는 파일 I/O, 트랜잭션, Linux 프로세스 관리와 관찰 기반입니다. 업무별 실행 정책이나 모델 프롬프트는 이 폴더에서 결정하지 않습니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | 공통 기반 서비스 패키지 설명입니다. |
| [storage.py](storage.py) | atomic JSON, append, 파일 작업과 순차 비동기 StorageIO 경계입니다. |
| [transactions.py](transactions.py) | 여러 파일 변경의 undo journal, fsync, commit·rollback·복구를 구현합니다. |
| [locking.py](locking.py) | Linux 파일 잠금과 workspace 소유권 범위를 관리합니다. |
| [journal.py](journal.py) | JSONL 증분 읽기·projection·조회 인덱스입니다. |
| [logging.py](logging.py) | 도메인별 로그 경로와 주입 가능한 로그 출력 계약입니다. |
| [observability.py](observability.py) | 누적 운영 통계와 기존 owner 상태를 모은 조회 스냅샷입니다. |
| [activity.py](activity.py) | Project 단위 activity/event 참조 인덱스입니다. Run/Step 원본을 대체하지 않습니다. |
| [processes.py](processes.py) | Linux process group, parent-death gate와 종료·회수의 공통 처리입니다. |
| [backups.py](backups.py) | Project 백업 manifest, 무결성 확인, 복원 경계를 담당합니다. |
| [OBSERVABILITY.md](OBSERVABILITY.md) | 누적 통계의 의미, UI 조회와 계측 위치를 설명합니다. |
| [ACTIVITY.md](ACTIVITY.md) | Project activity 목록의 저장·조회·복구 계약을 설명합니다. |

## 저장과 관찰 원칙

변경 가능한 JSON은 원자적으로 교체하고 대화·출력 journal은 append합니다. 여러 파일을 일관되게 변경해야 하는 경우 기존 서비스의 트랜잭션 범위에 참여합니다. worker나 Engine에서 임의 파일 쓰기로 이 경계를 우회하지 않습니다.

StorageIO는 이벤트 루프 밖에서 저장을 수행하고 기존 소유권·취소 정리를 유지합니다. atomic write 하나만으로 모든 외부 효과가 트랜잭션이 되는 것은 아닙니다. Tool의 외부 작업은 별도의 operation receipt 계약을 사용합니다.

Observability는 누적 통계만 소유합니다. active/waiting/queue 상태는 기존 실행 owner에서 읽습니다. Activity는 검색을 위한 파생 인덱스이며 결과의 원본은 Run/Step입니다.

프로세스 worker와 process group 관리는 runtime isolation입니다. filesystem/network sandbox는 명시적 별도 runner 정책이 필요합니다. [저장 트랜잭션](../../../docs/llm/storage-transactions.md)을 참고하세요.

[상위 안내](../README.md)
