# 도메인 저장 트랜잭션

`services/infrastructure/transactions.py`의 `TransactionManager`는 각
`WorkspaceOwnership`이 소유한다. 기존 동기 서비스의 `workspace_locked`, Facade의
소유권 범위, 비동기 `StorageIO.run()`이 같은 트랜잭션에 참여한다. 중첩 호출은
새 확정을 만들지 않는다. 잠금 획득 후 읽기 전에 미완료 저널을 복구한다.

## 적용 범위

| 서비스 작업 | 함께 확정하는 원본 |
| --- | --- |
| 요청 제출·취소 | 선택된 Conversation의 메시지 상태·메타데이터; 큐 투입은 확정 후 |
| Run 시작 | Run, 입력 COMMITTED/Run 연결, Assistant STREAMING, Session.current_run_id/status |
| Run 완료·실패·중단·일시정지 | 미완료 Step 상태, Assistant 상태, Run 상태·사용량 확정, Session 해제 |
| 출력 델타 묶음 | outputs.jsonl 추가와 대화 JSONL 추가 |
| 최종 출력·Step 완료 | 출력 저널과 Run 결과/대화 또는 Step 결과 |
| Step 생성·시작·수정·종료 | 이벤트 하나에 대응하는 Step 기록 |
| 체크포인트 초기화 | 체크포인트 원본과 Run.metadata.checkpoints 연결 |
| 체크포인트 갱신 | 노드·Loop 회차·영수증 레코드 |
| 승인 응답·취소·갱신 | 해당 요청의 응답/제어 레코드 |
| 명시적 재개 | 승인 응답 영수증과 새 QUEUED 요청; 원본 Run은 유지 |
| 자동 승인 정책 | 해당 정책 평가의 응답 전체. PAUSED 확정 후 별도 저장 단위 |
| Tool 원장 | 예약·완료·효과 없음 영수증과 소유 Step의 operation_receipt 근거 |
| 외부 결과 확인 | CAS 원장 반영과 원래 Step의 근거 갱신 |
| Project 생성·기본 Project | project.json, Session/Component 초기화, 기본 참조 |
| Project 설정·Component 선택 | 설정·선택과 신규 Component 디렉토리 초기화 |
| Component 영구 제거 | Project의 선택/설정 제거와 컴포넌트 디렉토리 격리 |
| Session 생성·수정·삭제·복원 | Session 레코드 및 디렉토리 수명 |
| Project/Session 복제 | 목적지 메타데이터·Component 자료·선택 저장소의 대화. Run 기록은 복사하지 않음 |
| Project/Session 영구 삭제 | 트리 격리와 호출자가 보유한 객체 상태; 대화 캐시 해제는 확정 후 |
| 백업 복원 | 저널 내부의 준비 사본을 검증한 뒤 목적지 공개 |
| 보관 적용 | 대화 prune, 승인된 Run/Session 제거, 정리 영수증/의도 해제 |
| stale 상태 복구 | 출력 대조·메시지/Step/Run/Session 정리를 한 저장 단위로 처리 |

단일 파일 변경도 같은 계층을 통과한다. 일반적인 조회는 저널 파일을 생성하지 않는다.
기존 삭제/보관 의도 저널의 복구 API는 유지한다. 새 트랜잭션의 확정 전 실패는
부분 변경을 남기지 않고 되돌리므로 같은 의도를 다시 승인 없이 실행하지 않는다.
Run이 시작되기 전 저장 실패로 입력이 QUEUED로 돌아간 경우 Engine은 아직 실행되지 않았다.
이미 시작한 stale Run은 계속 INTERRUPTED로 정리하며 효과를 자동 재생하지 않는다.

## 저장 및 복구 방식

```text
workspace/projects/
  .ish.lock
  .transactions/
    <transaction-id>/
      00000000.json       # 버전·체크섬을 포함한 undo 명령
      00000000.data       # 교체 전 파일 inode의 hard link (해당 작업에만)
      00000001.removed/   # 삭제를 위해 격리한 원본 트리
      stage-.../         # 큰 복원 작업의 비공개 준비 사본
      COMMITTED          # 모든 대상 fsync 이후 확정 마커
      undone-00000000    # 중단된 rollback을 이어가기 위한 진행 표시
    gc-<transaction-id>/  # 확정 또는 rollback 완료 후 물리 정리 대기
```

쓰기 전 undo 정보를 fsync하고 기존 파일을 잠금 안에서 수정한다. 이 때문에
동일 트랜잭션 안에서는 직전 저장값을 다시 읽을 수 있다. 확정 마커가 없으면 역순으로
되돌리고, 마커가 있으면 결과를 보존하고 저널만 정리한다. 복구 자체가 중단되어도
완료한 undo 표시를 확인하여 계속 진행한다. 경로 이탈·심볼릭 링크·손상된 저널은
읽기를 차단하며 임의로 데이터를 추측하거나 저널을 지우지 않는다.

JSONL은 이전 길이만 기록하고 실패 시 truncate한다. 깨진 마지막 줄을 수리할 때는
제거할 꼬리만 보관한다. 스트리밍 중 전체 대화를 복사하거나 재작성하지 않는다.
명시적 보관 prune의 원자 교체는 기존 파일 hard link로 보호한다. 삭제는 트리를
저널로 rename한 뒤 확정하고 물리 삭제는 재시도 가능한 GC로 수행한다. GC 실패는
이미 완료된 삭제를 취소하지 않는다. 캐시와 도메인 객체는 rollback 때 복원/무효화한다.
운영 로그와 대화 캐시 해제는 commit 이후 실행하며 로그 실패로 저장을 되돌리지 않는다.

저널과 확정 마커 때문에 fsync 비용은 증가한다. 기존 `policies.output`의
`batch_size`, `max_delay`, `max_chars`로 델타 묶음을 조절할 수 있다. 승인·Tool·Step·
체크포인트 이벤트의 저장 완료 경계는 묶음 설정으로 생략하지 않는다.

## 도메인·외부 효과 경계

Engine은 기존처럼 이벤트만 내보낸다. TransactionManager가 LLM/Tool을 실행하거나
Project/Session/Run/Step의 상태 전이 규칙을 결정하지 않는다. 서비스가 묶을 범위를
정하고 공통 저장 함수가 참여한다. 다른 workspace를 중첩해서 확정하는 기능은 없다.

Tool 실행 전의 원장 예약과 실행 후의 결과 저장은 별개 트랜잭션이다. 외부 효과와
로컬 파일을 하나의 원자 작업으로 만들 수는 없다. 결과/Step 저장이 실패하면 원장은
started로 남아 불확실한 효과를 보호한다. 명시적 외부 확인 후에만 재사용한다.
None 반환도 정상 완료 결과다. Step의 최종 상태와 이후 체크포인트는 각각의 Engine
이벤트가 기록한다. 나중 이벤트를 기다리며 트랜잭션을 열어 두지 않는다.

자동 승인 정책은 PAUSED 확정 뒤 별도로 실행한다. 정책 응답 저장 실패는 응답 묶음을
되돌리고 PAUSED는 유지한다. 정책 응답을 저장하는 것만으로 재개하지 않는다.
UI 응답/취소/갱신의 INTERACTION_CHANGED 알림 예약도 after_commit에 참여한다.
확정 실패로 rollback하면 알림을 보내지 않고, 저장 transaction 문맥을 UI에 넘기지 않는다.
알림 전달은 관찰 기능이며 실제 상태는 승인 조회 API와 Run 기록이 원본이다.

RAG는 계속 자체 세대·DB 구조를 소유한다. 모델 준비는 잠금 밖에서 진행하고 공개
단계만 참여한다. 새 세대, 활성 포인터 및 정리 작업의 파일 연동을 보호한다.
Memory 병합도 공통 JSON 쓰기에 참여하므로 서비스 호출 실패는 변경 전 기록으로
돌아간다. 프로젝트 밖 백업 생성·변환은 기존 검증된 준비 디렉토리의 원자 공개를
유지한다. workspace와 외부 백업 경로를 묶는 분산 트랜잭션은 제공하지 않는다.

## 확장 저장소와 사용 조건

기본 파일 저장의 보장은 같은 로컬 Linux 파일시스템의 atomic rename/hard link,
fsync 및 협력하는 workspace 잠금을 전제로 한다. 파일을 직접 열어 읽는 외부 도구는
저장 중간 상태를 볼 수 있으므로 UI는 Facade를 사용한다. 저수준 Repository를 직접
호출할 때는 `with projects.ownership.scope():`로 논리 작업 전체를 감싼다.

확장 파일 저장소는 `atomic_json`, `append_bytes`, `make_directory`, `unlink_file`,
`remove_named_tree` 또는 현재 Transaction의 참여 메서드를 사용한다. 새 디렉토리의
사용자 초기화는 선언한 루트 아래에서 해야 한다. 기존 디렉토리를 수정하는 확장은
공통 파일 함수를 사용해야 한다. 직접 `Path.write_text`, 외부 DB/네트워크 호출은
자동으로 원자화되지 않는다. 사용자 저장소를 기본 구현으로 교체하지 않는다.

`current_transaction().on_rollback(key, callback)`은 확장 메모리 투영을 복원하고
`after_commit(callback)`은 확정 후 관찰을 연결한다. 이 훅 자체는 외부 저장소의
crash recovery를 보장하지 않는다. 기본 MemoryConversation은 메시지 단위 rollback을
지원하지만 프로세스가 종료되면 사라지는 기존 수명은 바뀌지 않는다.

commit 마커 rename 직후 fsync가 실패한 경우 확정 결과가 불명확한 오류가 전달될 수 있다.
다음 잠금 진입의 복구와 저장된 요청/Run을 조회한 뒤 판단한다. 같은 작업을 무조건
다시 제출하지 않는다. 전원 차단·손상된 디스크·네트워크 파일시스템의 보장까지
프로세스 강제 종료 테스트로 입증한 것은 아니다.
